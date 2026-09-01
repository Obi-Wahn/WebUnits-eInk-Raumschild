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
from typing import Any, Dict, Optional, Tuple

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
    Prueft eine einzelne Uhrzeit und gibt sie einheitlich als "HH:MM" zurueck.

    Die Vereinheitlichung ist noetig, weil der uebrige Programmcode Uhrzeiten
    als Zeichenketten vergleicht: parse_lesson() ordnet einer Stunde ihren Namen
    zu, indem es die Startzeit der WebUntis-Stunde mit dem Eintrag im
    Stundenplan vergleicht. "8:00" und "08:00" waeren fuer diesen Vergleich
    verschiedene Zeiten - der Stundenname bliebe leer, ohne jede Fehlermeldung.
    """
    if wert is None:
        return None, f"{wo}: Die Uhrzeit fehlt."
    text = str(wert).strip()
    try:
        zeit = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None, f"{wo}: '{text}' ist keine Uhrzeit im Format HH:MM."
    return zeit.strftime("%H:%M"), None


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


def pruefe_stundenplan(text: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Prueft den Stundenplan aus dem Formular und gibt ihn aufgeraeumt zurueck.

    Der Stundenplan wird als JSON bearbeitet - dieselbe Form, in der er in der
    config.json steht und in der die Anleitung ihn beschreibt. Ein
    Formular mit Eingabefeldern pro Stunde waere bequemer, braeuchte aber
    JavaScript zum Hinzufuegen und Entfernen von Zeilen; das Projekt kommt
    bisher ohne aus.

    Wichtiger als die Bequemlichkeit ist ohnehin, dass nichts Unbrauchbares
    gespeichert wird: Ein kaputter Stundenplan aeussert sich nicht als
    Fehlermeldung, sondern als Schild, das keine Stundennamen mehr anzeigt oder
    Pausen nicht mehr erkennt. Deshalb wird hier jede Uhrzeit einzeln geprueft
    und die Rueckgabe enthaelt nur die bekannten Felder - alles Ueberzaehlige
    faellt weg, statt unbemerkt in der config.json zu landen.
    """
    if text is None:
        return None, "Der Stundenplan fehlt in der Anfrage."

    try:
        roh = json.loads(str(text))
    except json.JSONDecodeError as e:
        return None, f"Kein gültiges JSON: {e.msg} (Zeile {e.lineno}, Spalte {e.colno})."

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


def formatiere_stundenplan(conf: Dict[str, Any]) -> str:
    """
    Bereitet den Stundenplan fuer das Textfeld im Web-Formular auf.

    Eingerueckt und mit echten Umlauten, damit die Datei im Browser lesbar
    bleibt - json.dumps() wuerde sonst "\u00e4" schreiben.
    """
    plan = conf.get("SCHEDULE")
    if not isinstance(plan, dict):
        plan = {"DAY_START": DEFAULT_DAY_START, "DAY_END": DEFAULT_DAY_END,
                "LESSONS": [], "BREAKS": []}
    return json.dumps(plan, indent=4, ensure_ascii=False)


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

