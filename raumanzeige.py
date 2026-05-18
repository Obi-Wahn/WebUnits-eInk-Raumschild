#!/usr/bin/env python3
# -*- coding:utf-8 -*-

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
import webuntis
from flask import Flask, render_template_string, request, redirect
from waitress import serve
from PIL import Image, ImageDraw, ImageFont

# RPi.GPIO und smbus2 (I2C) für die Hardware-Steuerung laden
try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warnung: RPi.GPIO ist nicht installiert.")
    GPIO = None

try:
    import smbus2 as smbus
    i2c_bus = smbus.SMBus(1)
except ImportError:
    print("Warnung: smbus2 ist nicht installiert.")
    i2c_bus = None

# Pfad zu den Waveshare-E-Paper-Treibern hinzufügen
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

from waveshare_epd import epd2in13_V3


# ==============================================================================
# 2. KONSTANTEN & GLOBALE VARIABLEN (STATE)
# ==============================================================================
app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')

# Hardware-Konfiguration (Touch-Controller GT1151)
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14                # I2C-Adresse des Touch-Chips

# Steuerungs-Flags für den Hintergrund-Thread
force_update_flag = True             # Startet auf True, damit direkt beim Booten das Display aktualisiert wird
show_demo_once = False               # Schalter für den Präsentations-Modus (Demo-Daten)
test_mode_active = False             # Blockiert reguläre Updates, während die Test-Routine läuft
shutdown_event = threading.Event()   # Erlaubt das saubere Beenden des Hintergrund-Threads
display_lock = threading.Lock()      # Verhindert, dass Display und Webserver gleichzeitig auf SPI zugreifen

# Globaler Cache für das Web-Interface (spart ständige, langsame API-Abfragen)
current_display_data = None
current_display_msg = "Warte auf erstes Update..."


# ==============================================================================
# 3. KONFIGURATIONS-VERWALTUNG
# ==============================================================================
def load_config():
    """Lädt die Einstellungen und den Stundenplan aus der config.json."""
    if not os.path.exists(CONFIG_FILE): return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except Exception as e:
        print(f"FEHLER beim Laden der config.json: {e}")
        return {}

def save_config(config):
    """Speichert geänderte Einstellungen (z.B. aus dem Web-Interface) ab."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"FEHLER beim Speichern der config.json: {e}")


# ==============================================================================
# 4. HARDWARE-EBENE (TOUCH & DISPLAY RESET)
# ==============================================================================
def check_touch_via_i2c():
    """Prüft über den I2C-Bus, ob das kapazitive Display berührt wurde."""
    if not i2c_bus: return False
    try:
        # Register auslesen, das anzeigt, ob ein Touch-Event vorliegt
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        
        # Bit 7 (0x80) gibt an, ob Daten bereitstehen (Touch erkannt)
        if list(read_msg)[0] & 0x80:
            # Quittung senden: "Habe den Touch registriert, setze Alarm zurück"
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except: pass
    return False

def clear_touch_interrupt_via_i2c():
    """Sendet ein Quittungssignal an den Touch-Chip, um ihn zurückzusetzen."""
    if not i2c_bus: return
    try: i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except: pass

def clear_display_once():
    """Löscht das E-Paper-Display komplett (weiß), z.B. wenn das Schild deaktiviert wird."""
    if shutdown_event.is_set(): return
    with display_lock:
        try:
            epd = epd2in13_V3.EPD()
            epd.init()
            epd.Clear(0xFF)
            epd.sleep()
        except: pass


# ==============================================================================
# 5. DATEN-EBENE: WEBUNTIS API
# ==============================================================================
def parse_lesson(lesson, conf):
    """Hilfsfunktion: Extrahiert Fach, Lehrer, Klasse etc. aus einem WebUntis-Objekt."""
    if not lesson: return None
    schedule = conf.get("SCHEDULE", {})
    lessons_conf = schedule.get("LESSONS", [])
    
    start_str = lesson.start.strftime("%H:%M")
    stunde_name = ""
    
    # Sucht den passenden Anzeigenamen (z.B. "1. Std.") aus der Config
    if isinstance(lessons_conf, list):
        for l in lessons_conf:
            if l.get("start") == start_str:
                stunde_name = l.get("name", "")
                break
    elif isinstance(lessons_conf, dict):
        stunde_name = lessons_conf.get(start_str, "")

    return {
        "fach": ", ".join([s.name for s in lesson.subjects]),
        "lehrer": ", ".join([t.name for t in lesson.teachers]),
        "klasse": ", ".join([k.name for k in lesson.klassen]),
        "zeit": f"{start_str} - {lesson.end.strftime('%H:%M')}",
        "stunde": stunde_name,
        "status_code": getattr(lesson, 'code', None) # Wichtig für Vertretungen/Ausfall
    }

def get_current_lesson(conf):
    """Verbindet sich mit WebUntis, holt den Tagesplan und filtert nach JETZT und DANACH."""
    if not conf or not conf.get('UNTIS_PASS'):
        return None, "Konfiguration unvollständig."
    
    session = None
    try:
        # 1. Login bei WebUntis
        session = webuntis.Session(
            server=conf.get('UNTIS_SERVER', ''),
            username=conf.get('UNTIS_USER', ''),
            password=conf.get('UNTIS_PASS', ''),
            school=conf.get('UNTIS_SCHOOL', ''),
            useragent='WebUntis-Tuerschild'
        )
        session.login()
        
        # 2. Raum-ID für den konfigurierten Raum ermitteln
        rooms = session.rooms().filter(name=conf.get('ROOM_NAME', ''))
        if not rooms:
            return None, f"Raum {conf.get('ROOM_NAME', 'Unbekannt')} fehlt."
        
        today = datetime.date.today()
        now = datetime.datetime.now()
        now_time = now.time()
        
        # Wochenende abfangen
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        # 3. Stundenplan für den heutigen Tag laden und chronologisch sortieren
        timetable = session.timetable(room=rooms[0], start=today, end=today)
        if not timetable:
            return {"current": None, "next": None}, "Unterrichtsfrei"
            
        timetable = sorted(timetable, key=lambda l: l.start)
        current_lesson = None
        next_lesson = None
        
        # 4. Aktuelle und nächste Stunde anhand der Uhrzeit bestimmen
        for lesson in timetable:
            # 5-Minuten-Vorlauf: Das Display schaltet bereits 5 Min vor Stundenbeginn um
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
            elif lesson.start > now and next_lesson is None:
                next_lesson = lesson

        # 5. Pausen- und Freizeit-Texte ermitteln (falls gerade kein Unterricht ist)
        message = ""
        if current_lesson is None:
            schedule = conf.get("SCHEDULE", {})
            day_start = schedule.get("DAY_START", "07:55")
            day_end = schedule.get("DAY_END", "15:30")
            breaks = schedule.get("BREAKS", [])

            try:
                ds_h, ds_m = map(int, day_start.split(":"))
                de_h, de_m = map(int, day_end.split(":"))
                
                if now_time < datetime.time(ds_h, ds_m):
                    message = "Guten Morgen!"
                elif now_time >= datetime.time(de_h, de_m):
                    message = "Unterrichtsende"
                else:
                    message = "Raum ist frei"
                    # Prüfen, ob wir uns gerade in einer definierten Pause befinden
                    for b in breaks:
                        bs_h, bs_m = map(int, b.get("start", "00:00").split(":"))
                        be_h, be_m = map(int, b.get("end", "00:00").split(":"))
                        if datetime.time(bs_h, bs_m) <= now_time < datetime.time(be_h, be_m):
                            message = b.get("name", "Pause")
                            break
            except Exception as e:
                print(f"Zeit-Parsing Fehler: {e}")
                message = "Raum ist frei"

        # Rückgabe der aufbereiteten Daten
        return {
            "current": parse_lesson(current_lesson, conf),
            "next": parse_lesson(next_lesson, conf)
        }, message
        
    except Exception as e:
        # Fehlerbehandlung bei Netzwerk- oder Login-Problemen (gekuerzte Texte für das E-Paper)
        error_msg = str(e)
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg:
            return None, "Kein WLAN/Internet"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Untis-Login falsch"
        else:
            return None, "WebUntis offline"
    finally:
        # Sitzung immer sauber beenden, um Serverressourcen zu schonen
        if session:
            try: session.logout()
            except: pass


# ==============================================================================
# 6. DARSTELLUNGS-EBENE: E-PAPER LAYOUT
# ==============================================================================
def draw_lesson_block(draw, lesson_data, y_offset, label_text, f_small, f_reg, f_med):
    """Zeichnet einen einzelnen Unterrichtsblock (z.B. JETZT oder DANACH) ins Layout."""
    header_text = f"{label_text} {lesson_data['stunde']} ({lesson_data['zeit']})"
    draw.text((5, y_offset), header_text, font=f_small, fill=0)
    
    status = lesson_data.get('status_code')
    y_content = y_offset + 16
    
    # Ausfall (Invertierter Block)
    if status == 'cancelled':
        draw.rectangle((5, y_content, 85, y_content + 18), fill=0)
        draw.text((8, y_content+2), "FÄLLT AUS", font=f_small, fill=255)
        draw.text((90, y_content), f"{lesson_data['klasse']}", font=f_reg, fill=0)
    # Vertretung (Invertiertes Label)
    elif status == 'irregular':
        draw.rectangle((5, y_content, 90, y_content + 18), fill=0)
        draw.text((8, y_content+2), "VERTRETUNG", font=f_small, fill=255)
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((95, y_content), main_info, font=f_reg, fill=0)
    # Normaler Unterricht
    else:
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((5, y_content), main_info, font=f_reg, fill=0)

def update_display_logic(data, message, conf):
    """Bereitet das komplette Layout vor und sendet es an das Hardware-E-Paper."""
    if shutdown_event.is_set(): return 
    with display_lock: 
        try: 
            # Hardware initialisieren
            epd = epd2in13_V3.EPD()
            epd.init()
            image = Image.new('1', (epd.height, epd.width), 255) # Weißes Canvas (250x122)
            draw = ImageDraw.Draw(image) 
            
            try: 
                # Schriftgröße 16 sorgt für perfekte Zentrierung auf 250px Displays (Wochenende)
                f_mega = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
                f_huge = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
                f_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18) 
                f_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
                f_reg = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
                f_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
            except:
                # Fallback auf Bitmap, falls TrueType nicht gefunden wird
                f_mega = f_huge = f_large = f_med = f_reg = f_small = ImageFont.load_default()

            now = datetime.datetime.now()
            
            # KOPFZEILE: Invertierter Balken mit Raumname und aktueller Uhrzeit
            draw.rectangle((0, 0, 250, 24), fill=0)
            draw.text((5, 3), conf.get('ROOM_NAME', 'Unbekannt'), font=f_med, fill=255)
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((120, 5), time_str, font=f_small, fill=255)

            # HAUPTBEREICH: Zweigeteilt (Jetzt/Danach) ODER Mittig (Wochenende/Feiertag)
            if data and isinstance(data, dict) and (data.get('current') or data.get('next')):
                curr_lesson = data.get('current')
                next_lesson = data.get('next')
                
                # Bereich JETZT
                if curr_lesson:
                    draw_lesson_block(draw, curr_lesson, 30, "JETZT:", f_small, f_reg, f_med)
                else:
                    draw.text((5, 35), message, font=f_large, fill=0)
                
                # Horizontale Trennlinie
                draw.line((5, 68, 245, 68), fill=0, width=1)
                
                # Bereich DANACH
                if next_lesson:
                    draw_lesson_block(draw, next_lesson, 74, "DANACH:", f_small, f_reg, f_med)
                else:
                    msg_text = "Kein Unterricht mehr heute." if "Unterrichtsende" not in message else "Bis morgen!"
                    draw.text((5, 74), "DANACH:", font=f_small, fill=0)
                    draw.text((5, 90), msg_text, font=f_reg, fill=0)
            else:
                # Aufgeräumte Einzelmeldung zentrieren (z.B. "Schönes Wochenende!")
                try:
                    bbox = draw.textbbox((0, 0), message, font=f_mega)
                    text_w = bbox[2] - bbox[0]
                except AttributeError:
                    text_w, _ = draw.textsize(message, font=f_mega)
                
                x_pos = (250 - text_w) / 2 if text_w < 250 else 2
                draw.text((x_pos, 60), message, font=f_mega, fill=0)

            # Bild an E-Paper senden und Hardware schlafen legen (Strom sparen)
            epd.display(epd.getbuffer(image))
            epd.sleep()
        except Exception as e:
            print(f"Hardware-Fehler: {e}")

# ==============================================================================
# 7. STEUERUNGS-EBENE: HINTERGRUND-LOOP & TEST-ROUTINE
# ==============================================================================

def run_display_test_sequence():
    """Spielt nacheinander alle möglichen Layouts und Fehlermeldungen auf dem Display ab."""
    global test_mode_active, current_display_data, current_display_msg, force_update_flag
    
    if test_mode_active: return
    test_mode_active = True
    conf = load_config()
    
    # 8 verschiedene Test-Szenarien für den UI-Test (mit anonymisierten Dummy-Daten)
    test_cases = [
        # 1. Normalbetrieb
        ( {"current": {"fach": "Geschichte", "lehrer": "Ab", "klasse": "9B", "zeit": "08:00 - 08:45", "stunde": "1. Std.", "status_code": None},
           "next": {"fach": "Informatik", "lehrer": "Cd", "klasse": "11B", "zeit": "08:50 - 09:35", "stunde": "2. Std.", "status_code": None}}, "" ),
        # 2. Ausfall
        ( {"current": {"fach": "Religion", "lehrer": "Ef", "klasse": "7A", "zeit": "09:55 - 10:40", "stunde": "3. Std.", "status_code": "cancelled"},
           "next": {"fach": "Geschichte", "lehrer": "Ef", "klasse": "12", "zeit": "10:45 - 11:30", "stunde": "4. Std.", "status_code": None}}, "" ),
        # 3. Vertretung und danach frei
        ( {"current": {"fach": "Werte u. Normen", "lehrer": "Gk", "klasse": "8C", "zeit": "11:45 - 12:30", "stunde": "5. Std.", "status_code": "irregular"},
           "next": None}, "" ),
        # 4. Frei / Wochenende
        ( None, "Schönes Wochenende!" ),
        # 5. Feiertag / Ferien
        ( None, "Unterrichtsfrei" ),
        # 6. Fehler: WLAN
        ( None, "Kein WLAN/Internet" ),
        # 7. Fehler: Login
        ( None, "Untis-Login falsch" ),
        # 8. Fehler: Offline
        ( None, "WebUntis offline" )
    ]
    
    for idx, (data, msg) in enumerate(test_cases):
        if shutdown_event.is_set(): break
        
        # Webinterface mit Teststatus aktualisieren
        current_display_data = data
        current_display_msg = f"TESTLAUF ({idx+1}/{len(test_cases)})..."
        
        # Das generierte Bild auf das Hardware-Display pushen
        update_display_logic(data, msg, conf)
        
        # 4 Sekunden warten (Genug Zeit, damit das E-Paper lädt und der User es betrachten kann)
        time.sleep(4)
        
    # Test beendet, Normalbetrieb wieder aufnehmen und sofortiges Update erzwingen
    test_mode_active = False
    force_update_flag = True

def background_loop():
    """Läuft dauerhaft im Hintergrund, prüft die Zeit, Touch-Events und triggert Updates."""
    global force_update_flag, show_demo_once, current_display_data, current_display_msg, test_mode_active
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None

    while not shutdown_event.is_set():
        # Solange die UI-Test-Routine läuft, macht der reguläre Haupt-Loop Pause
        if test_mode_active:
            shutdown_event.wait(1)
            continue

        conf = load_config()
        if not conf:
            shutdown_event.wait(5)
            continue

        # 1. Update-Zeiten anhand des Stundenplans generieren (inkl. 5 Min Vorlauf)
        schedule = conf.get("SCHEDULE", {})
        lessons_conf = schedule.get("LESSONS", [])
        dyn_update_times = set()
        
        if isinstance(lessons_conf, list):
            for l in lessons_conf:
                start_t = l.get("start")
                end_t = l.get("end")
                if start_t: dyn_update_times.add(start_t)
                if end_t: dyn_update_times.add(end_t)
                try:
                    h, m = map(int, start_t.split(":"))
                    dt = datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=5)
                    dyn_update_times.add(dt.strftime("%H:%M"))
                except: pass
        
        for b in schedule.get("BREAKS", []):
            if b.get("start"): dyn_update_times.add(b.get("start"))
            if b.get("end"): dyn_update_times.add(b.get("end"))
            
        dyn_update_times.add(schedule.get("DAY_START", "07:55"))
        dyn_update_times.add(schedule.get("DAY_END", "15:30"))
        
        update_times = list(dyn_update_times)

        now_time = time.time()
        current_hm = datetime.datetime.now().strftime("%H:%M")
        
        # 2. Ist es Zeit für ein turnusmäßiges oder punktgenaues Update?
        is_exact_time = (current_hm in update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900))

        # 3. Wurde das Display berührt?
        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time - last_touch_time > 5.0: # 5 Sekunden Cooldown
                print(f"\n[TOUCH {datetime.datetime.now().strftime('%H:%M:%S')}] Display beruehrt! Update wird vorbereitet...")
                force_update_flag = True
            last_touch_time = now_time

        # 4. Daten holen und Update ausführen
        if force_update_flag or is_interval_reached or is_exact_time:
            if is_exact_time: last_minute_triggered = current_hm 
            
            is_manual = force_update_flag 
            force_update_flag = False
            
            if conf.get('DISPLAY_ACTIVE', True):
                # Demo-Modus für Präsentationen (simulierte, anonyme Daten)
                if show_demo_once:
                    data = {
                        "current": {"fach": "Informatik", "lehrer": "Ab", "klasse": "11B", "zeit": "09:55 - 10:40", "stunde": "3. Std.", "status_code": "irregular"},
                        "next": {"fach": "Geschichte", "lehrer": "Cd", "klasse": "9B", "zeit": "10:45 - 11:30", "stunde": "4. Std.", "status_code": None}
                    }
                    err = ""
                    show_demo_once = False
                else:
                    # Echtdaten von WebUntis abfragen
                    data, err = get_current_lesson(conf)
                
                # Daten in den globalen Cache für das Webinterface schreiben
                current_display_data = data
                current_display_msg = err

                current_date = datetime.date.today().strftime("%Y-%m-%d")
                is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"]
                
                # Hardware schonen: An freien Tagen nur noch bei manuellem Trigger aktualisieren
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
            # Touch-Chip zurücksetzen nach dem Update
            clear_touch_interrupt_via_i2c()
            last_touch_time = time.time()
            
        shutdown_event.wait(0.5)


# ==============================================================================
# 8. WEB-EBENE: FLASK ADMIN-INTERFACE & ROUTEN
# ==============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Türschild-Admin</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #1e293b; margin: 0; padding: 20px; display: flex; justify-content: center; }
        .card { background: white; max-width: 400px; width: 100%; border-radius: 20px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1); overflow: hidden; margin-top: 20px; margin-bottom: 20px; }
        .header { background-color: #0f172a; color: white; padding: 30px; }
        .header h1 { margin: 0; font-size: 24px; letter-spacing: -1px; text-transform: uppercase; }
        .header p { margin: 5px 0 0; opacity: 0.6; font-size: 12px; font-weight: bold; }
        .content { padding: 30px; }
        
        .section-title { font-size: 11px; font-weight: 800; color: #64748b; text-transform: uppercase; margin: 30px 0 15px 0; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; letter-spacing: 0.5px; }
        .section-title:first-child { margin-top: 0; }
        
        .btn-group { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; }
        .btn-full { grid-column: span 2; }
        .btn { display: block; text-decoration: none; text-align: center; padding: 15px; border-radius: 12px; font-weight: bold; color: white; transition: transform 0.1s; border: none; cursor: pointer; font-size: 14px;}
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
    </style>
</head>
<body>
    <div class="card">
        <!-- 1. KOPFZEILE -->
        <div class="header">
            <h1>Display-Control</h1>
            <p>{{ conf.get('ROOM_NAME', 'Unbekannt') }} | Raumanzeige</p>
        </div>
        
        <div class="content">
            {% if conf|length == 0 %}
                <div class="error-msg">Konfigurationsfehler! Die Datei 'config.json' konnte nicht gelesen werden.</div>
            {% endif %}
            
            <!-- 2. GERÄTESTEUERUNG -->
            <div class="section-title">Gerätesteuerung</div>
            <div class="btn-group">
                <a href="/update" class="btn btn-update btn-full">Manuelles Update</a>
                <a href="/toggle" class="btn {% if conf.get('DISPLAY_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                    {% if conf.get('DISPLAY_ACTIVE', True) %}Display aus{% else %}Display an{% endif %}
                </a>
                <a href="/toggle_touch" class="btn {% if conf.get('TOUCH_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                    {% if conf.get('TOUCH_ACTIVE', True) %}Touch aus{% else %}Touch an{% endif %}
                </a>
            </div>
            
            <!-- 3. EINSTELLUNGEN -->
            <div class="section-title">Einstellungen</div>
            <form action="/save" method="POST">
                <div class="form-group">
                    <label>Anzeigeraum</label>
                    <input type="text" name="ROOM_NAME" value="{{ conf.get('ROOM_NAME', '') }}">
                </div>
                <div class="form-group">
                    <label>Intervall (Sekunden)</label>
                    <input type="number" name="AUTO_UPDATE_SECONDS" value="{{ conf.get('AUTO_UPDATE_SECONDS', 900) }}">
                </div>
                <button type="submit" class="btn btn-save">Speichern</button>
            </form>
            
            <!-- 4. STATUS -->
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
                        </div>
                    {% else %}
                        <div class="empty-state">Kein Unterricht mehr.</div>
                    {% endif %}
                    
                {% else %}
                    <!-- Aufgeräumter Wochenend-/Feiertags-Bildschirm im Webinterface -->
                    <div class="empty-state" style="font-size: 16px; padding: 30px 20px;">{{ msg }}</div>
                {% endif %}
            </div>
            
            <!-- 5. TEST & SIMULATION -->
            <div class="section-title">Test & Simulation</div>
            <div class="btn-group">
                <a href="/demo" class="btn btn-demo btn-full">Simulierte Daten laden</a>
                <a href="/test_all" class="btn btn-test btn-full">Display-Testlauf starten (ca. 30 Sek)</a>
            </div>
            
            <p class="footer">Status: {{ now }}</p>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    """Startseite: Liest die Daten aus dem Cache, blockiert also nicht bei WebUntis-Anfragen."""
    conf = load_config()
    return render_template_string(
        HTML_TEMPLATE, 
        conf=conf, 
        data=current_display_data, 
        msg=current_display_msg, 
        now=datetime.datetime.now().strftime("%H:%M:%S")
    )

@app.route('/save', methods=['POST'])
def save():
    """Speichert Raum und Intervall in der Konfiguration ab."""
    conf = load_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        conf['AUTO_UPDATE_SECONDS'] = int(request.form.get('AUTO_UPDATE_SECONDS'))
        save_config(conf)
        global force_update_flag
        force_update_flag = True
        time.sleep(0.5) 
    return redirect('/')

@app.route('/update')
def trigger_update():
    """Erzwingt einen sofortigen Refresh bei WebUntis."""
    global force_update_flag
    force_update_flag = True
    time.sleep(0.5)
    return redirect('/')

@app.route('/demo')
def trigger_demo():
    """Lädt einmalig simulierte Vertretungsdaten auf das Display (für Präsentationen)."""
    global force_update_flag, show_demo_once
    show_demo_once = True
    force_update_flag = True
    time.sleep(0.5) 
    return redirect('/')

@app.route('/test_all')
def trigger_test_all():
    """Startet den Test-Thread im Hintergrund, damit das Webinterface nicht blockiert."""
    threading.Thread(target=run_display_test_sequence, daemon=True).start()
    time.sleep(0.5) # Kurze Pause, damit die "TESTLAUF aktiv..." Meldung direkt auf der Website erscheint
    return redirect('/')

@app.route('/toggle')
def toggle_display():
    """Schaltet das Hardware-Display in den Konfigurationseinstellungen an oder aus."""
    conf = load_config()
    if conf:
        conf['DISPLAY_ACTIVE'] = not conf.get('DISPLAY_ACTIVE', True)
        save_config(conf)
        global force_update_flag
        force_update_flag = True
        time.sleep(0.5)
    return redirect('/')

@app.route('/toggle_touch')
def toggle_touch():
    """Aktiviert/Deaktiviert die Touch-Fläche (I2C-Polling)."""
    conf = load_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        global force_update_flag
        force_update_flag = True
        time.sleep(0.5)
    return redirect('/')


# ==============================================================================
# 9. START-EBENE: HAUPTPROGRAMM (ENTRY POINT)
# ==============================================================================
if __name__ == '__main__':
    try:
        # Hardware-Reset des Touch-Chips einmalig beim Start durchführen
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

        # Den eigentlichen "Motor" des Programms als Hintergrundprozess starten
        threading.Thread(target=background_loop, daemon=True).start()
            
        print(f" * Admin-Interface (Localhost): http://127.0.0.1:5000")
        
        # Den lokalen Webserver über Waitress starten 
        # (Wird nach außen durch Nginx per HTTPS geschützt)
        serve(app, host='127.0.0.1', port=5000)
        
    except KeyboardInterrupt:
        # Bei Abbruch (Strg+C) durch den Benutzer den Hintergrund-Thread sauber anhalten
        shutdown_event.set()
    finally:
        # Ressourcen freigeben und Display löschen
        shutdown_event.set()
        if GPIO: GPIO.cleanup()
        with display_lock:
            try:
                epd = epd2in13_V3.EPD()
                epd.init()
                epd.Clear(0xFF)
                epd.sleep()
                epd2in13_V3.epdconfig.module_exit()
            except: pass
        sys.exit(0)
