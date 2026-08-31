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
from typing import Any, Dict

from .konstanten import (DEFAULT_UPDATE_SECONDS, MAX_UPDATE_SECONDS,
                         MIN_UPDATE_SECONDS, PROJEKT_VERZEICHNIS,
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

