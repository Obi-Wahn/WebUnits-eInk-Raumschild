"""
==============================================================================
Web-Ebene: Flask-Oberflaeche zur Administration
==============================================================================
Anmeldung, CSRF-Schutz und die Bedienseite des Tuerschilds. Der Server lauscht
standardmaessig nur auf dem Localhost; der Zugriff von aussen laeuft ueber einen
Reverse Proxy (siehe Installationsanleitung).
"""
import datetime
import io
import ipaddress
import logging
import secrets
import subprocess
import threading
import socket
import time
from functools import wraps
from typing import Optional

from flask import Flask, abort, redirect, render_template, request, Response
from werkzeug.security import check_password_hash, generate_password_hash

from .anzeige import sichtbare_raumzeichen, zeichne_anzeige
from .konfiguration import (formatiere_dauer, get_cached_config, get_now,
                            get_update_interval, pruefe_raumname, save_config)
from .konstanten import (DEFAULT_UPDATE_SECONDS, FAILED_LOGIN_MAX,
                         FAILED_LOGIN_TTL, LOGIN_LOCKOUT_SECONDS,
                         MAX_LOGIN_ATTEMPTS, MAX_UPDATE_SECONDS,
                         MIN_UPDATE_SECONDS, ROOM_NAME_MAX_LEN,
                         STALE_ALERT_SECONDS, TRUSTED_PROXIES,
                         UI_REFRESH_SECONDS)
from .steuerung import run_display_test_sequence
from .zustand import app_state

app = Flask(__name__)

def check_auth(username, password) -> bool:
    """
    Überprüft die HTTP Basic Auth Zugangsdaten.
    
    TECHNISCHER HINTERGRUND (Kryptographie):
    Passwörter sollten niemals im Klartext gespeichert werden!
    Wir nutzen 'werkzeug.security', um das Klartext-Passwort aus der config.json
    beim ersten Start einmalig in einen Einweg-Hash umzuwandeln (Auto-Migration).
    Selbst wenn Hacker die SD-Karte stehlen, sehen sie nur den Hash.

    TECHNISCHER HINTERGRUND (Timing-Angriffe):
    Ein gewöhnlicher Textvergleich mit '==' bricht beim ersten abweichenden
    Zeichen ab. Ein falscher Name ist dadurch je nach Eingabe minimal
    unterschiedlich schnell abgelehnt - über sehr viele Messungen lässt sich
    daraus der hinterlegte Benutzername Zeichen für Zeichen erraten.
    secrets.compare_digest() vergleicht stattdessen immer über die volle Länge
    und benötigt so stets gleich lange.
    Aus demselben Grund prüfen wir Name UND Passwort immer beide, statt die
    Passwortprüfung bei falschem Namen zu überspringen: Sonst wäre an der
    Antwortzeit ablesbar, ob der Benutzername existiert.
    """
    conf = get_cached_config()
    u = conf.get('ADMIN_USER', 'admin')
    saved_pass = conf.get('ADMIN_PASS', 'tuerschild')

    if not saved_pass.startswith('scrypt:') and not saved_pass.startswith('pbkdf2:'):
        logging.info("Klartext-Passwort entdeckt. Wird verschlüsselt und gespeichert...")
        hashed_pass = generate_password_hash(saved_pass)
        conf['ADMIN_PASS'] = hashed_pass
        save_config(conf)
        saved_pass = hashed_pass

    # Fehlende Angaben zu "" machen: compare_digest und check_password_hash
    # erwarten Text und wuerfen bei None eine Ausnahme.
    user_ok = secrets.compare_digest(str(username or ''), str(u))
    pass_ok = check_password_hash(saved_pass, str(password or ''))
    return user_ok and pass_ok

def authenticate():
    """Gibt den HTTP 401 Fehler (Unauthorized) an den Browser zurück, der daraufhin nach Passwörtern fragt."""
    return Response(
    'Zugriff verweigert. Bitte korrekte Zugangsdaten eingeben.\n', 401,
    {'WWW-Authenticate': 'Basic realm="Tuerschild Admin-Bereich"'})

def get_client_ip() -> str:
    """
    Ermittelt die Adresse des Rechners, von dem eine Anfrage stammt.

    TECHNISCHER HINTERGRUND (Warum nicht einfach request.remote_addr?):
    Waitress lauscht nur auf dem Localhost, davor sitzt Nginx. Aus Sicht von
    Flask kommt deshalb JEDE Anfrage von 127.0.0.1 - egal, wer sie gestellt
    hat. Der Rate-Limiter zaehlte dadurch alle Zugriffe in einen gemeinsamen
    Topf: Fuenf Fehlversuche eines Fremden haetten die Lehrkraft im Nachbarraum
    mit ausgesperrt, und eine gezielte Begrenzung pro Angreifer fand gar nicht
    statt.

    Nginx vermerkt die echte Adresse in der Kopfzeile X-Real-IP.

    SICHERHEITSHINWEIS: Solche Kopfzeilen darf man nur auswerten, wenn die
    Anfrage tatsaechlich vom eigenen Proxy kommt. Andernfalls koennte jeder
    Angreifer sich durch eine selbst gesetzte Kopfzeile bei jedem Versuch eine
    neue Adresse geben und die Sperre damit vollstaendig umgehen. Deshalb die
    Pruefung gegen TRUSTED_PROXIES.
    """
    direkt = request.remote_addr or "unbekannt"
    if direkt not in TRUSTED_PROXIES:
        return direkt

    kandidat = request.headers.get("X-Real-IP", "").strip()
    if not kandidat:
        # X-Forwarded-For ist eine Kette. Der Proxy haengt die von IHM gesehene
        # Adresse hinten an; die vorderen Eintraege stammen moeglicherweise vom
        # Aufrufer selbst und sind damit faelschbar. Wir nehmen den letzten.
        kette = request.headers.get("X-Forwarded-For", "")
        if kette:
            kandidat = kette.split(",")[-1].strip()

    if not kandidat:
        return direkt

    # Nur eine echte IP-Adresse akzeptieren. Beliebiger Text als Schluessel
    # waere ein bequemer Weg, den Speicher vollzuschreiben.
    try:
        ipaddress.ip_address(kandidat)
    except ValueError:
        logging.warning(f"Unbrauchbare Adresse in der Proxy-Kopfzeile verworfen: {kandidat[:40]!r}")
        return direkt
    return kandidat

def cleanup_failed_logins(jetzt: float) -> None:
    """
    Entfernt veraltete Eintraege aus der Fehlversuchs-Liste.

    Ohne diese Bereinigung waechst die Liste unbegrenzt: Jede Adresse, die
    jemals einen Fehlversuch hatte, bliebe fuer immer im Speicher. Auf einem
    Geraet mit 512 MB ist das kein theoretisches Problem.

    ACHTUNG: Muss unter app_state.state_lock aufgerufen werden.
    """
    veraltet = [ip for ip, daten in app_state.failed_logins.items()
                if jetzt - daten.get('last_seen', 0) > FAILED_LOGIN_TTL]
    for ip in veraltet:
        del app_state.failed_logins[ip]

    # Notbremse, falls sehr viele Adressen in kurzer Zeit auftauchen:
    # die am laengsten unbenutzten zuerst verwerfen.
    ueberzaehlig = len(app_state.failed_logins) - FAILED_LOGIN_MAX
    if ueberzaehlig > 0:
        nach_alter = sorted(app_state.failed_logins.items(),
                            key=lambda eintrag: eintrag[1].get('last_seen', 0))
        for ip, _ in nach_alter[:ueberzaehlig]:
            del app_state.failed_logins[ip]

def requires_auth(f):
    """
    Decorator (@requires_auth) für alle geschützten Flask-Routen.

    TECHNISCHER HINTERGRUND (Sicherheit & Rate Limiting):
    Hier wird nicht nur das Passwort geprüft, sondern auch ein Rate Limiter im
    Arbeitsspeicher geführt. Er sperrt eine Adresse für eine Minute, sobald
    fünf Fehlversuche registriert wurden. Das schützt den Raspberry Pi vor
    automatisiertem Passwortraten.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = get_client_ip()
        now = time.time()

        # 1. Rate Limiting Check (Wurde diese Adresse bereits blockiert?)
        with app_state.state_lock:
            cleanup_failed_logins(now)
            attempt_data = app_state.failed_logins.get(ip, {'count': 0, 'lockout_until': 0})
            if now < attempt_data['lockout_until']:
                wait_time = int(attempt_data['lockout_until'] - now)
                abort(429, description=f"Zu viele Fehlversuche. Bitte warten Sie {wait_time} Sekunden.")

        # 2. Login-Überprüfung
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            # Fehlversuch protokollieren und Zähler erhöhen
            with app_state.state_lock:
                attempt_data['count'] += 1
                attempt_data['last_seen'] = now
                if attempt_data['count'] >= MAX_LOGIN_ATTEMPTS:
                    logging.warning(f"Brute-Force Schutz: {ip} temporär gesperrt.")
                    attempt_data['lockout_until'] = now + LOGIN_LOCKOUT_SECONDS
                    attempt_data['count'] = 0  # Reset des Zählers nach Sperre
                app_state.failed_logins[ip] = attempt_data
            return authenticate()

        # 3. Erfolgreicher Login -> Zähler für diese Adresse bereinigen
        with app_state.state_lock:
            if ip in app_state.failed_logins:
                del app_state.failed_logins[ip]

        return f(*args, **kwargs)
    return decorated

def verify_csrf(f):
    """
    Decorator (@verify_csrf) gegen Cross-Site Request Forgery (CSRF).
    Prüft bei allen POST-Anfragen, ob das Webinterface den korrekten, 
    kryptographischen Token (self.csrf_token) mitgesendet hat. 
    Verhindert, dass fremde Skripte von außen ungewollt Systembefehle ausführen.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.form.get('csrf_token')
        # Zeitkonstanter Vergleich, aus demselben Grund wie beim Benutzernamen:
        # '!=' bricht beim ersten abweichenden Zeichen ab und verraet ueber die
        # Antwortzeit, wie viele Zeichen bereits stimmen.
        if not token or not secrets.compare_digest(str(token), app_state.csrf_token):
            abort(403, description="Ungültiger CSRF Token. Bitte Seite neu laden.")
        return f(*args, **kwargs)
    return decorated

# ------------------------------------------------------------------------------
# DIE HTML-VORLAGE
# ------------------------------------------------------------------------------
# Sie liegt in tuerschild/templates/dashboard.html - dort, wo Flask sie von
# selbst findet (das Vorlagenverzeichnis richtet sich nach dem Paket, in dem
# Flask() aufgerufen wurde).
#
# Frueher standen die 232 Zeilen HTML als Zeichenkette mitten in dieser Datei.
# Der Grund war die Installation per Copy&Paste einer einzigen Datei - seit der
# Aufteilung in ein Paket gilt das nicht mehr. Als eigene Datei bekommt die
# Vorlage im Editor wieder Syntaxhervorhebung und Einrueckungshilfe, und diese
# Datei enthaelt nur noch Programmcode.
#
# Das Layout nutzt CSS Grid und einen Mobile-First-Ansatz (Flexbox column),
# damit es sich auf Smartphones und Desktop-PCs automatisch anordnet.
DASHBOARD_VORLAGE = "dashboard.html"
# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# FLASK ROUTEN (Endpunkte für das Web-Interface)
# ------------------------------------------------------------------------------
@app.route('/')
@requires_auth
def index():
    """Rendert die Hauptseite des Admin-Interfaces mit Jinja2-Templating."""
    conf = get_cached_config()
    
    # Thread-sicherer Lesevorgang aus dem globalen State.
    # Die Rueckmeldung des Speicherformulars wird dabei GELESEN UND GELEERT:
    # Sie gehoert zu genau einem Speichervorgang und darf nicht bei jedem
    # spaeteren Seitenaufruf erneut erscheinen.
    with app_state.state_lock:
        is_simulated = app_state.simulated_datetime is not None
        d_data = app_state.current_display_data
        d_msg_raw = app_state.current_display_msg
        c_token = app_state.csrf_token
        is_stale = app_state.data_is_stale
        sync_dt = app_state.last_successful_sync
        stoerung_seit = app_state.stoerung_seit

        save_error = app_state.save_error
        save_ok = app_state.save_ok
        app_state.save_error = None
        app_state.save_ok = False

    # Zeitstempel des letzten geglückten API-Abrufs (nur relevant, wenn wir
    # gerade aus der Offline-Rücklage anzeigen).
    last_sync = sync_dt.strftime("%d.%m.%Y %H:%M") if sync_dt else None

    # Dauer einer laufenden Störung. Ab STALE_ALERT_SECONDS wird der Hinweis
    # auf der Seite von gelb auf rot gestellt - derselbe Schwellwert, ab dem
    # auch das Protokoll eine Meldung bekommt (siehe steuerung.py).
    stoerung_dauer = None
    stoerung_lang = False
    if stoerung_seit:
        sekunden = time.time() - stoerung_seit
        stoerung_dauer = formatiere_dauer(sekunden)
        stoerung_lang = sekunden >= STALE_ALERT_SECONDS


    display_time = get_now().strftime("%d.%m.%Y %H:%M:%S")

    return render_template(
        DASHBOARD_VORLAGE,
        conf=conf, 
        data=d_data, 
        beschreibung=vorschau_beschreibung(d_data, d_msg_raw),
        now=display_time,
        sim_active=is_simulated,
        csrf_token=c_token,
        stale=is_stale,
        last_sync=last_sync,
        stoerung_dauer=stoerung_dauer,
        stoerung_lang=stoerung_lang,
        save_error=save_error,
        save_ok=save_ok,
        # Kein Selbstneuladen, solange eine Rückmeldung zum Speichern auf der
        # Seite steht: Sie würde weggezogen, bevor sie gelesen ist - und wer
        # gerade gespeichert hat, sitzt oft noch am Formular.
        refresh_sekunden=None if (save_ok or save_error) else UI_REFRESH_SECONDS,
        room_max=ROOM_NAME_MAX_LEN,
        # Die Spanne wird aus der echten Kopfzeilen-Geometrie gerechnet, nicht
        # im Formular eingetragen: eine feste Zahl im Text waere still falsch
        # geworden, sobald sich Schrift oder Kopfzeile aendern.
        room_sichtbar=sichtbare_raumzeichen(),
        # Das TATSAECHLICH benutzte Intervall, nicht der rohe Wert aus der
        # Datei: get_update_interval() begrenzt ihn. Wer von Hand 30 einträgt,
        # sieht im Formular 30 - gearbeitet wird aber mit 300.
        update_interval=get_update_interval(conf),
        min_interval=MIN_UPDATE_SECONDS,
        max_interval=MAX_UPDATE_SECONDS
    )

def vorschau_beschreibung(data, meldung: str) -> str:
    """
    Beschreibt in Worten, was auf dem Display steht.

    Die Vorschau ist ein Bild - fuer eine Vorlesesoftware also stumm, und auch
    mit den Augen ist ein 250x122-Bitmap muehsam zu lesen. Diese Beschreibung
    steht im alt-Attribut und macht den Inhalt wieder zu Text.

    Der Text stammt teils aus WebUntis. Er wird hier NICHT zu HTML
    zusammengesetzt, sondern als reine Zeichenkette zurueckgegeben; Jinja
    maskiert ihn beim Einsetzen ins Attribut. Fruehere Fassungen dieser Seite
    bauten aus der Meldung HTML mit <br> - das musste eigens gegen
    eingeschleusten Code abgesichert werden.
    """
    def stunde(lesson):
        teile = [lesson.stunde, lesson.zeit,
                 lesson.fach_lang or lesson.fach, lesson.klasse]
        if lesson.lehrer:
            teile.append(lesson.lehrer)
        # Dieselbe Rangfolge wie im Etikettenkasten des Displays. Die
        # Beschreibung soll das Bild wiedergeben, nicht eine zweite Meinung
        # dazu haben.
        if lesson.status_code == "cancelled":
            teile.append("fällt aus")
        elif lesson.pruefung:
            teile.append(lesson.pruefung.capitalize())
        elif lesson.status_code == "irregular":
            teile.append("Vertretung")
        return ", ".join(t for t in teile if t)

    if isinstance(data, dict) and (data.get("current") or data.get("next")):
        zeilen = []
        zeilen.append("Jetzt: " + stunde(data["current"]) if data.get("current")
                      else "Jetzt: " + (meldung or "kein Unterricht"))
        zeilen.append("Danach: " + stunde(data["next"]) if data.get("next")
                      else "Danach: kein Unterricht mehr heute")
        return " | ".join(zeilen)

    return (meldung or "").replace("\n", " ") or "keine Anzeige"

@app.route('/vorschau.png')
@requires_auth
def vorschau():
    """
    Liefert das Bild, das gerade auf dem E-Paper steht - als PNG.

    KEINE NACHBILDUNG, SONDERN DASSELBE BILD:
    Gezeichnet wird mit zeichne_anzeige(), also mit genau der Funktion, die
    auch das Display bemalt. Frueher stand hier eine Nachbildung in HTML.
    Die sah aehnlich aus, war aber eine zweite Fassung desselben Layouts - und
    sie zeigte vor allem nicht, was auf 250x122 Pixeln wirklich Platz hat.
    Gerade die Kuerzungen langer Fachnamen sah man dort nie.

    Die Hardware wird dabei nicht angefasst: Es wird nur gezeichnet, nichts
    gesendet. Der Aufruf ist also auch bei arbeitendem Tuerschild harmlos.
    """
    conf = get_cached_config()
    with app_state.state_lock:
        data = app_state.current_display_data
        meldung = app_state.current_display_msg
        stale = app_state.data_is_stale

    bild = zeichne_anzeige(data, meldung, conf, stale=stale)

    puffer = io.BytesIO()
    bild.save(puffer, format="PNG")

    antwort = Response(puffer.getvalue(), mimetype="image/png")
    # Ohne das zeigte der Browser nach einem Update weiter das alte Bild.
    antwort.headers["Cache-Control"] = "no-store, must-revalidate"
    return antwort

@app.route('/simulate_time', methods=['POST'])
@requires_auth
@verify_csrf
def simulate_time():
    sim_time_str = request.form.get('SIM_TIME')
    if sim_time_str:
        try:
            parsed_time = datetime.datetime.strptime(sim_time_str, "%Y-%m-%dT%H:%M")
            with app_state.state_lock:
                app_state.simulated_datetime = parsed_time
                app_state.simulation_started_at = time.time()
                app_state.force_update_flag = True
        except Exception as e:
            logging.error(f"Fehler beim Parsen der Simulationszeit: {e}")
    return redirect('/')

@app.route('/reset_time', methods=['POST'])
@requires_auth
@verify_csrf
def reset_time():
    with app_state.state_lock:
        app_state.simulated_datetime = None
        app_state.simulation_started_at = None
        app_state.force_update_flag = True
    return redirect('/')

@app.route('/save', methods=['POST'])
@requires_auth
@verify_csrf
def save():
    """
    Uebernimmt Raumname und Abrufintervall aus dem Formular.

    Der Raumname wurde bisher ungeprueft uebernommen. Ein leeres Feld fuehrte
    dazu, dass das Schild "Raum None fehlt." anzeigte - der Name geht als
    Suchbegriff an WebUntis -, und die Ursache stand nirgends.

    ALLES ODER NICHTS: Wird eine Angabe abgelehnt, wird gar nichts gespeichert.
    Ein halb uebernommenes Formular waere schlimmer als ein abgelehntes -
    niemand wuesste, welcher Stand nun in der Datei steht.
    """
    conf = get_cached_config()
    if not conf:
        return redirect('/')

    raumname, fehler = pruefe_raumname(request.form.get('ROOM_NAME'))
    if fehler:
        logging.warning(f"Speichern abgelehnt: {fehler}")
        with app_state.state_lock:
            app_state.save_error = fehler
        return redirect('/')

    conf['ROOM_NAME'] = raumname
    try:
        val = int(request.form.get('AUTO_UPDATE_SECONDS', DEFAULT_UPDATE_SECONDS))
        conf['AUTO_UPDATE_SECONDS'] = max(MIN_UPDATE_SECONDS, min(val, MAX_UPDATE_SECONDS))
    except Exception:
        pass

    save_config(conf)
    with app_state.state_lock:
        app_state.save_ok = True
        app_state.force_update_flag = True
    return redirect('/')

@app.route('/update', methods=['POST'])
@requires_auth
@verify_csrf
def trigger_update():
    with app_state.state_lock:
        app_state.force_update_flag = True
    return redirect('/')

@app.route('/demo', methods=['POST'])
@requires_auth
@verify_csrf
def trigger_demo():
    with app_state.state_lock:
        app_state.show_demo_once = True
        app_state.force_update_flag = True
    return redirect('/')

@app.route('/test_all', methods=['POST'])
@requires_auth
@verify_csrf
def trigger_test_all():
    with app_state.state_lock:
        is_testing = app_state.test_mode_active
        
    if not is_testing:
        threading.Thread(target=run_display_test_sequence, daemon=True).start()
    return redirect('/')

@app.route('/toggle', methods=['POST'])
@requires_auth
@verify_csrf
def toggle_display():
    conf = get_cached_config()
    if conf:
        conf['DISPLAY_ACTIVE'] = not conf.get('DISPLAY_ACTIVE', True)
        save_config(conf)
        with app_state.state_lock:
            app_state.force_update_flag = True
    return redirect('/')

@app.route('/toggle_touch', methods=['POST'])
@requires_auth
@verify_csrf
def toggle_touch():
    conf = get_cached_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        with app_state.state_lock:
            app_state.force_update_flag = True
    return redirect('/')

@app.route('/sys_reboot', methods=['POST'])
@requires_auth
@verify_csrf
def sys_reboot():
    """
    Startet das System über den Linux-Befehl 'reboot' neu.
    
    TECHNISCHER HINTERGRUND (PoLP & Fire and Forget):
    1. PoLP (Prinzip der geringsten Privilegien): Der Nutzer 'pi' hat über die 
       /etc/sudoers eine Ausnahmegenehmigung erhalten, diesen EINEN Befehl ohne 
       Passwortabfrage auszuführen.
    2. Fire & Forget: Popen() startet den Befehl als losgelösten Unterprozess. 
       Das ermöglicht es Flask, sofort eine HTTP 200 Erfolgsmeldung an den Browser 
       zurückzusenden, BEVOR das System tatsächlich neustartet und blockiert.
    """
    logging.info("Web-Kommando empfangen: System wird neu gestartet.")
    app_state.shutdown_event.set() 
    
    def delayed_reboot():
        time.sleep(2.5)
        subprocess.Popen(["/usr/bin/sudo", "-n", "/sbin/reboot"])
        
    threading.Thread(target=delayed_reboot, daemon=True).start()
    return "System startet neu. Bitte haben Sie einen Moment Geduld...", 200

@app.route('/sys_shutdown', methods=['POST'])
@requires_auth
@verify_csrf
def sys_shutdown():
    """Fährt das System sicher herunter (Shutdown)."""
    logging.info("Web-Kommando empfangen: System fährt herunter.")
    app_state.shutdown_event.set() 
    
    def delayed_shutdown():
        time.sleep(2.5)
        subprocess.Popen(["/usr/bin/sudo", "-n", "/sbin/poweroff"])
        
    threading.Thread(target=delayed_shutdown, daemon=True).start()
    return "System fährt herunter. Sie können den Strom in ca. 10 Sekunden sicher trennen.", 200


# ==============================================================================
# NETZWERKADRESSE FUER DIE STARTAUSGABE
# ==============================================================================
def get_local_ip() -> Optional[str]:
    """
    Ermittelt die Adresse, unter der der Raspberry Pi im lokalen Netz
    erreichbar ist (z. B. 192.168.1.42).

    TECHNISCHER HINTERGRUND:
    Ein Rechner besitzt meist mehrere Adressen (WLAN, LAN, Loopback). Welche
    davon die "richtige" ist, weiß nur die Routing-Tabelle des Systems. Wir
    fragen sie ab, indem wir einen UDP-Socket auf eine Adresse außerhalb des
    eigenen Netzes "verbinden" und anschließend nachsehen, welche eigene
    Adresse das Betriebssystem dafür gewählt hätte.
    Bei UDP entsteht dabei kein Netzwerkverkehr - es werden keinerlei Pakete
    verschickt. Die Abfrage funktioniert daher auch ohne Internetzugang.

    Rückgabe: die IP als Text, oder None wenn keine Netzwerkverbindung besteht.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        # 192.0.2.0/24 ist laut RFC 5737 fest für Dokumentationszwecke
        # reserviert und damit garantiert nirgends real erreichbar. Für die
        # reine Routen-Abfrage genügt das - im Gegensatz zu einer fremden
        # echten Adresse fragen wir so keinen fremden Server an.
        sock.connect(("192.0.2.1", 80))
        ip = sock.getsockname()[0]
        # Ohne Netzwerkverbindung liefert das System die Loopback-Adresse
        # zurück, die dem Nutzer hier nicht weiterhilft.
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        if sock:
            sock.close()
