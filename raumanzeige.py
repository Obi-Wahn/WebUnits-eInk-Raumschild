#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
==============================================================================
WebUntis E-Paper Türschild
Ein Projekt für den schulischen Einsatz (Raspberry Pi Zero 2 W)
==============================================================================
Dieses Skript verbindet sich mit der WebUntis API, lädt den aktuellen
Stundenplan herunter und visualisiert ihn auf einem E-Paper-Display.
Zusätzlich stellt es ein lokales Web-Interface zur Administration bereit.

Technische Schwerpunkte der Architektur: 
- Nebenläufigkeit (Multithreading) & Ressourcen-Sperren (Locks)
- Kryptographie (Passwort-Hashing & CSRF-Tokens)
- Objektorientierung (State-Kapselung in AppState, Dataclasses)
- Ausfallsicherheit (Atomare Dateizugriffe, Graceful Degradation)
- Prinzip der geringsten Privilegien (PoLP für Systembefehle)
"""

# ==============================================================================
# 1. IMPORTS & HARDWARE-SETUP
# ==============================================================================
import sys
import os
import time
import datetime
import json
import threading
import socket
import tempfile
import subprocess
import logging
import secrets               # Für kryptografisch sichere Zufallszahlen (CSRF-Tokens)
from dataclasses import dataclass # Für strukturierte Daten statt loser Dictionaries
from typing import Optional, Dict, Any, Tuple
import webuntis
from functools import wraps
from flask import Flask, render_template_string, request, redirect, Response, abort
from waitress import serve
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------------------------
# LOGGING KONFIGURATION
# ------------------------------------------------------------------------------
# Das Standard-Logging von Python. Ersetzt simple print()-Befehle.
# INFO zeigt normale Systemereignisse an. Für eine tiefe Hardware-Fehlersuche
# (z.B. I2C Bus Aussetzer) kann das Level auf logging.DEBUG gestellt werden.
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ------------------------------------------------------------------------------
# HARDWARE-TREIBER LADEN (Graceful Degradation)
# ------------------------------------------------------------------------------
# TECHNISCHER HINTERGRUND: "Graceful Degradation" (Gutmütiges Herabstufen)
# Die Try-Except-Blöcke ermöglichen es, das Skript auch auf einem normalen 
# Windows/Mac-Rechner (ohne GPIO-Pins) zu testen und weiterzuentwickeln. 
# Fehlt die Raspberry-Hardware, fangen wir den Fehler ab und loggen ihn als 
# Warnung, anstatt das Programm mit einem harten Absturz zu beenden.
try:
    import RPi.GPIO as GPIO
except ImportError as e:
    logging.warning(f"RPi.GPIO nicht installiert (Entwicklungsmodus?). Fehler: {e}")
    GPIO = None

try:
    import smbus2 as smbus
    i2c_bus = smbus.SMBus(1)
except (ImportError, FileNotFoundError) as e:
    logging.warning(f"I2C Bus nicht verfügbar (Entwicklungsmodus?). Fehler: {e}")
    smbus = None
    i2c_bus = None

# Pfad zu den E-Paper-Treibern des Herstellers (Waveshare) dynamisch hinzufügen
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

try:
    from waveshare_epd import epd2in13_V3
except ImportError as e:
    logging.warning(f"waveshare_epd Treiber nicht gefunden. ({e})")
    epd2in13_V3 = None


# ==============================================================================
# 2. KONSTANTEN, DATENKLASSEN & ZENTRALER ZUSTAND (State Management)
# ==============================================================================
app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')

# Hardware-Pins & Konstanten
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14           # Hexadezimale I2C-Adresse des Touch-Chips
TOUCH_COOLDOWN = 5.0            # Entprell-Zeit in Sekunden (verhindert Touch-Spam)
HOLIDAYS_CACHE_SECONDS = 86400  # API-Schonung: Ferien für 24 Stunden im RAM cachen

# Magic Numbers (Festgelegte Layout-Werte für das E-Paper-Display)
UI_WIDTH = 250
UI_HEIGHT = 122
UI_HEADER_HEIGHT = 24
UI_LINE_Y = 68
UI_MARGIN = 5

@dataclass
class Lesson:
    """
    Datenstruktur für einen einzelnen Unterrichtsblock.
    Verhindert Schreibfehler, die bei der Nutzung von Standard-Dictionaries 
    (z.B. lesson['Fach'] statt lesson['fach']) oft passieren.
    """
    fach: str
    lehrer: str
    klasse: str
    zeit: str
    stunde: str
    status_code: Optional[str]
    stunden_info: str

class AppState:
    """
    Zentrale Zustandsverwaltung (State Management).
    Kapselt alle globalen Variablen an einem Ort. Dies verhindert das unkontrollierte
    Überschreiben von Werten über verschiedene Dateien/Funktionen hinweg.
    """
    def __init__(self):
        # Steuerungs-Flags für den Ablauf der Hintergrund-Schleife
        self.force_update_flag: bool = True     # Erzwingt ein sofortiges Display-Update
        self.show_demo_once: bool = False       # Zeigt einmalig simulierte Demo-Daten
        self.test_mode_active: bool = False     # Pausiert das System für den Testlauf
        self.shutdown_event = threading.Event() # Signalisiert allen Threads, dass das System beendet wird
        
        # TECHNISCHER HINTERGRUND: Thread-Locks (Sperren)
        # Hier laufen Threads parallel (Flask-Webserver vs. Hintergrund-Loop). 
        # Ein "Lock" (Mutex) wirkt wie ein Schlüssel: Wer den Schlüssel hat, darf 
        # die Hardware/Datei nutzen. Der andere Thread wartet. Das verhindert Datenkorruption.
        self.display_lock = threading.Lock()    # Schützt das SPI-Display vor simultanen Schreibzugriffen
        self.state_lock = threading.Lock()      # Schützt Zugriffe auf diesen AppState
        self.config_lock = threading.Lock()     # Schützt das Dateisystem (config.json)
        
        # Caches & Simulation (Zwischenspeicher im RAM für mehr Performance)
        self.simulated_datetime: Optional[datetime.datetime] = None
        self.current_display_data: Optional[Dict[str, Optional[Lesson]]] = None
        self.current_display_msg: str = "Warte auf erstes Update..."
        self.cached_config: Dict[str, Any] = {}
        self.last_config_mtime: float = 0
        self.cached_holidays = None
        self.last_holidays_fetch: float = 0
        self.global_fonts: Dict[str, ImageFont.FreeTypeFont] = {}
        
        # Security: Generiert beim Start einen einmaligen, kryptografisch sicheren Token.
        # Schützt gegen CSRF (Cross-Site Request Forgery) Angriffe über das Web-Interface.
        self.csrf_token: str = secrets.token_hex(32)

# Instanziierung des globalen Zustands
app_state = AppState()


# ==============================================================================
# 3. KONFIGURATIONS-VERWALTUNG & UHR
# ==============================================================================
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

def get_now() -> datetime.datetime:
    """
    Gibt die aktuelle Zeit zurück. 
    Abstrahiert die Systemzeit, um das Zeit-Simulations-Feature im Web-Interface 
    zu ermöglichen (Time-Travel-Tests für Ferien und Randfälle).
    """
    with app_state.state_lock:
        if app_state.simulated_datetime:
            return app_state.simulated_datetime
    return datetime.datetime.now()


# ==============================================================================
# 4. HARDWARE-EBENE (TOUCH, DISPLAY RESET & FONTS)
# ==============================================================================
def init_fonts() -> None:
    """
    Lädt die Schriftarten beim Programmstart einmalig in den RAM (Lazy Loading).
    I/O-Optimierung: Verhindert langsame SD-Karten-Zugriffe bei jedem Display-Refresh.
    """
    if app_state.global_fonts: return 
    try: 
        app_state.global_fonts['mega'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
        app_state.global_fonts['huge'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
        app_state.global_fonts['large'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18) 
        app_state.global_fonts['med'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
        app_state.global_fonts['reg'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
        app_state.global_fonts['small'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    except Exception as e:
        logging.warning(f"Schriftarten nicht gefunden, nutze Fallback. ({e})")
        default = ImageFont.load_default()
        app_state.global_fonts = {k: default for k in ['mega', 'huge', 'large', 'med', 'reg', 'small']}

def check_touch_via_i2c() -> bool:
    """
    Prüft direkt über den I2C-Bus, ob der Touch-Chip eine Berührung registriert hat.
    
    TECHNISCHER HINTERGRUND:
    Der Touch-Chip speichert Berührungen in einem internen Register (Adresse 0x81, 0x4E).
    Wir lesen dieses Byte aus. Wenn das höchste Bit gesetzt ist (& 0x80), liegt 
    ein Touch vor. Anschließend MÜSSEN wir dem Chip eine "Quittung" (0x00) zurücksenden,
    damit er seinen internen Alarm wieder abschaltet, sonst bleibt der Touch hängen.
    """
    if not i2c_bus or not smbus: return False
    try:
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        
        # Bit-Maskierung auf Bit 7
        if list(read_msg)[0] & 0x80:
            # Quittungssignal an den Touch-Chip senden (Reset)
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except OSError as e:
        logging.debug(f"I2C Read Error (oft normal bei fehlendem Touch): {e}")
    return False

def clear_touch_interrupt_via_i2c() -> None:
    """Setzt den Touch-Chip manuell zurück (wird primär beim Bootvorgang genutzt)."""
    if not i2c_bus: return
    try: 
        i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except OSError as e: 
        logging.debug(f"I2C Reset Fehler: {e}")

def clear_display_once() -> None:
    """Löscht das E-Paper-Display komplett weiß (verhindert Einbrennen der Tinte)."""
    if app_state.shutdown_event.is_set() or epd2in13_V3 is None: return 
    
    with app_state.display_lock:
        try:
            epd = epd2in13_V3.EPD()
            epd.init()
            epd.Clear(0xFF)
            epd.sleep()
        except Exception as e: 
            logging.error(f"Display Clear Fehler: {e}")


# ==============================================================================
# 5. DATEN-EBENE: WEBUNTIS API
# ==============================================================================
def parse_lesson(lesson, conf: Dict[str, Any]) -> Optional[Lesson]:
    """
    Hilfsfunktion: Nimmt ein komplexes, rohes WebUntis-Klassenobjekt und 
    extrahiert genau die Daten, die wir für das Display brauchen.
    """
    if not lesson or not getattr(lesson, 'start', None) or not getattr(lesson, 'end', None): 
        return None
    
    schedule = conf.get("SCHEDULE", {})
    lessons_conf = schedule.get("LESSONS", [])
    
    start_str = lesson.start.strftime("%H:%M")
    stunde_name = ""
    
    # Ordnet der reinen Uhrzeit (z.B. 08:00) den Namen der Stunde (z.B. "1. Std.") zu
    if isinstance(lessons_conf, list):
        for l in lessons_conf:
            if l.get("start") == start_str:
                stunde_name = l.get("name", "")
                break
    elif isinstance(lessons_conf, dict):
        stunde_name = lessons_conf.get(start_str, "")

    info_parts = []
    for attr in ['info', 'lstext', 'substText']:
        val = getattr(lesson, attr, '')
        if val and str(val).strip() and str(val).strip() not in info_parts:
            info_parts.append(str(val).strip())
    
    # Rückgabe als sicher typisierte Dataclass
    return Lesson(
        fach=", ".join([s.name for s in getattr(lesson, 'subjects', [])]),
        lehrer=", ".join([t.name for t in getattr(lesson, 'teachers', [])]),
        klasse=", ".join([k.name for k in getattr(lesson, 'klassen', [])]),
        zeit=f"{start_str} - {lesson.end.strftime('%H:%M')}",
        stunde=stunde_name,
        status_code=getattr(lesson, 'code', None),
        stunden_info=" | ".join(info_parts)
    )

def get_current_lesson(conf: Dict[str, Any]) -> Tuple[Optional[Dict[str, Optional[Lesson]]], str]:
    """
    Hauptfunktion der Daten-Ebene: Verbindet sich mit der WebUntis-API, lädt den
    Tagesplan für den konfigurierten Raum herunter und filtert heraus, was 
    JETZT gerade stattfindet und was DANACH passiert.
    """
    req_keys = ['UNTIS_SERVER', 'UNTIS_USER', 'UNTIS_PASS', 'UNTIS_SCHOOL', 'ROOM_NAME']
    if not conf or any(not conf.get(k) for k in req_keys):
        return None, "Konfiguration unvollständig."
    
    # Lokaler Socket-Timeout (Best Practice)
    # Python-webuntis hat nativ keinen Timeout-Parameter. Bricht das WLAN weg,
    # würde die Funktion ewig blockieren. Wir zwingen sie zum Abbruch nach 30s.
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    
    session = None
    
    try:
        session = webuntis.Session(
            server=conf.get('UNTIS_SERVER'),
            username=conf.get('UNTIS_USER'),
            password=conf.get('UNTIS_PASS'),
            school=conf.get('UNTIS_SCHOOL'),
            useragent='WebUntis-Tuerschild'
        )
        session.login()
        
        rooms = session.rooms().filter(name=conf.get('ROOM_NAME'))
        if not rooms:
            return None, f"Raum {conf.get('ROOM_NAME')} fehlt."
        
        now = get_now()
        today = now.date()
        now_time = now.time()

        # ----------------------------------------------------------------------
        # PÄDAGOGISCHER HINTERGRUND: Ferien-Erkennung & API-Schonung
        # Da sich Ferien nicht stündlich ändern, sparen wir teure API-Aufrufe, 
        # indem wir die Ferientermine für 24 Stunden im RAM cachen.
        # Zudem iterieren wir absichtlich über ALLE Schuljahre (session.schoolyears()), 
        # da Sommerferien exakt zwischen zwei Schuljahren liegen und WebUntis
        # sie bei einer simplen Abfrage sonst oft nicht ausliefert.
        # ----------------------------------------------------------------------
        now_ts = time.time()
        if app_state.cached_holidays is not None and (now_ts - app_state.last_holidays_fetch) < HOLIDAYS_CACHE_SECONDS:
            holidays = app_state.cached_holidays
        else:
            holidays = []
            try:
                for sy in session.schoolyears():
                    holidays.extend(session.holidays(schoolyear=sy))
                app_state.cached_holidays = holidays
                app_state.last_holidays_fetch = now_ts
            except Exception as e:
                logging.warning(f"Ferien-API Iteration fehlgeschlagen: {e}")
                try:
                    # Fallback auf normale Abfrage
                    holidays = session.holidays()
                    app_state.cached_holidays = holidays
                    app_state.last_holidays_fetch = now_ts
                except Exception:
                    pass
                
        for holiday in holidays:
            h_start = holiday.start.date() if isinstance(holiday.start, datetime.datetime) else holiday.start
            h_end = holiday.end.date() if isinstance(holiday.end, datetime.datetime) else holiday.end
            
            if h_start <= today <= h_end:
                return {"current": None, "next": None}, f"Schöne Ferien!\n({holiday.name})"
        
        # Am Wochenende (Samstag=5, Sonntag=6) API schonen
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        try:
            timetable = session.timetable(room=rooms[0], start=today, end=today)
        except Exception as e:
            # WebUntis sperrt oft den Kalender in den Sommerferien hart ab.
            # Statt eines Absturzes werten wir den Error-String aus und zeigen Ferien an.
            err_str = str(e).lower()
            if "schoolyear" in err_str or "schuljahr" in err_str or "no valid" in err_str or "date" in err_str or "notallowed" in err_str:
                return {"current": None, "next": None}, "Unterrichtsfrei!\n(Ferienzeit)"
            logging.error(f"Unerwarteter WebUntis Stundenplan-Fehler: {e}")
            raise e
            
        if not timetable:
            return {"current": None, "next": None}, "Unterrichtsfrei"
            
        # Wir sortieren chronologisch. Fallback auf datetime.max verhindert Abstürze 
        # bei beschädigten WebUntis-Einträgen, die kein Start-Datum haben.
        timetable = sorted(timetable, key=lambda l: getattr(l, 'start', datetime.datetime.max))
        current_lesson = None
        next_lesson = None
        
        for lesson in timetable:
            if getattr(lesson, 'start', None) is None or getattr(lesson, 'end', None) is None:
                continue
                
            # 5-Minuten-Vorlauf: Das Display schaltet bereits 5 Min vor dem Klingeln auf die neue Stunde um
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
            elif lesson.start > now and next_lesson is None:
                next_lesson = lesson

        message = ""
        # Freistunden / Pausen generieren
        if current_lesson is None:
            schedule = conf.get("SCHEDULE", {})
            try:
                ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
                de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
                
                if now_time < datetime.time(ds_h, ds_m):
                    message = "Guten Morgen!"
                elif now_time >= datetime.time(de_h, de_m):
                    message = "Unterrichtsende"
                else:
                    message = "Raum ist frei"
                    # Befindet sich die aktuelle Zeit in einem definierten Pausen-Slot?
                    for b in schedule.get("BREAKS", []):
                        bs_h, bs_m = map(int, str(b.get("start", "00:00")).split(":"))
                        be_h, be_m = map(int, str(b.get("end", "00:00")).split(":"))
                        if datetime.time(bs_h, bs_m) <= now_time < datetime.time(be_h, be_m):
                            message = b.get("name", "Pause")
                            break
            except Exception as e:
                logging.warning(f"Zeit-Parsing Fehler: {e}")
                message = "Raum ist frei"

        return {
            "current": parse_lesson(current_lesson, conf),
            "next": parse_lesson(next_lesson, conf)
        }, message
        
    except Exception as e:
        # PÄDAGOGISCHER HINTERGRUND: Spezifisches Error-Handling
        # Hier geben wir je nach Fehlerbild sprechende Strings an das E-Paper zurück.
        error_msg = str(e)
        logging.error(f"WebUntis API Fehler: {error_msg}")
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg or "timeout" in error_msg.lower():
            return None, "Kein WLAN/Internet"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Untis-Login falsch"
        else:
            return None, "WebUntis offline"
    finally:
        socket.setdefaulttimeout(old_timeout)
        if session:
            try: session.logout()
            except Exception: pass


# ==============================================================================
# 6. DARSTELLUNGS-EBENE: E-PAPER LAYOUT & CANVAS
# ==============================================================================
def get_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """Hilfsfunktion: Berechnet die exakte Pixelbreite eines Textes (wichtig fürs Zentrieren)."""
    try: return int(draw.textlength(text, font=font))
    except AttributeError:
        # Abwärtskompatibilität für ältere Pillow-Versionen auf älteren Linux-Distributionen
        try: return draw.textbbox((0,0), text, font=font)[2] 
        except AttributeError: return draw.textsize(text, font=font)[0] 

def draw_lesson_block(draw: ImageDraw.ImageDraw, lesson: Lesson, y_offset: int, label_text: str, f_small, f_reg, f_med) -> None:
    """
    Zeichnet einen strukturierten Unterrichtsblock (JETZT oder DANACH) als Grafik.
    Wertet die Status-Codes (cancelled = Ausfall, irregular = Vertretung) aus
    und hebt diese farblich durch Invertierung (schwarzer Kasten) hervor.
    """
    header_text = f"{label_text} {lesson.stunde} ({lesson.zeit})"
    draw.text((UI_MARGIN, y_offset), header_text, font=f_small, fill=0) 
    
    status = lesson.status_code
    y_content = y_offset + 16
    
    if status == 'cancelled':
        # Schwarzes Rechteck zeichnen und Schrift weiß färben (fill=255)
        draw.rectangle((UI_MARGIN, y_content, 85, y_content + 18), fill=0)
        draw.text((8, y_content+2), "FÄLLT AUS", font=f_small, fill=255) 
        draw.text((90, y_content), f"{lesson.klasse}", font=f_reg, fill=0)
        
    elif status == 'irregular':
        draw.rectangle((UI_MARGIN, y_content, 90, y_content + 18), fill=0)
        draw.text((8, y_content+2), "VERTRETUNG", font=f_small, fill=255)
        main_info = f"{lesson.fach} | {lesson.klasse} ({lesson.lehrer})"
        draw.text((95, y_content), main_info, font=f_reg, fill=0)
        
    else:
        # Regulärer Unterrichtsblock
        main_info = f"{lesson.fach} | {lesson.klasse} ({lesson.lehrer})"
        draw.text((UI_MARGIN, y_content), main_info, font=f_reg, fill=0)

def update_display_logic(data: Optional[Dict[str, Optional[Lesson]]], message: str, conf: Dict[str, Any]) -> None:
    """
    Erstellt ein 1-Bit (Schwarz/Weiß) Bitmap-Bild des Stundenplans und sendet
    es an den Hardware-Controller des Waveshare E-Paper-Displays.
    """
    if app_state.shutdown_event.is_set(): return 
    message = message or "" 

    if epd2in13_V3 is None: 
        logging.info(f"Display-Update (Simulation): {message}")
        return
        
    # Thread-Lock: Garantiert, dass wir das Display nicht versehentlich 
    # von zwei Threads gleichzeitig flashen (SPI-Kollision).
    with app_state.display_lock: 
        try: 
            epd = epd2in13_V3.EPD()
            epd.init()
            
            # Neues, komplett weißes Bild (255) erzeugen
            image = Image.new('1', (epd.height, epd.width), 255) 
            draw = ImageDraw.Draw(image) 
            
            init_fonts()
            f_mega = app_state.global_fonts['mega']
            f_large = app_state.global_fonts['large']
            f_med = app_state.global_fonts['med']
            f_reg = app_state.global_fonts['reg']
            f_small = app_state.global_fonts['small']

            now = get_now()
            
            # --- KOPFZEILE ---
            draw.rectangle((0, 0, UI_WIDTH, UI_HEADER_HEIGHT), fill=0)
            draw.text((UI_MARGIN, 3), conf.get('ROOM_NAME', 'Unbekannt'), font=f_med, fill=255)
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((120, 5), time_str, font=f_small, fill=255)

            # --- HAUPTBEREICH (Unterricht) ---
            if data and (data.get('current') or data.get('next')):
                curr_lesson = data.get('current')
                next_lesson = data.get('next')
                
                if curr_lesson:
                    draw_lesson_block(draw, curr_lesson, 30, "JETZT:", f_small, f_reg, f_med)
                else:
                    draw.text((UI_MARGIN, 35), message, font=f_large, fill=0)
                
                draw.line((UI_MARGIN, UI_LINE_Y, UI_WIDTH - UI_MARGIN, UI_LINE_Y), fill=0, width=1)
                
                if next_lesson:
                    draw_lesson_block(draw, next_lesson, 74, "DANACH:", f_small, f_reg, f_med)
                else:
                    msg_text = "Kein Unterricht mehr heute." if "Unterrichtsende" not in message else "Bis morgen!"
                    draw.text((UI_MARGIN, 74), "DANACH:", font=f_small, fill=0)
                    draw.text((UI_MARGIN, 90), msg_text, font=f_reg, fill=0)
            
            # --- HAUPTBEREICH (Freistunde / Ferien) ---
            else:
                # Wir handhaben mehrzeilige Strings (\n), damit lange Texte 
                # (wie "Unterrichtsfrei!\n(Ferienzeit)") sauber und mittig auf 
                # das schmale Display passen.
                if "\n" in message:
                    lines = message.split("\n")
                    y_pos = 45
                    for line in lines:
                        text_w = get_text_width(draw, line, f_mega)
                        x_pos = (UI_WIDTH - text_w) / 2 if text_w < UI_WIDTH else 2
                        draw.text((x_pos, y_pos), line, font=f_mega, fill=0)
                        y_pos += 24 
                else:
                    text_w = get_text_width(draw, message, f_mega)
                    x_pos = (UI_WIDTH - text_w) / 2 if text_w < UI_WIDTH else 2
                    draw.text((x_pos, 60), message, font=f_mega, fill=0)

            # Das fertige Bitmap an den Hardware-Controller übertragen
            epd.display(epd.getbuffer(image))
            # EXTREM WICHTIG: Das Display am Ende in den Deep-Sleep schicken!
            # Steht das E-Paper dauerhaft unter Spannung, brennt die E-Tinte ein.
            epd.sleep()
        except Exception as e:
            logging.error(f"Hardware-Fehler (Display): {e}")


# ==============================================================================
# 7. STEUERUNGS-EBENE: HINTERGRUND-LOOP & TEST-ROUTINE
# ==============================================================================
def run_display_test_sequence() -> None:
    """
    Spielt hardcodierte Test-Szenarien nacheinander auf dem Hardware-Display ab.
    Dient zur Überprüfung von Sonderfällen (Ausfall, Vertretung, Lauftext) 
    direkt vor Ort im Flur, ohne reale Plandaten manipulieren zu müssen.
    """
    with app_state.state_lock:
        app_state.test_mode_active = True
        
    conf = get_cached_config()
    
    test_cases = [
        ( {"current": Lesson("Geschichte", "Ab", "9B", "08:00 - 08:45", "1. Std.", None, "Buch auf Seite 12 aufschlagen"),
           "next": Lesson("Informatik", "Cd", "11B", "08:50 - 09:35", "2. Std.", None, "")}, "" ),
        
        ( {"current": Lesson("Religion", "Ef", "7A", "09:55 - 10:40", "3. Std.", "cancelled", "Aufgaben in IServ bearbeiten"),
           "next": Lesson("Geschichte", "Ef", "12", "10:45 - 11:30", "4. Std.", None, "")}, "" ),
        
        ( {"current": Lesson("Werte u. Normen", "Gk", "8C", "11:45 - 12:30", "5. Std.", "irregular", "Achtung: Raumänderung nach In2"),
           "next": None}, "" ),
        
        ( None, "Unterrichtsfrei!\n(Ferienzeit)" ),
        ( None, "Schönes Wochenende!" ),
        ( None, "Kein WLAN/Internet" )
    ]
    
    for idx, (data, msg) in enumerate(test_cases):
        if app_state.shutdown_event.is_set(): break
        with app_state.state_lock:
            app_state.current_display_data = data
            app_state.current_display_msg = f"TESTLAUF ({idx+1}/{len(test_cases)})..."
        
        update_display_logic(data, msg, conf)
        # .wait() statt sleep() nutzen, um den Vorgang bei einem Shutdown abbrechen zu können
        app_state.shutdown_event.wait(4) 
        
    with app_state.state_lock:
        app_state.test_mode_active = False
        app_state.force_update_flag = True

def background_loop() -> None:
    """
    Der Kernprozess (Endlosschleife), der asynchron im Hintergrund läuft.
    Er vergleicht die aktuelle Uhrzeit mit dem Stundenplan und feuert ein 
    Update-Event, wenn eine neue Stunde beginnt oder das Display berührt wurde.
    """
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None

    while not app_state.shutdown_event.is_set():
        with app_state.state_lock:
            is_testing = app_state.test_mode_active
            
        if is_testing:
            app_state.shutdown_event.wait(1)
            continue

        conf = get_cached_config()
        if not conf:
            app_state.shutdown_event.wait(5)
            continue

        schedule = conf.get("SCHEDULE", {})
        lessons_conf = schedule.get("LESSONS", [])
        
        # PÄDAGOGISCH: Wir nutzen 'Sets' anstelle von Listen für die Suchzeiten. 
        # Sets garantieren extrem schnelle Zugriffszeiten (O(1)), was den Pi entlastet.
        dyn_update_times = set() 
        
        if isinstance(lessons_conf, list):
            for l in lessons_conf:
                start_t = l.get("start")
                end_t = l.get("end")
                if start_t: 
                    dyn_update_times.add(start_t)
                    try:
                        # Berechne den 5-Minuten-Vorlauf
                        h, m = map(int, str(start_t).split(":"))
                        dt = datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=5)
                        dyn_update_times.add(dt.strftime("%H:%M"))
                    except Exception: 
                        pass 
                if end_t: 
                    dyn_update_times.add(end_t)
        
        for b in schedule.get("BREAKS", []):
            if b.get("start"): dyn_update_times.add(b.get("start"))
            if b.get("end"): dyn_update_times.add(b.get("end"))
            
        dyn_update_times.add(schedule.get("DAY_START", "07:55"))
        dyn_update_times.add(schedule.get("DAY_END", "15:30"))
        
        now_time_system = time.time() 
        current_dt = get_now()
        current_hm = current_dt.strftime("%H:%M")
        current_time_obj = current_dt.time()
        
        # Laufzeit-Prüfung: Außerhalb der Schulzeiten updaten wir seltener
        try:
            ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
            de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
            active_start = datetime.time(max(0, ds_h - 1), ds_m)
            active_end = datetime.time(min(23, de_h + 1), de_m)
            is_active_hours = active_start <= current_time_obj <= active_end
        except Exception:
            is_active_hours = True 

        # Touch-Erkennung
        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time_system - last_touch_time > TOUCH_COOLDOWN:
                logging.info(f"Display beruehrt! Update wird vorbereitet...")
                with app_state.state_lock:
                    app_state.force_update_flag = True
            last_touch_time = now_time_system

        with app_state.state_lock:
            current_force_update = app_state.force_update_flag
            current_show_demo = app_state.show_demo_once

        # Logik: Update erforderlich?
        is_exact_time = (current_hm in dyn_update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time_system - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900)) and is_active_hours

        # Update ausführen
        if current_force_update or is_interval_reached or is_exact_time:
            if is_exact_time: last_minute_triggered = current_hm 
            is_manual = current_force_update 
            
            with app_state.state_lock:
                app_state.force_update_flag = False
            
            if conf.get('DISPLAY_ACTIVE', True):
                if current_show_demo:
                    data = {
                        "current": Lesson("Informatik", "Ab", "11B", "09:55 - 10:40", "3. Std.", "irregular", "Theorieunterricht - Netzwerktechnik"),
                        "next": Lesson("Geschichte", "Cd", "9B", "10:45 - 11:30", "4. Std.", None, "")
                    }
                    err = ""
                    with app_state.state_lock:
                        app_state.show_demo_once = False
                else:
                    data, err = get_current_lesson(conf)
                
                # Cachen der Ergebnisse für das Webinterface
                with app_state.state_lock:
                    app_state.current_display_data = data
                    app_state.current_display_msg = err

                current_date = current_dt.strftime("%Y-%m-%d")
                is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"] or (isinstance(err, str) and "Ferien" in err)
                
                # E-Paper Schonung: Statische Meldungen (z.B. Ferien) zeichnen wir nur einmal pro Tag neu
                skip_update = False
                if is_static_day and not is_manual:
                    if last_static_date == current_date: skip_update = True
                    else: last_static_date = current_date 
                else: last_static_date = None 
                    
                if not skip_update:
                    update_display_logic(data, err, conf)
            else:
                clear_display_once()
                
            last_update = time.time()
            app_state.shutdown_event.wait(1.5)
            clear_touch_interrupt_via_i2c()
            last_touch_time = time.time()
            
        # Kurze Pause verhindert CPU-Spam (100% Auslastung)
        app_state.shutdown_event.wait(0.5)


# ==============================================================================
# 8. WEB-EBENE: FLASK ADMIN-INTERFACE & ROUTEN
# ==============================================================================
def check_auth(username, password) -> bool:
    """
    Überprüft die HTTP Basic Auth Zugangsdaten.
    
    TECHNISCHER HINTERGRUND (Kryptographie):
    Passwörter sollten niemals im Klartext gespeichert werden! 
    Wir nutzen 'werkzeug.security', um das Klartext-Passwort aus der config.json 
    beim ersten Start einmalig in einen Einweg-Hash umzuwandeln (Auto-Migration). 
    Selbst wenn Hacker die SD-Karte stehlen, sehen sie nur den Hash.
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

    return username == u and check_password_hash(saved_pass, password)

def authenticate():
    """Gibt den HTTP 401 Fehler (Unauthorized) an den Browser zurück, der daraufhin nach Passwörtern fragt."""
    return Response(
    'Zugriff verweigert. Bitte korrekte Zugangsdaten eingeben.\n', 401,
    {'WWW-Authenticate': 'Basic realm="Tuerschild Admin-Bereich"'})

def requires_auth(f):
    """
    Decorator (@requires_auth) für alle geschützten Flask-Routen.
    Bevor Flask die aufgerufene Route (z.B. /save) ausführt, läuft dieser Code ab.
    So muss die Passwortprüfung nicht händisch in jeder Route wiederholt werden.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
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
        if not token or token != app_state.csrf_token:
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
                            <label>Intervall (Sekunden)</label>
                            <input type="number" name="AUTO_UPDATE_SECONDS" value="{{ conf.get('AUTO_UPDATE_SECONDS', 900) }}" min="60">
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
                                        {{ data.current.fach }} <span style="color: #cbd5e1; margin: 0 4px;">|</span> {{ data.current.klasse }}
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
                                        {{ data.next.fach }} <span style="color: #cbd5e1; margin: 0 4px;">|</span> {{ data.next.klasse }}
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
                                {{ msg | replace('\n', '<br>') | safe }}
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
        d_msg = app_state.current_display_msg
        c_token = app_state.csrf_token
        
    display_time = get_now().strftime("%d.%m.%Y %H:%M:%S")

    return render_template_string(
        HTML_TEMPLATE, 
        conf=conf, 
        data=d_data, 
        msg=d_msg, 
        now=display_time,
        sim_active=is_simulated,
        csrf_token=c_token
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
            val = int(request.form.get('AUTO_UPDATE_SECONDS', 900))
            conf['AUTO_UPDATE_SECONDS'] = max(60, min(val, 86400)) 
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
# 9. START-EBENE: HAUPTPROGRAMM (ENTRY POINT)
# ==============================================================================
if __name__ == '__main__':
    try:
        if GPIO:
            try:
                # Hardware Setup: I2C-Pin des Touch-Panels vorbereiten
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(TOUCH_RST_PIN, GPIO.OUT)
                GPIO.output(TOUCH_RST_PIN, GPIO.LOW)
                time.sleep(0.1)
                GPIO.output(TOUCH_RST_PIN, GPIO.HIGH)
                time.sleep(0.2)
                clear_touch_interrupt_via_i2c()
                logging.info("Kapazitives Touch-Display initialisiert.")
            except OSError as e:
                logging.debug(f"GPIO Setup Fehler: {e}")

        # Schriftarten vorab in den RAM laden (Optimierung)
        init_fonts()

        # Hintergrundschleife für API-Pulls als asynchronen Dämonen-Thread starten
        threading.Thread(target=background_loop, daemon=True).start()
            
        logging.info(f" * Admin-Interface (Localhost): http://127.0.0.1:5000")
        
        # Flasks eingebauter Server ist nicht netzwerksicher. Daher wickelt 
        # 'Waitress' als robuster WSGI-Server die HTTP-Requests ab.
        serve(app, host='127.0.0.1', port=5000)
        
    except KeyboardInterrupt:
        # Fängt das STRG+C Signal des Nutzers im Terminal ab
        app_state.shutdown_event.set()
    finally:
        # Wird immer ausgeführt (auch bei Abstürzen oder beim sudo-Reboot), 
        # um die Hardware sicher herunterzufahren und Ressourcen freizugeben.
        app_state.shutdown_event.set()
        if GPIO: GPIO.cleanup()
        
        if epd2in13_V3 is not None:
            # 5 Sekunden Timeout verhindern, dass das Skript endlos hängt (Deadlock)
            if app_state.display_lock.acquire(timeout=5):
                try:
                    epd = epd2in13_V3.EPD()
                    epd.init()
                    epd.Clear(0xFF)
                    epd.sleep()  # Deep-Sleep (Bewahrt die Tinte vor dem Einbrennen)
                    epd2in13_V3.epdconfig.module_exit()
                except OSError as e: 
                    logging.debug(f"Fehler beim finalen Display-Clear: {e}")
                finally:
                    app_state.display_lock.release()
            else:
                logging.error("WARNUNG: Display-Lock konnte beim Beenden nicht erlangt werden.")
        sys.exit(0)
