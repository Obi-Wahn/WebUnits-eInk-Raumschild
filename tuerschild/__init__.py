"""
WebUntis E-Paper-Raumanzeige - Programmpaket.

Die Ebenen bauen aufeinander auf und greifen nur nach unten:

    konstanten     feste Werte, keine Abhaengigkeiten
    zustand        Datenstrukturen und gemeinsamer Zustand
    konfiguration  config.json lesen und schreiben, Uhrzeit
    hardware       GPIO, I2C, Displaytreiber, Schriftarten
    anzeige        Layout und Zeichnen auf dem E-Paper
    untis          Abruf und Auswertung der Stundenplandaten
    web            Flask-Oberflaeche zur Administration
    steuerung      Hintergrundschleife, die alles zusammenfuehrt

Der Einstiegspunkt raumanzeige.py im Projektverzeichnis startet das Ganze.

Diese Datei fuehrt die oeffentlich nutzbaren Namen an einer Stelle zusammen,
sodass "import tuerschild" genuegt, um an sie heranzukommen.

WICHTIG BEIM ERSETZEN VON NAMEN (etwa in Tests):
Die Namen hier sind Kopien der Verweise, keine zweite Wahrheit. Wer eine
Funktion ersetzen will, muss das im definierenden Modul tun - also etwa
tuerschild.hardware.epd2in13_V3 statt tuerschild.epd2in13_V3. Ein Ersetzen
hier wuerde nur diese Kopie treffen, waehrend die uebrigen Ebenen weiter das
Original benutzen.
"""

from .konstanten import (BACKGROUND_ERROR_PAUSE, DEFAULT_UPDATE_SECONDS,
                         ERR_NO_NETWORK, ERR_UNTIS_OFFLINE, FAILED_LOGIN_MAX,
                         FAILED_LOGIN_TTL, HOLIDAYS_CACHE_SECONDS,
                         LOGIN_LOCKOUT_SECONDS, MAX_LOGIN_ATTEMPTS,
                         MAX_UPDATE_SECONDS, MIN_UPDATE_SECONDS,
                         PROJEKT_VERZEICHNIS, SIMULATION_MAX_SECONDS,
                         STATUS_LABELS, TOUCH_COOLDOWN, TOUCH_I2C_ADDR,
                         TOUCH_RST_PIN, TRANSIENT_ERRORS, TRUSTED_PROXIES,
                         UI_BADGE_GAP, UI_BADGE_PADDING, UI_ELLIPSIS,
                         UI_HEADER_HEIGHT, UI_HEIGHT, UI_LINE_Y, UI_MARGIN,
                         UI_WIDTH, WAVESHARE_LIB)
from .zustand import AppState, Lesson, TimedLesson, app_state
from .konfiguration import (CONFIG_FILE, get_cached_config, get_now,
                            get_update_interval, save_config)
from .hardware import (check_touch_via_i2c, clear_display_once,
                       clear_touch_interrupt_via_i2c, init_fonts)
from .anzeige import (build_detail_line, draw_lesson_block, get_text_width,
                      truncate_to_width, update_display_logic)
from .untis import (get_current_lesson, get_offline_fallback, parse_lesson,
                    resolve_timetable, select_lessons)
from .steuerung import background_loop, run_display_test_sequence
from .web import (app, check_auth, cleanup_failed_logins, get_client_ip,
                  get_local_ip)
