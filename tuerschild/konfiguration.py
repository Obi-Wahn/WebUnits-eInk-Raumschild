"""
==============================================================================
Konfigurations-Verwaltung und Uhr
==============================================================================
Liest und schreibt die config.json und liefert die aktuelle Uhrzeit. Die Uhr
liegt hier, weil sie fuer das Simulations-Feature aus dem Web-Interface
abstrahiert werden muss.
"""
import datetime
import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from .konstanten import (DEFAULT_DAY_END, DEFAULT_DAY_START,
                         DEFAULT_UPDATE_SECONDS, MAX_UPDATE_SECONDS,
                         MIN_UPDATE_SECONDS, PROJEKT_VERZEICHNIS,
                         ROOM_NAME_MAX_LEN, SCHEDULE_MAX_BREAKS,
                         SCHEDULE_MAX_LESSONS, SCHEDULE_NAME_MAX_LEN,
                         SIMULATION_MAX_SECONDS)
from .zustand import app_state

# Die Konfigurationsdatei liegt im Projektverzeichnis, nicht im Paket.
CONFIG_FILE = os.path.join(PROJEKT_VERZEICHNIS, 'config.json')

def get_cached_config() -> Dict[str, Any]:
    """
    Lädt die 'config.json' nur neu, wenn sich ihr Zeitstempel (mtime) geändert hat.
    Verhindert langsame Festplattenzugriffe, wenn Flask mehrmals pro Sekunde anfragt.
    """
    with app_state.config_lock:
        if not os.path.exists(CONFIG_FILE): return {}
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
            if mtime > app_state.last_config_mtime:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    app_state.cached_config = json.loads(content) if content else {}
                app_state.last_config_mtime = mtime
                # Nur bei einem tatsaechlichen Neueinlesen - diese Funktion
                # wird mehrmals pro Sekunde aufgerufen, das Protokoll wuerde
                # sonst zulaufen. Nach einer Aenderung der Datei steht der
                # Hinweis genau einmal da.
                melde_konfigurationsfehler(app_state.cached_config)
        except Exception as e:
            logging.error(f"FEHLER beim Laden der config.json: {e}")
        # Wichtig: Eine Kopie des Dictionaries zurückgeben (dict()), 
        # damit Referenzverknüpfungen nicht versehentlich den Cache verändern.
        return dict(app_state.cached_config)

def save_config(config: Dict[str, Any]) -> None:
    """
    Speichert Einstellungen stromausfallsicher ab (Atomare Dateitransaktion).
    
    TECHNISCHER HINTERGRUND:
    Würde der Raspberry Pi exakt während dem Schreibvorgang 'open(file, w)' 
    den Strom verlieren, wäre die config.json korrupt (0 Byte). Wir schreiben 
    daher erst in eine unsichtbare, temporäre Datei und tauschen diese am Ende 
    nahtlos (atomar) auf Linux-Betriebssystemebene aus (os.replace).
    """
    with app_state.config_lock:
        try:
            dir_name = os.path.dirname(CONFIG_FILE)
            fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            os.replace(temp_path, CONFIG_FILE)
            # RAM-Cache invalidieren, damit er beim nächsten Aufruf neu von der SD-Karte liest
            app_state.last_config_mtime = 0 
        except Exception as e:
            logging.error(f"FEHLER beim Speichern der config.json: {e}")

def get_update_interval(conf: Dict[str, Any]) -> int:
    """
    Liefert das Abrufintervall in Sekunden, begrenzt auf einen sinnvollen Bereich.

    Die Begrenzung sitzt bewusst hier und nicht nur im Web-Formular: Die
    config.json lässt sich auch von Hand bearbeiten, und ein dort eingetragener
    Wert von 5 Sekunden würde den WebUntis-Server unnötig belasten. Ein
    unbrauchbarer Eintrag (Text, leer) fällt auf die Voreinstellung zurück,
    statt das Programm mit einer Ausnahme zu beenden.
    """
    try:
        val = int(conf.get('AUTO_UPDATE_SECONDS', DEFAULT_UPDATE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_UPDATE_SECONDS
    return max(MIN_UPDATE_SECONDS, min(val, MAX_UPDATE_SECONDS))


# ------------------------------------------------------------------------------
# Pruefung der Eingaben aus dem Web-Formular
# ------------------------------------------------------------------------------
# WARUM DIE PRUEFUNG HIER LIEGT UND NICHT IN web.py:
# Sie gehoert zur Konfiguration, nicht zur Oberflaeche. So laesst sie sich ohne
# Flask pruefen, und ein spaeterer zweiter Weg in die config.json (etwa ein
# Kommandozeilenwerkzeug) benutzt dieselben Regeln.
#
# Alle Funktionen liefern ein Paar (Wert, Fehlertext). Genau eines von beiden
# ist gesetzt. Diese Form zwingt die aufrufende Stelle dazu, den Fehlerfall zu
# behandeln - anders als eine Funktion, die im Zweifel None zurueckgibt und
# deren Rueckgabe man versehentlich einfach weiterreicht.
def pruefe_raumname(wert: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Prueft den Raumnamen aus dem Formular.

    Der Name ist keine Kosmetik: Er geht als Suchbegriff an WebUntis. Bisher
    wurde er ungeprueft uebernommen - ein leeres Feld fuehrte dazu, dass das
    Schild "Raum None fehlt." anzeigte, und die Ursache stand nirgends.
    """
    if wert is None:
        return None, "Der Raumname fehlt in der Anfrage."

    name = str(wert).strip()

    if not name:
        return None, "Der Raumname darf nicht leer sein."
    if len(name) > ROOM_NAME_MAX_LEN:
        return None, (f"Der Raumname ist zu lang (maximal {ROOM_NAME_MAX_LEN} "
                      f"Zeichen, eingegeben: {len(name)}).")
    # Steuerzeichen (Zeilenumbrueche, Tabulatoren) kommen nur durch Einfuegen
    # aus einer anderen Anwendung herein und wuerden die Kopfzeile zerlegen.
    if any(ord(zeichen) < 32 for zeichen in name):
        return None, "Der Raumname enthält unerlaubte Sonderzeichen."

    return name, None


def _pruefe_uhrzeit(wert: Any, wo: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Prueft eine einzelne Uhrzeit. Verlangt wird genau die Form "HH:MM".

    WARUM "8:00" NICHT DURCHGEHT, OBWOHL ES SICH LESEN LAESST:
    Der uebrige Programmcode vergleicht Uhrzeiten als Zeichenketten.
    parse_lesson() ordnet einer Stunde ihren Namen zu, indem es die Startzeit
    aus WebUntis - immer zweistellig - mit dem Eintrag im Stundenplan
    vergleicht. "8:00" trifft dabei auf nichts. Der Stundenname bleibt leer,
    und zwar ohne jede Fehlermeldung.

    Genau das ist der Tippfehler, den niemand als Tippfehler empfindet, und
    deshalb wird er hier benannt statt stillschweigend zurechtgebogen: Diese
    Pruefung schreibt die config.json nicht um, sie meldet nur. Wuerde sie die
    kurze Schreibweise als gueltig durchwinken, bliebe der Fehler in der Datei
    stehen und das Schild weiter stumm.
    """
    if wert is None:
        return None, f"{wo}: Die Uhrzeit fehlt."
    text = str(wert).strip()
    try:
        zeit = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None, f"{wo}: '{text}' ist keine Uhrzeit im Format HH:MM."

    einheitlich = zeit.strftime("%H:%M")
    if text != einheitlich:
        return None, (f"{wo}: '{text}' muss zweistellig geschrieben werden "
                      f"('{einheitlich}') - sonst bleibt der Stundenname leer.")
    return einheitlich, None


def _pruefe_eintraege(rohliste: Any, wo: str, hoechstzahl: int
                      ) -> Tuple[Optional[list], Optional[str]]:
    """Prueft die Liste der Stunden beziehungsweise der Pausen."""
    if rohliste is None:
        return [], None
    if not isinstance(rohliste, list):
        return None, f"{wo} muss eine Liste sein."
    if len(rohliste) > hoechstzahl:
        return None, (f"{wo}: zu viele Einträge "
                      f"({len(rohliste)}, erlaubt sind {hoechstzahl}).")

    geprueft = []
    for nummer, eintrag in enumerate(rohliste, start=1):
        stelle = f"{wo}, Eintrag {nummer}"
        if not isinstance(eintrag, dict):
            return None, f"{stelle}: erwartet wird ein Objekt mit start, end und name."

        start, fehler = _pruefe_uhrzeit(eintrag.get("start"), f"{stelle} (start)")
        if fehler:
            return None, fehler
        ende, fehler = _pruefe_uhrzeit(eintrag.get("end"), f"{stelle} (end)")
        if fehler:
            return None, fehler
        if ende <= start:
            return None, f"{stelle}: Das Ende ({ende}) liegt nicht nach dem Beginn ({start})."

        name = str(eintrag.get("name", "")).strip()
        if len(name) > SCHEDULE_NAME_MAX_LEN:
            return None, (f"{stelle}: Der Name ist zu lang "
                          f"(maximal {SCHEDULE_NAME_MAX_LEN} Zeichen).")
        if any(ord(zeichen) < 32 for zeichen in name):
            return None, f"{stelle}: Der Name enthält unerlaubte Sonderzeichen."

        geprueft.append({"start": start, "end": ende, "name": name})

    return geprueft, None


def pruefe_stundenplan(roh: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Prueft den Stundenplan aus der config.json.

    WAS DER STUNDENPLAN IST - UND WAS NICHT:
    Er enthaelt keine Plandaten. Die kommen ausschliesslich aus WebUntis und
    werden von hier aus auch nie zurueckgeschrieben. SCHEDULE haelt nur fest,
    wie dieses Geraet die Zeiten des Hauses benennt: dass "08:00" die 1. Stunde
    ist und dass zwischen 13:20 und 13:55 Mittagspause statt "Raum ist frei"
    auf dem Schild steht.

    WARUM DAS GEPRUEFT WIRD:
    Ein kaputter Stundenplan aeussert sich nicht als Fehlermeldung, sondern als
    Schild, das keine Stundennamen mehr anzeigt oder Pausen nicht mehr erkennt.
    Der haeufigste Fall ist "8:00" statt "08:00" - eine Schreibweise, die kein
    Mensch als falsch empfindet, die parse_lesson() aber nie findet, weil dort
    Zeichenketten verglichen werden. Wer die config.json von Hand bearbeitet,
    sucht so einen Fehler lange.
    """
    if not isinstance(roh, dict):
        return None, "Der Stundenplan muss ein JSON-Objekt sein (geschweifte Klammern)."

    beginn, fehler = _pruefe_uhrzeit(roh.get("DAY_START", DEFAULT_DAY_START), "DAY_START")
    if fehler:
        return None, fehler
    ende, fehler = _pruefe_uhrzeit(roh.get("DAY_END", DEFAULT_DAY_END), "DAY_END")
    if fehler:
        return None, fehler
    if ende <= beginn:
        return None, (f"DAY_END ({ende}) muss nach DAY_START ({beginn}) liegen.")

    stunden, fehler = _pruefe_eintraege(roh.get("LESSONS"), "LESSONS", SCHEDULE_MAX_LESSONS)
    if fehler:
        return None, fehler
    pausen, fehler = _pruefe_eintraege(roh.get("BREAKS"), "BREAKS", SCHEDULE_MAX_BREAKS)
    if fehler:
        return None, fehler

    return {"DAY_START": beginn, "DAY_END": ende,
            "LESSONS": stunden, "BREAKS": pausen}, None



def pruefe_konfiguration(conf: Dict[str, Any]) -> List[str]:
    """
    Sammelt alles, was an einer geladenen config.json auffaellt.

    WARUM BEIM LADEN UND NICHT NUR BEIM SPEICHERN:
    Die Datei wird in aller Regel von Hand bearbeitet - per SSH, wie es die
    Installationsanleitung beschreibt. Genau dieser Weg kam bisher an keiner
    Pruefung vorbei. Die Fehler, die dabei entstehen, aeussern sich nicht als
    Absturz, sondern als stille Unauffaelligkeit: leere Stundennamen, eine
    Pause, die nicht erkannt wird, oder ein Schild, das "Raum None fehlt."
    anzeigt, ohne dass irgendwo der Grund steht.

    Es wird nur gewarnt, nichts abgelehnt. Ein Tuerschild, das wegen eines
    Kommafehlers gar nicht erst startet, waere die schlechtere Loesung - und
    das Programm kommt mit fehlenden Angaben ohnehin zurecht.
    """
    beanstandungen = []

    _, fehler = pruefe_raumname(conf.get('ROOM_NAME'))
    if fehler:
        beanstandungen.append(f"ROOM_NAME: {fehler}")

    if 'SCHEDULE' not in conf:
        beanstandungen.append(
            "SCHEDULE fehlt - Stundennamen und Pausen bleiben leer.")
    else:
        _, fehler = pruefe_stundenplan(conf.get('SCHEDULE'))
        if fehler:
            beanstandungen.append(f"SCHEDULE: {fehler}")

    return beanstandungen


def melde_konfigurationsfehler(conf: Dict[str, Any]) -> None:
    """Schreibt die Beanstandungen aus pruefe_konfiguration() ins Protokoll."""
    for hinweis in pruefe_konfiguration(conf):
        logging.warning(f"config.json: {hinweis}")


def formatiere_dauer(sekunden: float) -> str:
    """
    Macht aus einer Sekundenzahl eine lesbare Angabe wie "3 Std. 20 Min.".

    Steht hier bei der Uhr, weil sowohl das Protokoll als auch das
    Web-Interface dieselbe Formulierung brauchen und keine der beiden Ebenen
    von der anderen abhaengen soll.
    """
    minuten = max(0, int(sekunden // 60))
    stunden, minuten = divmod(minuten, 60)
    if stunden and minuten:
        return f"{stunden} Std. {minuten} Min."
    if stunden:
        return f"{stunden} Std."
    return f"{minuten} Min."


def get_now() -> datetime.datetime:
    """
    Gibt die aktuelle Zeit zurück.
    Abstrahiert die Systemzeit, um das Zeit-Simulations-Feature im Web-Interface
    zu ermöglichen (Time-Travel-Tests für Ferien und Randfälle).

    SICHERUNG GEGEN VERGESSENE SIMULATION:
    Eine gesetzte Simulationszeit gilt nur für SIMULATION_MAX_SECONDS, danach
    kehrt das Programm von selbst zur echten Uhr zurück. Ohne diese Grenze
    würde ein vergessener Testlauf dazu führen, dass das Schild auf unbestimmte
    Zeit einen falschen Tag anzeigt - und zwar unauffällig, weil in der
    Kopfzeile ein völlig plausibles Datum steht.
    """
    with app_state.state_lock:
        if app_state.simulated_datetime:
            # Fehlt der Startzeitpunkt, beginnt die Frist jetzt. So kann das
            # Setzen der Simulation niemals dadurch wirkungslos werden, dass
            # zwei zusammengehoerige Felder auseinanderlaufen - die Simulation
            # geht im Zweifel nicht verloren, sie laeuft nur spaeter ab.
            if app_state.simulation_started_at is None:
                app_state.simulation_started_at = time.time()

            laufzeit = time.time() - app_state.simulation_started_at
            if laufzeit < SIMULATION_MAX_SECONDS:
                return app_state.simulated_datetime

            logging.info("Zeitsimulation abgelaufen - zurück zur Echtzeit.")
            app_state.simulated_datetime = None
            app_state.simulation_started_at = None
            app_state.force_update_flag = True
    return datetime.datetime.now()

