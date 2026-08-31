"""
==============================================================================
Web-Ebene: Flask-Oberflaeche zur Administration
==============================================================================
Anmeldung, CSRF-Schutz und die Bedienseite des Tuerschilds. Der Server lauscht
standardmaessig nur auf dem Localhost; der Zugriff von aussen laeuft ueber einen
Reverse Proxy (siehe Installationsanleitung).
"""
import datetime
import ipaddress
import logging
import secrets
import subprocess
import threading
import socket
import time
from functools import wraps
from typing import Optional

from flask import Flask, abort, redirect, render_template_string, request, Response
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

from .konfiguration import get_cached_config, get_now, save_config
from .konstanten import (DEFAULT_UPDATE_SECONDS, FAILED_LOGIN_MAX,
                         FAILED_LOGIN_TTL, LOGIN_LOCKOUT_SECONDS,
                         MAX_LOGIN_ATTEMPTS, MAX_UPDATE_SECONDS,
                         MIN_UPDATE_SECONDS, TRUSTED_PROXIES)
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
# HINWEIS ZUR PORTABILITÄT (HTML inline & CSS Grid):
# Normalerweise gehört HTML in /templates. Da dieses Skript aber oft per 
# Copy&Paste installiert wird, bleibt alles in einer Datei (Zero-Config-Ansatz).
# Das Layout nutzt CSS Grid und einen Mobile-First Ansatz (Flexbox column), 
# damit es sich auf Smartphones und Desktop-PCs automatisch perfekt anordnet.
# ------------------------------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Türschild-Admin</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #1e293b; margin: 0; padding: 15px; display: flex; justify-content: center; }
        .card { background: white; max-width: 950px; width: 100%; border-radius: 20px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1); overflow: hidden; margin-top: 10px; margin-bottom: 20px; }
        
        .header { background-color: #0f172a; color: white; padding: 30px; }
        .header h1 { margin: 0; font-size: 24px; letter-spacing: -1px; text-transform: uppercase; }
        .header p { margin: 5px 0 0; opacity: 0.6; font-size: 12px; font-weight: bold; }
        .content { padding: 30px; }
        
        .dashboard-grid { display: flex; flex-direction: column; gap: 0; }
        .col-preview { margin-top: 20px; margin-bottom: 20px; }
        
        .section-title { font-size: 11px; font-weight: 800; color: #64748b; text-transform: uppercase; margin: 30px 0 15px 0; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; letter-spacing: 0.5px; }
        .section-title:first-child { margin-top: 0; }
        
        form.inline-form { margin: 0; }
        .btn-group { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; }
        .btn-full { grid-column: span 2; }
        .btn { width: 100%; box-sizing: border-box; display: block; text-decoration: none; text-align: center; padding: 15px; border-radius: 12px; font-weight: bold; color: white; transition: transform 0.1s; border: none; cursor: pointer; font-size: 14px;}
        .btn:active { transform: scale(0.98); }
        .btn-update { background-color: #007BFF; } 
        .btn-demo { background-color: #6f42c1; } 
        .btn-off { background-color: #DC3545; }    
        .btn-on { background-color: #28A745; } 
        .btn-test { background-color: #f59e0b; }    
        .btn-save { background-color: #0f172a; width: 100%; font-size: 16px; margin-top: 5px; color: white; padding: 15px; border-radius: 12px; font-weight: bold; }
        
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 10px; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }
        input { width: 100%; box-sizing: border-box; background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 12px; border-radius: 10px; font-size: 14px; font-weight: 600; outline: none; }
        
        .lesson-block { background: #f8fafc; border-radius: 10px; padding: 15px; margin-top: 10px; border: 1px solid #e2e8f0; }
        .empty-state { text-align: center; color: #94a3b8; font-size: 13px; padding: 20px; background: #f8fafc; border-radius: 10px; margin-top: 10px; font-weight: bold; }
        .error-msg { background-color: #fee2e2; color: #dc2626; padding: 15px; border-radius: 10px; font-size: 13px; font-weight: bold; text-align: center; margin-bottom: 20px; }
        .warn-msg { background-color: #fef3c7; color: #854d0e; padding: 15px; border-radius: 10px; font-size: 13px; font-weight: bold; text-align: center; margin-bottom: 20px; line-height: 1.5; }
        .footer { text-align: center; font-size: 10px; color: #cbd5e1; margin-top: 35px; text-transform: uppercase; letter-spacing: 1px; }
        
        .tag-red { background-color: #fee2e2; color: #dc2626; padding: 4px 8px; border-radius: 5px; font-size: 11px; font-weight: bold; text-transform: uppercase; margin-bottom: 6px; display: inline-block;}
        .tag-yellow { background-color: #fef08a; color: #854d0e; padding: 4px 8px; border-radius: 5px; font-size: 11px; font-weight: bold; text-transform: uppercase; margin-bottom: 6px; display: inline-block;}

        @media (min-width: 800px) {
            body { padding: 40px 20px; }
            .card { margin-top: 0; }
            .dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; align-items: start; }
            .col-controls-top { grid-column: 1; grid-row: 1; }
            .col-controls-bottom { grid-column: 1; grid-row: 2; }
            .col-preview { grid-column: 2; grid-row: 1 / span 2; margin-top: 0; margin-bottom: 0; background-color: #f8fafc; padding: 25px; border-radius: 15px; border: 2px dashed #e2e8f0; }
            .col-preview .section-title { margin-top: 0; }
            .col-preview .lesson-block { background: white; }
            .col-preview .empty-state { background: white; }
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1>Display-Control</h1>
            <p>{{ conf.get('ROOM_NAME', 'Unbekannt') }} | Raumanzeige</p>
        </div>
        
        <div class="content">
            {% if conf|length == 0 %}
                <div class="error-msg">Konfigurationsfehler! Die Datei 'config.json' konnte nicht gelesen werden.</div>
            {% endif %}

            {% if stale %}
                <div class="warn-msg">
                    WebUntis ist derzeit nicht erreichbar.<br>
                    Angezeigt wird der zuletzt abgerufene Plan von heute &ndash; kurzfristige
                    Aenderungen koennen darin fehlen.
                    {% if last_sync %}<br>Letzter erfolgreicher Abruf: {{ last_sync }}{% endif %}
                </div>
            {% endif %}
            
            <div class="dashboard-grid">
                
                <div class="col-controls-top">
                    <div class="section-title">Gerätesteuerung</div>
                    <div class="btn-group">
                        <form action="/update" method="POST" class="inline-form btn-full">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-update">Manuelles Update</button>
                        </form>
                        
                        <form action="/toggle" method="POST" class="inline-form">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn {% if conf.get('DISPLAY_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                                {% if conf.get('DISPLAY_ACTIVE', True) %}Display aus{% else %}Display an{% endif %}
                            </button>
                        </form>
                        
                        <form action="/toggle_touch" method="POST" class="inline-form">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn {% if conf.get('TOUCH_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                                {% if conf.get('TOUCH_ACTIVE', True) %}Touch aus{% else %}Touch an{% endif %}
                            </button>
                        </form>
                    </div>
                    
                    <div class="section-title">Einstellungen</div>
                    <form action="/save" method="POST">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                        <div class="form-group">
                            <label>Anzeigeraum</label>
                            <input type="text" name="ROOM_NAME" value="{{ conf.get('ROOM_NAME', '') }}">
                        </div>
                        <div class="form-group">
                            <label>Intervall (Sekunden, mind. {{ min_interval }})</label>
                            <input type="number" name="AUTO_UPDATE_SECONDS" value="{{ conf.get('AUTO_UPDATE_SECONDS', 900) }}" min="{{ min_interval }}" max="{{ max_interval }}">
                        </div>
                        <button type="submit" class="btn btn-save">Speichern</button>
                    </form>
                </div>
                
                <div class="col-preview">
                    <div class="section-title">Aktuelle Anzeige ({{ conf.get('ROOM_NAME', '') }})</div>
                    <div>
                        {% if data and data is mapping and (data.current or data.next) %}
                            <h4 style="margin: 15px 0 5px 0; font-size: 12px; color: #64748b;">JETZT</h4>
                            {% if data.current %}
                                <div class="lesson-block">
                                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 8px;">
                                        <strong style="color: #0f172a; font-size: 14px;">{{ data.current.stunde }}</strong>
                                        <span style="color: #64748b; font-size: 12px; font-weight: bold;">{{ data.current.zeit }}</span>
                                    </div>
                                    
                                    {% if data.current.status_code == 'cancelled' %}<div class="tag-red">Fällt aus</div>
                                    {% elif data.current.status_code == 'irregular' %}<div class="tag-yellow">Vertretung</div>{% endif %}
                                    
                                    <div style="font-size: 16px; font-weight: 800; color: #1e293b; margin-bottom: 4px;">
                                        {{ data.current.fach_lang if data.current.fach_lang else data.current.fach }} <span style="color: #cbd5e1; margin: 0 4px;">|</span> {{ data.current.klasse }}
                                    </div>
                                    <div style="font-size: 12px; color: #475569; font-weight: 600;">Lehrkraft: {{ data.current.lehrer }}</div>
                                    
                                    {% if data.current.stunden_info %}
                                    <div style="margin-top: 8px; padding: 6px 10px; background-color: #e2e8f0; border-radius: 6px; font-size: 11px; color: #334155; border-left: 3px solid #94a3b8;">
                                        <strong>Info:</strong> {{ data.current.stunden_info }}
                                    </div>
                                    {% endif %}
                                </div>
                            {% else %}
                                <div class="empty-state">{{ msg }}</div>
                            {% endif %}

                            <h4 style="margin: 20px 0 5px 0; font-size: 12px; color: #64748b;">DANACH</h4>
                            {% if data.next %}
                                <div class="lesson-block">
                                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 8px;">
                                        <strong style="color: #0f172a; font-size: 14px;">{{ data.next.stunde }}</strong>
                                        <span style="color: #64748b; font-size: 12px; font-weight: bold;">{{ data.next.zeit }}</span>
                                    </div>
                                    
                                    {% if data.next.status_code == 'cancelled' %}<div class="tag-red">Fällt aus</div>
                                    {% elif data.next.status_code == 'irregular' %}<div class="tag-yellow">Vertretung</div>{% endif %}
                                    
                                    <div style="font-size: 16px; font-weight: 800; color: #1e293b; margin-bottom: 4px;">
                                        {{ data.next.fach_lang if data.next.fach_lang else data.next.fach }} <span style="color: #cbd5e1; margin: 0 4px;">|</span> {{ data.next.klasse }}
                                    </div>
                                    <div style="font-size: 12px; color: #475569; font-weight: 600;">Lehrkraft: {{ data.next.lehrer }}</div>
                                    
                                    {% if data.next.stunden_info %}
                                    <div style="margin-top: 8px; padding: 6px 10px; background-color: #e2e8f0; border-radius: 6px; font-size: 11px; color: #334155; border-left: 3px solid #94a3b8;">
                                        <strong>Info:</strong> {{ data.next.stunden_info }}
                                    </div>
                                    {% endif %}
                                </div>
                            {% else %}
                                <div class="empty-state">Kein Unterricht mehr.</div>
                            {% endif %}
                            
                        {% else %}
                            <div class="empty-state" style="font-size: 16px; padding: 30px 20px;">
                                {{ msg }}
                            </div>
                        {% endif %}
                    </div>
                </div>
                
                <div class="col-controls-bottom">
                    <div class="section-title">Test & Simulation</div>
                    <div style="background: #f8fafc; border-radius: 10px; padding: 15px; margin-bottom: 15px; border: 1px solid #e2e8f0;">
                        <label>Datum & Uhrzeit simulieren</label>
                        <form action="/simulate_time" method="POST" style="margin-bottom: 10px;">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="datetime-local" name="SIM_TIME" required style="margin-bottom: 10px;">
                            <button type="submit" class="btn btn-test">Zeit simulieren</button>
                        </form>
                        <form action="/reset_time" method="POST" class="inline-form">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-update">Zurück zur Echtzeit</button>
                        </form>
                    </div>
                    
                    <div class="btn-group">
                        <form action="/demo" method="POST" class="inline-form btn-full">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-demo">Lokale Dummy-Daten laden</button>
                        </form>
                        <form action="/test_all" method="POST" class="inline-form btn-full">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-test" style="background-color: #0f172a;">Display-Testlauf (ca. 30 Sek)</button>
                        </form>
                    </div>

                    <div class="section-title">System</div>
                    <div class="btn-group">
                        <!-- Die onsubmit Methode wirft vorher noch einen JavaScript-Confirm-Dialog aus -->
                        <form action="/sys_reboot" method="POST" class="inline-form btn-full" onsubmit="return confirm('Raspberry Pi wirklich neu starten? Das E-Paper wird kurz abgeschaltet.');">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-test" style="background-color: #475569;">System Neustart</button>
                        </form>
                        <form action="/sys_shutdown" method="POST" class="inline-form btn-full" onsubmit="return confirm('ACHTUNG: Raspberry Pi wirklich herunterfahren? Er muss danach manuell vom Strom getrennt und wieder verbunden werden, um neu zu starten!');">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit" class="btn btn-off" style="background-color: #94a3b8; color: #0f172a;">System Herunterfahren</button>
                        </form>
                    </div>
                </div>

            </div>
            
            <p class="footer">Status: {{ now }}{% if sim_active %} <br><strong style="color: #dc2626;">(ZEIT WIRD SIMULIERT)</strong>{% endif %}</p>
        </div>
    </div>
</body>
</html>
"""

# ------------------------------------------------------------------------------
# FLASK ROUTEN (Endpunkte für das Web-Interface)
# ------------------------------------------------------------------------------
@app.route('/')
@requires_auth
def index():
    """Rendert die Hauptseite des Admin-Interfaces mit Jinja2-Templating."""
    conf = get_cached_config()
    
    # Thread-sicherer Lesevorgang aus dem globalen State
    with app_state.state_lock:
        is_simulated = app_state.simulated_datetime is not None
        d_data = app_state.current_display_data
        d_msg_raw = app_state.current_display_msg
        c_token = app_state.csrf_token
        is_stale = app_state.data_is_stale
        sync_dt = app_state.last_successful_sync

    # Zeitstempel des letzten geglückten API-Abrufs (nur relevant, wenn wir
    # gerade aus der Offline-Rücklage anzeigen).
    last_sync = sync_dt.strftime("%d.%m.%Y %H:%M") if sync_dt else None


    # PÄDAGOGISCHER HINTERGRUND (Sicherheit gegen XSS):
    # Um zu verhindern, dass externe API-Daten schadhaften HTML-Code in unser
    # Dashboard einschleusen (Cross-Site Scripting), escapen wir den Text zuerst.
    # Erst DANACH ersetzen wir die reinen Zeilenumbrüche (\n) durch HTML-Breaks (<br>), 
    # damit lange Feriennamen im Browser schön dargestellt werden.
    d_msg_safe = Markup(escape(d_msg_raw)).replace('\n', Markup('<br>'))
        
    display_time = get_now().strftime("%d.%m.%Y %H:%M:%S")

    return render_template_string(
        HTML_TEMPLATE, 
        conf=conf, 
        data=d_data, 
        msg=d_msg_safe, 
        now=display_time,
        sim_active=is_simulated,
        csrf_token=c_token,
        stale=is_stale,
        last_sync=last_sync,
        min_interval=MIN_UPDATE_SECONDS,
        max_interval=MAX_UPDATE_SECONDS
    )

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
    conf = get_cached_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        try:
            val = int(request.form.get('AUTO_UPDATE_SECONDS', DEFAULT_UPDATE_SECONDS))
            conf['AUTO_UPDATE_SECONDS'] = max(MIN_UPDATE_SECONDS, min(val, MAX_UPDATE_SECONDS))
        except Exception:
            pass
        save_config(conf)
        with app_state.state_lock:
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
