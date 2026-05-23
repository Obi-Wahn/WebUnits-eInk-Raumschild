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

Pädagogischer Fokus: Dieser Code ist so strukturiert, dass er im 
Informatikunterricht als Praxisbeispiel für Nebenläufigkeit (Multithreading), 
Ressourcen-Sperren (Locks), Kryptographie (Hashing), Responsives Webdesign
(CSS Grid) und defensives Programmieren (Graceful Degradation) dienen kann.
"""

# ==============================================================================
# 1. IMPORTS & HARDWARE-SETUP
# ==============================================================================
import sys
import os            # Für Dateipfad-Operationen
import time
import datetime
import json
import threading     # Für asynchrone Prozesse (Hintergrund-Schleife)
import socket        # Für Netzwerk-Timeouts
import tempfile      # Für sicheres (atomares) Speichern von Dateien
import subprocess    # Für absolute, sichere System-Aufrufe an das Linux-OS
import webuntis      # Die offizielle WebUntis API-Schnittstelle
from functools import wraps
from flask import Flask, render_template_string, request, redirect, Response
from waitress import serve  # Ein sicherer, produktionsreifer WSGI-Webserver
from werkzeug.security import generate_password_hash, check_password_hash # Kryptographie
from PIL import Image, ImageDraw, ImageFont # Pillow: Für die Bildgenerierung

# ------------------------------------------------------------------------------
# Hardware-Schnittstellen (GPIO & I2C) laden. 
# 
# PÄDAGOGISCHER HINTERGRUND: "Graceful Degradation" (Gutmütiges Herabstufen)
# Try-Except-Blöcke ermöglichen es, das Skript auch auf einem normalen 
# Windows/Mac-Rechner zu testen und weiterzuentwickeln. Fehlt die Raspberry-
# Hardware, fangen wir den Fehler ab und loggen ihn, anstatt abzustürzen.
# ------------------------------------------------------------------------------
try:
    import RPi.GPIO as GPIO
except ImportError as e:
    print(f"Warnung: RPi.GPIO ist nicht installiert. Entwicklungsmodus? ({e})")
    GPIO = None

try:
    import smbus2 as smbus
    i2c_bus = smbus.SMBus(1)
except ImportError as e:
    print(f"Warnung: smbus2 ist nicht installiert. Entwicklungsmodus? ({e})")
    smbus = None
    i2c_bus = None

# Pfad zu den E-Paper-Treibern des Herstellers (Waveshare) dynamisch hinzufügen
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

try:
    from waveshare_epd import epd2in13_V3
except ImportError as e:
    print(f"Warnung: waveshare_epd Treiber nicht gefunden. ({e})")
    epd2in13_V3 = None


# ==============================================================================
# 2. KONSTANTEN & GLOBALE VARIABLEN (ZUSTAND/STATE)
# ==============================================================================
app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')

# Hardware-Pins & Konstanten
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14                # Hexadezimale Adresse des Touch-Controllers auf dem I2C-Bus
TOUCH_COOLDOWN = 5.0                 # Entprell-Zeit für das Touch-Display in Sekunden

# Steuerungsvariablen für die Kommunikation zwischen Webserver und Hintergrund-Loop
force_update_flag = True             # Startet auf True, damit beim Booten direkt ein Update erfolgt
show_demo_once = False               # Schalter für den Präsentations-Modus (zeigt simulierte Daten)
test_mode_active = False             # Blockiert reguläre Updates, solange der Display-Test läuft
shutdown_event = threading.Event()   # Thread-sicheres Signal zum sauberen Beenden des Programms (STRG+C)

# ------------------------------------------------------------------------------
# THREADING-LOCKS (Schutz vor "Race Conditions")
# 
# PÄDAGOGISCHER HINTERGRUND: Hier laufen Threads parallel (Flask-Webserver vs. 
# Endlosschleife). Ein "Lock" (Mutex) wirkt wie ein Schlüssel zu einem Raum: 
# Wer den Schlüssel hat, darf arbeiten. Der andere Thread wartet an der Tür.
# Fehlen die Locks, entstehen "Race Conditions", bei denen Variablen mit 
# inkonsistenten Werten überschrieben werden oder das SPI-Display abstürzt.
# ------------------------------------------------------------------------------
display_lock = threading.Lock()      # Schützt das Hardware-Display vor simultanen Schreibzugriffen
state_lock = threading.Lock()        # Schützt unsere globalen Steuerungs-Flags
config_lock = threading.Lock()       # Schützt das Lesen/Schreiben der Konfigurationsdatei

# Variablen für die Zeit-Simulation (Time-Travel-Feature für das Testen im Webinterface)
simulated_datetime = None

# Globaler Cache, damit das Webinterface nicht bei jedem Neuladen die API abfragt
current_display_data = None
current_display_msg = "Warte auf erstes Update..."

# Performance-Caching für Dateien
_cached_config = {}
_last_config_mtime = 0
GLOBAL_FONTS = {}


# ==============================================================================
# 3. KONFIGURATIONS-VERWALTUNG & CACHING
# ==============================================================================
def get_cached_config():
    """
    Lädt die Config nur neu, wenn sie sich auf der SD-Karte geändert hat.
    
    PÄDAGOGISCHER HINTERGRUND (I/O-Bottleneck & Thread-Safety): 
    Ständiger Festplattenzugriff ist extrem langsam. Wir cachen die Datei.
    Das 'with config_lock' verhindert eine Race Condition, falls Flask und der 
    Hintergrund-Loop in exakt derselben Millisekunde auf die Datei zugreifen wollen.
    """
    global _cached_config, _last_config_mtime
    
    with config_lock:
        if not os.path.exists(CONFIG_FILE): return {}
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
            if mtime > _last_config_mtime:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    _cached_config = json.loads(content) if content else {}
                _last_config_mtime = mtime
        except Exception as e:
            print(f"FEHLER beim Laden der config.json: {e}")
        
        # Ein echtes Dictionary als Kopie zurückgeben, um Pointer-Probleme zu vermeiden
        return dict(_cached_config)

def save_config(config):
    """
    Speichert Einstellungen stromausfallsicher ab.
    
    PÄDAGOGISCHER HINTERGRUND (Atomare Operationen):
    Würde der Raspberry Pi während 'open(file, w)' den Strom verlieren, wäre 
    die Datei korrupt (0 Byte). Wir schreiben daher erst in eine temporäre Datei 
    und tauschen sie am Ende nahtlos (atomar) auf Betriebssystemebene aus.
    """
    global _last_config_mtime
    
    with config_lock:
        try:
            dir_name = os.path.dirname(CONFIG_FILE)
            fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            os.replace(temp_path, CONFIG_FILE)
            
            # Den RAM-Cache sofort invalidieren
            _last_config_mtime = 0 
        except Exception as e:
            print(f"FEHLER beim Speichern der config.json: {e}")


# ==============================================================================
# 3.5 ZENTRALE UHR (Abstraktion der Zeit für Testzwecke)
# ==============================================================================
def get_now() -> datetime.datetime:
    """
    Gibt das aktuelle Datum/Uhrzeit zurück (inklusive Simulations-Logik).
    """
    global simulated_datetime
    with state_lock:
        if simulated_datetime:
            return simulated_datetime
    return datetime.datetime.now()


# ==============================================================================
# 4. HARDWARE-EBENE (TOUCH, DISPLAY RESET & FONTS)
# ==============================================================================
def init_fonts():
    """
    Lädt die Schriftarten beim Programmstart einmalig in den RAM (Lazy Loading).
    Verhindert langsame Festplattenzugriffe bei jedem Display-Refresh.
    """
    global GLOBAL_FONTS
    if GLOBAL_FONTS: return 
    try: 
        GLOBAL_FONTS['mega'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
        GLOBAL_FONTS['huge'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
        GLOBAL_FONTS['large'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18) 
        GLOBAL_FONTS['med'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
        GLOBAL_FONTS['reg'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
        GLOBAL_FONTS['small'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    except Exception as e:
        print(f"Schriftarten nicht gefunden, nutze Fallback. ({e})")
        default = ImageFont.load_default()
        GLOBAL_FONTS = {k: default for k in ['mega', 'huge', 'large', 'med', 'reg', 'small']}

def check_touch_via_i2c():
    """Prüft direkt über den I2C-Bus, ob ein kapazitives Touch-Event anliegt."""
    if not i2c_bus or not smbus: return False
    try:
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        
        # Bit-Maskierung: Wenn das höchste Bit (0x80) gesetzt ist, gibt es einen Touch
        if list(read_msg)[0] & 0x80:
            # Quittungssignal senden, damit der Chip seinen internen Alarm zurücksetzt
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except Exception: 
        pass 
    return False

def clear_touch_interrupt_via_i2c():
    """Setzt den Touch-Chip manuell zurück (z.B. beim Bootvorgang)."""
    if not i2c_bus: return
    try: 
        i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except Exception as e: 
        print(f"I2C Reset Fehler: {e}")

def clear_display_once():
    """Löscht das E-Paper-Display komplett weiß."""
    if shutdown_event.is_set(): return
    if epd2in13_V3 is None: return 
    
    with display_lock:
        try:
            epd = epd2in13_V3.EPD()
            epd.init()
            epd.Clear(0xFF)
            epd.sleep()
        except Exception as e: 
            print(f"Display Clear Fehler: {e}")


# ==============================================================================
# 5. DATEN-EBENE: WEBUNTIS API
# ==============================================================================
def parse_lesson(lesson, conf):
    """
    Hilfsfunktion: Nimmt ein komplexes, rohes WebUntis-Klassenobjekt und 
    extrahiert genau die Strings, die wir für das Display brauchen.
    """
    # Sicherheit (Input Validation): Falls ein kaputtes Objekt übergeben wird
    if not lesson or not getattr(lesson, 'start', None) or not getattr(lesson, 'end', None): 
        return None
    
    schedule = conf.get("SCHEDULE", {})
    lessons_conf = schedule.get("LESSONS", [])
    
    start_str = lesson.start.strftime("%H:%M")
    stunde_name = ""
    
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
    
    stunden_info = " | ".join(info_parts)

    return {
        "fach": ", ".join([s.name for s in getattr(lesson, 'subjects', [])]),
        "lehrer": ", ".join([t.name for t in getattr(lesson, 'teachers', [])]),
        "klasse": ", ".join([k.name for k in getattr(lesson, 'klassen', [])]),
        "zeit": f"{start_str} - {lesson.end.strftime('%H:%M')}",
        "stunde": stunde_name,
        "status_code": getattr(lesson, 'code', None),
        "stunden_info": stunden_info 
    }

def get_current_lesson(conf):
    """Verbindet sich mit der WebUntis-API, holt den Tagesplan und filtert ihn."""
    
    # Input Validation: Niemals API-Calls starten, wenn Pflichtdaten fehlen!
    req_keys = ['UNTIS_SERVER', 'UNTIS_USER', 'UNTIS_PASS', 'UNTIS_SCHOOL', 'ROOM_NAME']
    if not conf or any(not conf.get(k) for k in req_keys):
        return None, "Konfiguration unvollständig."
    
    session = None
    # HINWEIS: python-webuntis unterstützt nativ keinen Timeout-Parameter. 
    # Wir setzen daher notgedrungen den globalen Socket-Timeout, um bei WLAN-Ausfällen
    # Endlos-Hänger zu vermeiden.
    socket.setdefaulttimeout(30) 
    
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
        
        # Am Wochenende API-Schonung
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        timetable = session.timetable(room=rooms[0], start=today, end=today)
        if not timetable:
            return {"current": None, "next": None}, "Unterrichtsfrei"
            
        # PÄDAGOGISCHER HINTERGRUND (Lambda-Funktionen):
        # Wir sortieren chronologisch. Die anonyme Lambda-Funktion nutzt 'start' 
        # als Sortier-Kriterium. Fallback auf datetime.max verhindert Abstürze bei None.
        timetable = sorted(timetable, key=lambda l: getattr(l, 'start', datetime.datetime.max))
        current_lesson = None
        next_lesson = None
        
        for lesson in timetable:
            if getattr(lesson, 'start', None) is None or getattr(lesson, 'end', None) is None:
                continue
                
            # 5-Minuten-Vorlauf: Das Display schaltet bereits 5 Min vor dem Klingeln um
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
            elif lesson.start > now and next_lesson is None:
                next_lesson = lesson

        message = ""
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
                    for b in schedule.get("BREAKS", []):
                        bs_h, bs_m = map(int, str(b.get("start", "00:00")).split(":"))
                        be_h, be_m = map(int, str(b.get("end", "00:00")).split(":"))
                        if datetime.time(bs_h, bs_m) <= now_time < datetime.time(be_h, be_m):
                            message = b.get("name", "Pause")
                            break
            except Exception as e:
                print(f"Zeit-Parsing Fehler: {e}")
                message = "Raum ist frei"

        return {
            "current": parse_lesson(current_lesson, conf),
            "next": parse_lesson(next_lesson, conf)
        }, message
        
    except Exception as e:
        # Graceful Degradation: Sprechende Fehlermeldungen für das E-Paper
        error_msg = str(e)
        print(f"WebUntis API Fehler: {error_msg}")
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg or "timeout" in error_msg.lower():
            return None, "Kein WLAN/Internet"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Untis-Login falsch"
        else:
            return None, "WebUntis offline"
    finally:
        if session:
            try: session.logout()
            except Exception: pass


# ==============================================================================
# 6. DARSTELLUNGS-EBENE: E-PAPER LAYOUT & CANVAS
# ==============================================================================
def get_text_width(draw, text, font):
    """
    Hilfsfunktion zur Abwärtskompatibilität für verschiedene Pillow-Versionen.
    Vermeidet Abstürze auf älteren oder brandneuen Betriebssystemen.
    """
    try: return draw.textlength(text, font=font) 
    except AttributeError:
        try: return draw.textbbox((0,0), text, font=font)[2] 
        except AttributeError: return draw.textsize(text, font=font)[0] 

def draw_lesson_block(draw, lesson_data, y_offset, label_text, f_small, f_reg, f_med):
    """Zeichnet einen einzelnen Unterrichtsblock (JETZT oder DANACH) als Grafik."""
    header_text = f"{label_text} {lesson_data['stunde']} ({lesson_data['zeit']})"
    draw.text((5, y_offset), header_text, font=f_small, fill=0) 
    
    status = lesson_data.get('status_code')
    y_content = y_offset + 16
    
    # Priorität 1: Ausfall (Invertierter schwarzer Block für hohe Sichtbarkeit)
    if status == 'cancelled':
        draw.rectangle((5, y_content, 85, y_content + 18), fill=0)
        draw.text((8, y_content+2), "FÄLLT AUS", font=f_small, fill=255) 
        draw.text((90, y_content), f"{lesson_data['klasse']}", font=f_reg, fill=0)
        
    # Priorität 2: Vertretung (Invertiertes Label)
    elif status == 'irregular':
        draw.rectangle((5, y_content, 90, y_content + 18), fill=0)
        draw.text((8, y_content+2), "VERTRETUNG", font=f_small, fill=255)
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((95, y_content), main_info, font=f_reg, fill=0)
        
    # Standard-Darstellung
    else:
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((5, y_content), main_info, font=f_reg, fill=0)

def update_display_logic(data, message, conf):
    """Baut das komplette visuelle Layout zusammen und flasht es auf das E-Paper."""
    if shutdown_event.is_set(): return 
    message = message or "" 

    if epd2in13_V3 is None: 
        print(f"INFO: Display-Update (Simulation): {message}")
        return
        
    with display_lock: 
        try: 
            epd = epd2in13_V3.EPD()
            epd.init()
            
            image = Image.new('1', (epd.height, epd.width), 255) 
            draw = ImageDraw.Draw(image) 
            
            init_fonts()
            f_mega = GLOBAL_FONTS['mega']
            f_large = GLOBAL_FONTS['large']
            f_med = GLOBAL_FONTS['med']
            f_reg = GLOBAL_FONTS['reg']
            f_small = GLOBAL_FONTS['small']

            now = get_now()
            
            # KOPFZEILE
            draw.rectangle((0, 0, 250, 24), fill=0)
            draw.text((5, 3), conf.get('ROOM_NAME', 'Unbekannt'), font=f_med, fill=255)
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((120, 5), time_str, font=f_small, fill=255)

            # HAUPTBEREICH
            if data and isinstance(data, dict) and (data.get('current') or data.get('next')):
                curr_lesson = data.get('current')
                next_lesson = data.get('next')
                
                if curr_lesson:
                    draw_lesson_block(draw, curr_lesson, 30, "JETZT:", f_small, f_reg, f_med)
                else:
                    draw.text((5, 35), message, font=f_large, fill=0)
                
                draw.line((5, 68, 245, 68), fill=0, width=1)
                
                if next_lesson:
                    draw_lesson_block(draw, next_lesson, 74, "DANACH:", f_small, f_reg, f_med)
                else:
                    msg_text = "Kein Unterricht mehr heute." if "Unterrichtsende" not in message else "Bis morgen!"
                    draw.text((5, 74), "DANACH:", font=f_small, fill=0)
                    draw.text((5, 90), msg_text, font=f_reg, fill=0)
            else:
                text_w = get_text_width(draw, message, f_mega)
                x_pos = (250 - text_w) / 2 if text_w < 250 else 2
                draw.text((x_pos, 60), message, font=f_mega, fill=0)

            # Das fertige Bitmap an den E-Paper-Controller senden
            epd.display(epd.getbuffer(image))
            # WICHTIG: Display schlafen legen, um Lebensdauer zu erhalten und Strom zu sparen
            epd.sleep()
        except Exception as e:
            print(f"Hardware-Fehler (Display): {e}")


# ==============================================================================
# 7. STEUERUNGS-EBENE: HINTERGRUND-LOOP & TEST-ROUTINE
# ==============================================================================
def run_display_test_sequence():
    """Spielt Test-Szenarien nacheinander auf dem Hardware-Display ab."""
    global test_mode_active, current_display_data, current_display_msg, force_update_flag
    
    with state_lock:
        test_mode_active = True
        
    conf = get_cached_config()
    
    # Hartcodierte Test-Daten, didaktisch orientiert an deinen Unterrichtsfächern
    test_cases = [
        ( {"current": {"fach": "Geschichte", "lehrer": "Ab", "klasse": "9B", "zeit": "08:00 - 08:45", "stunde": "1. Std.", "status_code": None, "stunden_info": "Buch auf Seite 12 aufschlagen"},
           "next": {"fach": "Informatik", "lehrer": "Cd", "klasse": "11B", "zeit": "08:50 - 09:35", "stunde": "2. Std.", "status_code": None, "stunden_info": ""}}, "" ),
        
        ( {"current": {"fach": "Religion", "lehrer": "Ef", "klasse": "7A", "zeit": "09:55 - 10:40", "stunde": "3. Std.", "status_code": "cancelled", "stunden_info": "Aufgaben in IServ bearbeiten"},
           "next": {"fach": "Geschichte", "lehrer": "Ef", "klasse": "12", "zeit": "10:45 - 11:30", "stunde": "4. Std.", "status_code": None, "stunden_info": ""}}, "" ),
        
        ( {"current": {"fach": "Werte u. Normen", "lehrer": "Gk", "klasse": "8C", "zeit": "11:45 - 12:30", "stunde": "5. Std.", "status_code": "irregular", "stunden_info": "Achtung: Raumänderung nach In2"},
           "next": None}, "" ),
        
        ( None, "Schönes Wochenende!" ),
        ( None, "Kein WLAN/Internet" )
    ]
    
    for idx, (data, msg) in enumerate(test_cases):
        if shutdown_event.is_set(): break
        with state_lock:
            current_display_data = data
            current_display_msg = f"TESTLAUF ({idx+1}/{len(test_cases)})..."
        
        update_display_logic(data, msg, conf)
        
        # PÄDAGOGISCHER HINTERGRUND: shutdown_event.wait(4) statt time.sleep(4).
        # Schützt vor Blockaden beim Herunterfahren des Programms (Graceful Exit).
        shutdown_event.wait(4) 
        
    with state_lock:
        test_mode_active = False
        force_update_flag = True

def background_loop():
    """Der Kernprozess, der asynchron Zeiten vergleicht und Updates ausführt."""
    global force_update_flag, show_demo_once, current_display_data, current_display_msg, test_mode_active
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None

    while not shutdown_event.is_set():
        with state_lock:
            is_testing = test_mode_active
            
        if is_testing:
            shutdown_event.wait(1)
            continue

        conf = get_cached_config()
        if not conf:
            shutdown_event.wait(5)
            continue

        schedule = conf.get("SCHEDULE", {})
        lessons_conf = schedule.get("LESSONS", [])
        
        # PÄDAGOGISCH: Schnelle Suchvorgänge O(1) mit 'Sets' statt langsamer 'Listen'
        dyn_update_times = set() 
        
        if isinstance(lessons_conf, list):
            for l in lessons_conf:
                start_t = l.get("start")
                end_t = l.get("end")
                if start_t: 
                    dyn_update_times.add(start_t)
                    try:
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
        
        try:
            ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
            de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
            active_start = datetime.time(max(0, ds_h - 1), ds_m)
            active_end = datetime.time(min(23, de_h + 1), de_m)
            is_active_hours = active_start <= current_time_obj <= active_end
        except Exception:
            is_active_hours = True 

        # Logik: Haben wir einen exakten Treffer oder ist der Intervall abgelaufen?
        is_exact_time = (current_hm in dyn_update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time_system - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900)) and is_active_hours

        # Hardware Touch-Logik
        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time_system - last_touch_time > TOUCH_COOLDOWN:
                print(f"\n[TOUCH {datetime.datetime.now().strftime('%H:%M:%S')}] Display beruehrt! Update wird vorbereitet...")
                with state_lock:
                    force_update_flag = True
            last_touch_time = now_time_system

        with state_lock:
            current_force_update = force_update_flag
            current_show_demo = show_demo_once

        if current_force_update or is_interval_reached or is_exact_time:
            if is_exact_time: last_minute_triggered = current_hm 
            
            is_manual = current_force_update 
            
            with state_lock:
                force_update_flag = False
            
            if conf.get('DISPLAY_ACTIVE', True):
                if current_show_demo:
                    data = {
                        "current": {"fach": "Informatik", "lehrer": "Ab", "klasse": "11B", "zeit": "09:55 - 10:40", "stunde": "3. Std.", "status_code": "irregular", "stunden_info": "Theorieunterricht - Netzwerktechnik"},
                        "next": {"fach": "Geschichte", "lehrer": "Cd", "klasse": "9B", "zeit": "10:45 - 11:30", "stunde": "4. Std.", "status_code": None, "stunden_info": ""}
                    }
                    err = ""
                    with state_lock:
                        show_demo_once = False
                else:
                    data, err = get_current_lesson(conf)
                
                with state_lock:
                    current_display_data = data
                    current_display_msg = err

                current_date = current_dt.strftime("%Y-%m-%d")
                is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"]
                
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
            time.sleep(1.5)
            clear_touch_interrupt_via_i2c()
            last_touch_time = time.time()
            
        shutdown_event.wait(0.5)


# ==============================================================================
# 8. WEB-EBENE: FLASK ADMIN-INTERFACE & ROUTEN
# ==============================================================================
def check_auth(username, password):
    """
    Überprüft die Benutzerdaten und hasht Passwörter bei Bedarf transparent.
    
    PÄDAGOGISCHER HINTERGRUND (Kryptographie):
    Passwörter niemals im Klartext speichern! Wir nutzen 'werkzeug.security', 
    um das Klartext-Passwort aus der config.json einmalig in einen Einweg-Hash 
    (scrypt/pbkdf2) umzuwandeln. Selbst wenn die SD-Karte ausgelesen wird, 
    kann man das Passwort nicht mehr zurückrechnen.
    """
    conf = get_cached_config()
    u = conf.get('ADMIN_USER', 'admin')
    saved_pass = conf.get('ADMIN_PASS', 'tuerschild')
    
    if not saved_pass.startswith('scrypt:') and not saved_pass.startswith('pbkdf2:'):
        # Auto-Hashing: Das Passwort liegt noch unverschlüsselt vor.
        print("INFO: Klartext-Passwort entdeckt. Wird verschlüsselt und gespeichert...")
        hashed_pass = generate_password_hash(saved_pass)
        conf['ADMIN_PASS'] = hashed_pass
        save_config(conf)
        saved_pass = hashed_pass

    return username == u and check_password_hash(saved_pass, password)

def authenticate():
    return Response(
    'Zugriff verweigert. Bitte korrekte Zugangsdaten eingeben.\n', 401,
    {'WWW-Authenticate': 'Basic realm="Tuerschild Admin-Bereich"'})

def requires_auth(f):
    """
    Decorator für alle geschützten URLs. Verhindert unberechtigten Zugriff.
    PÄDAGOGISCHER HINTERGRUND: Decorator (das @-Symbol) wrappen eine Funktion.
    Bevor Flask die Route (z.B. /save) ausführt, läuft erst dieser Code ab,
    der das Passwort prüft.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# PÄDAGOGISCHER HINTERGRUND (HTML Inline, CSS Flexbox & CSS Grid):
# In großen Projekten lagert man HTML in einen /templates Ordner aus.
# Für absolute Portabilität behalten wir das Template hier als String.
# Die HTML-Struktur ist aufgeteilt: 1. Controls(Top), 2. Preview, 3. Controls(Bottom).
# Dadurch fließt das Layout auf dem Smartphone (Flexbox 'column') in exakt dieser Reihenfolge.
# Auf dem Desktop greift das CSS Grid (@media) und ordnet die Controls links (1. und 2. Zeile) 
# und die Preview rechts (über beide Zeilen gespannt) an.
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
        
        /* Mobile First: Flexbox für die logische vertikale Reihenfolge */
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

        /* Media Query für Desktops/Tablets im Querformat */
        @media (min-width: 800px) {
            body { padding: 40px 20px; }
            .card { margin-top: 0; }
            
            /* CSS Grid reassembliert die Blöcke in ein 2-Spalten-Layout */
            .dashboard-grid { 
                display: grid; 
                grid-template-columns: 1fr 1fr; 
                gap: 40px; 
                align-items: start; /* Verhindert das automatische Strecken der Spalten */
            }
            .col-controls-top { grid-column: 1; grid-row: 1; }
            .col-controls-bottom { grid-column: 1; grid-row: 2; }
            .col-preview { 
                grid-column: 2; 
                grid-row: 1 / span 2; /* Die Vorschau-Spalte spannt sich über beide Zeilen auf der rechten Seite */
                margin-top: 0; 
                margin-bottom: 0; 
                background-color: #f8fafc; 
                padding: 25px; 
                border-radius: 15px; 
                border: 2px dashed #e2e8f0; 
            }
            
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
                
                <!-- TEIL 1: Steuerung & Einstellungen (Mobil: oben, Desktop: links oben) -->
                <div class="col-controls-top">
                    <div class="section-title">Gerätesteuerung</div>
                    <div class="btn-group">
                        <form action="/update" method="POST" class="inline-form btn-full">
                            <button type="submit" class="btn btn-update">Manuelles Update</button>
                        </form>
                        
                        <form action="/toggle" method="POST" class="inline-form">
                            <button type="submit" class="btn {% if conf.get('DISPLAY_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                                {% if conf.get('DISPLAY_ACTIVE', True) %}Display aus{% else %}Display an{% endif %}
                            </button>
                        </form>
                        
                        <form action="/toggle_touch" method="POST" class="inline-form">
                            <button type="submit" class="btn {% if conf.get('TOUCH_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                                {% if conf.get('TOUCH_ACTIVE', True) %}Touch aus{% else %}Touch an{% endif %}
                            </button>
                        </form>
                    </div>
                    
                    <div class="section-title">Einstellungen</div>
                    <form action="/save" method="POST">
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
                
                <!-- TEIL 2: Live-Vorschau (Mobil: Mitte, Desktop: rechts) -->
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
                            <div class="empty-state" style="font-size: 16px; padding: 30px 20px;">{{ msg }}</div>
                        {% endif %}
                    </div>
                </div>
                
                <!-- TEIL 3: Test & System (Mobil: unten, Desktop: links unten) -->
                <div class="col-controls-bottom">
                    <div class="section-title">Test & Simulation</div>
                    <div style="background: #f8fafc; border-radius: 10px; padding: 15px; margin-bottom: 15px; border: 1px solid #e2e8f0;">
                        <label>Datum & Uhrzeit simulieren</label>
                        <form action="/simulate_time" method="POST" style="margin-bottom: 10px;">
                            <input type="datetime-local" name="SIM_TIME" required style="margin-bottom: 10px;">
                            <button type="submit" class="btn btn-test">Zeit simulieren</button>
                        </form>
                        <form action="/reset_time" method="POST" class="inline-form">
                            <button type="submit" class="btn btn-update">Zurück zur Echtzeit</button>
                        </form>
                    </div>
                    
                    <div class="btn-group">
                        <form action="/demo" method="POST" class="inline-form btn-full">
                            <button type="submit" class="btn btn-demo">Lokale Dummy-Daten laden</button>
                        </form>
                        <form action="/test_all" method="POST" class="inline-form btn-full">
                            <button type="submit" class="btn btn-test" style="background-color: #0f172a;">Display-Testlauf (ca. 30 Sek)</button>
                        </form>
                    </div>

                    <div class="section-title">System</div>
                    <div class="btn-group">
                        <!-- PÄDAGOGISCHER HINTERGRUND: onsubmit führt eine clientseitige JS-Validierung (confirm) aus -->
                        <form action="/sys_reboot" method="POST" class="inline-form btn-full" onsubmit="return confirm('Raspberry Pi wirklich neu starten? Das E-Paper wird kurz abgeschaltet.');">
                            <button type="submit" class="btn btn-test" style="background-color: #475569;">System Neustart</button>
                        </form>
                        <form action="/sys_shutdown" method="POST" class="inline-form btn-full" onsubmit="return confirm('ACHTUNG: Raspberry Pi wirklich herunterfahren? Er muss danach manuell vom Strom getrennt und wieder verbunden werden, um neu zu starten!');">
                            <button type="submit" class="btn btn-off" style="background-color: #94a3b8; color: #0f172a;">System Herunterfahren</button>
                        </form>
                    </div>
                </div>

            </div> <!-- Ende dashboard-grid -->
            
            <p class="footer">Status: {{ now }}{% if sim_active %} <br><strong style="color: #dc2626;">(ZEIT WIRD SIMULIERT)</strong>{% endif %}</p>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
@requires_auth
def index():
    conf = get_cached_config()
    global simulated_datetime
    
    with state_lock:
        is_simulated = simulated_datetime is not None
        d_data = current_display_data
        d_msg = current_display_msg
        
    display_time = get_now().strftime("%d.%m.%Y %H:%M:%S")

    # Jinja2-Template-Engine füllt unsere HTML-Vorlage mit den aktuellen Variablen auf
    return render_template_string(
        HTML_TEMPLATE, 
        conf=conf, 
        data=d_data, 
        msg=d_msg, 
        now=display_time,
        sim_active=is_simulated
    )

@app.route('/simulate_time', methods=['POST'])
@requires_auth
def simulate_time():
    global simulated_datetime, force_update_flag
    sim_time_str = request.form.get('SIM_TIME')
    if sim_time_str:
        try:
            parsed_time = datetime.datetime.strptime(sim_time_str, "%Y-%m-%dT%H:%M")
            with state_lock:
                simulated_datetime = parsed_time
                force_update_flag = True
        except Exception as e:
            print(f"Fehler beim Parsen der Simulationszeit: {e}")
    return redirect('/')

@app.route('/reset_time', methods=['POST'])
@requires_auth
def reset_time():
    global simulated_datetime, force_update_flag
    with state_lock:
        simulated_datetime = None
        force_update_flag = True
    return redirect('/')

@app.route('/save', methods=['POST'])
@requires_auth
def save():
    global force_update_flag
    conf = get_cached_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        try:
            val = int(request.form.get('AUTO_UPDATE_SECONDS', 900))
            conf['AUTO_UPDATE_SECONDS'] = max(60, min(val, 86400)) 
        except Exception:
            pass
        save_config(conf)
        with state_lock:
            force_update_flag = True
    return redirect('/')

@app.route('/update', methods=['POST'])
@requires_auth
def trigger_update():
    global force_update_flag
    with state_lock:
        force_update_flag = True
    return redirect('/')

@app.route('/demo', methods=['POST'])
@requires_auth
def trigger_demo():
    global force_update_flag, show_demo_once
    with state_lock:
        show_demo_once = True
        force_update_flag = True
    return redirect('/')

@app.route('/test_all', methods=['POST'])
@requires_auth
def trigger_test_all():
    global test_mode_active
    with state_lock:
        is_testing = test_mode_active
        
    if not is_testing:
        threading.Thread(target=run_display_test_sequence, daemon=True).start()
    return redirect('/')

@app.route('/toggle', methods=['POST'])
@requires_auth
def toggle_display():
    global force_update_flag
    conf = get_cached_config()
    if conf:
        conf['DISPLAY_ACTIVE'] = not conf.get('DISPLAY_ACTIVE', True)
        save_config(conf)
        with state_lock:
            force_update_flag = True
    return redirect('/')

@app.route('/toggle_touch', methods=['POST'])
@requires_auth
def toggle_touch():
    global force_update_flag
    conf = get_cached_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        with state_lock:
            force_update_flag = True
    return redirect('/')

# ------------------------------------------------------------------------------
# PÄDAGOGISCHER HINTERGRUND: Dämonen, TTYs und "Fire & Forget"
# Wenn ein Skript per Autostart (systemd) hochfährt, ist es ein "Daemon" (Hintergrundprozess)
# ohne echte Benutzeroberfläche oder Konsole (TTY). Linux-Befehle wie 'sudo' geraten dann oft 
# ins Stocken, weil sie sicherheitshalber unsichtbar nach einem Passwort oder einer Bestätigung 
# fragen wollen – das Skript bleibt hängen!
# 
# Lösung 1: Der Parameter '-n' (non-interactive) zwingt sudo, niemals nachzufragen.
# Lösung 2: Absolute Pfade (/usr/bin/sudo), da Dämonen oft einen eingeschränkten $PATH haben.
# Lösung 3: subprocess.Popen() statt .run(). Popen schießt den Befehl ab und entkoppelt ihn 
# vom Python-Skript ("Fire & Forget"), damit sich das System nicht selbst blockiert.
# ------------------------------------------------------------------------------
@app.route('/sys_reboot', methods=['POST'])
@requires_auth
def sys_reboot():
    print("Web-Kommando empfangen: System wird neu gestartet.")
    shutdown_event.set() 
    
    def delayed_reboot():
        time.sleep(2.5)
        # Popen startet den Befehl im Hintergrund, ohne auf das Ende zu warten.
        subprocess.Popen(["/usr/bin/sudo", "-n", "/sbin/reboot"])
        
    threading.Thread(target=delayed_reboot, daemon=True).start()
    return "System startet neu. Bitte haben Sie einen Moment Geduld...", 200

@app.route('/sys_shutdown', methods=['POST'])
@requires_auth
def sys_shutdown():
    print("Web-Kommando empfangen: System fährt herunter.")
    shutdown_event.set() 
    
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
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(TOUCH_RST_PIN, GPIO.OUT)
                GPIO.output(TOUCH_RST_PIN, GPIO.LOW)
                time.sleep(0.1)
                GPIO.output(TOUCH_RST_PIN, GPIO.HIGH)
                time.sleep(0.2)
                clear_touch_interrupt_via_i2c()
                print("Kapazitives Touch-Display initialisiert.")
            except Exception as e:
                print(f"GPIO Setup Fehler: {e}")

        threading.Thread(target=background_loop, daemon=True).start()
            
        print(f" * Admin-Interface (Localhost): http://127.0.0.1:5000")
        # PÄDAGOGISCHER HINTERGRUND (WSGI vs Proxy): 
        # Flask's eigener 'app.run()' Server ist nicht für echte Netzwerke gedacht. 
        # Waitress wickelt als WSGI-Server die Python-HTTP-Requests performant ab.
        serve(app, host='127.0.0.1', port=5000)
        
    except KeyboardInterrupt:
        shutdown_event.set()
    finally:
        # PÄDAGOGISCHER HINTERGRUND: Deadlock-Prävention (Verklemmung)
        # Wenn sudo reboot ausgelöst wird, sendet systemd ein SIGINT an unser Skript.
        # So erreichen wir sicher diesen finally-Block, um das E-Paper vor dem
        # Stromausfall in den Sleep-Modus zu schicken (verlängert die Lebensdauer enorm!).
        shutdown_event.set()
        if GPIO: GPIO.cleanup()
        
        if epd2in13_V3 is not None:
            if display_lock.acquire(timeout=2):
                try:
                    epd = epd2in13_V3.EPD()
                    epd.init()
                    epd.Clear(0xFF)
                    epd.sleep()
                    epd2in13_V3.epdconfig.module_exit()
                except Exception as e: 
                    print(f"Fehler beim finalen Display-Clear: {e}")
                finally:
                    display_lock.release()
            else:
                print("Warnung: Display-Lock konnte beim Beenden nicht erlangt werden.")
        sys.exit(0)
