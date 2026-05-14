#!/usr/bin/env python3
# -*- coding:utf-8 -*-

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

# RPi.GPIO und smbus2 (I2C) laden
try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warnung: RPi.GPIO ist nicht installiert.")
    GPIO = None

try:
    import smbus2 as smbus
    i2c_bus = smbus.SMBus(1)
except ImportError:
    print("Warnung: smbus2 ist nicht installiert. Bitte 'pip install smbus2' ausführen.")
    i2c_bus = None

# Pfad zu den Waveshare-Treibern hinzufügen
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

from waveshare_epd import epd2in13_V3
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')

# Globale Flags & Variablen
force_update_flag = False
shutdown_event = threading.Event()   
display_lock = threading.Lock()      

# Hardware-Konfiguration (Touch)
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14 # GT1151 Chip Adresse

# ==========================================
# KONFIGURATION LADEN / SPEICHERN
# ==========================================

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception as e:
        print(f"FEHLER beim Laden der config.json: {e}")
        return {}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"FEHLER beim Speichern der config.json: {e}")

# ==========================================
# TOUCH-CONTROLLER FUNKTIONEN (I2C POLLING)
# ==========================================

def check_touch_via_i2c():
    """Liest direkt das Speicher-Register des Touch-Chips aus (Polling)."""
    if not i2c_bus: return False
    try:
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        
        status = list(read_msg)[0]
        
        if status & 0x80:
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except:
        pass
    return False

def clear_touch_interrupt_via_i2c():
    """Löscht den Touch-Speicher."""
    if not i2c_bus: return
    try:
        i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except:
        pass

# ==========================================
# WEBUNTIS & DISPLAY LOGIK
# ==========================================

def get_current_lesson(conf):
    if not conf or not conf.get('UNTIS_PASS'):
        return None, "Konfiguration unvollständig."
    
    session = None
    try:
        session = webuntis.Session(
            server=conf.get('UNTIS_SERVER', ''),
            username=conf.get('UNTIS_USER', ''),
            password=conf.get('UNTIS_PASS', ''),
            school=conf.get('UNTIS_SCHOOL', ''),
            useragent='WebUntis-Tuerschild'
        )
        session.login()
        rooms = session.rooms().filter(name=conf.get('ROOM_NAME', ''))
        if not rooms:
            return None, f"Raum {conf.get('ROOM_NAME', 'Unbekannt')} fehlt."
        
        today = datetime.date.today()
        timetable = session.timetable(room=rooms[0], start=today, end=today)
        now = datetime.datetime.now()
        now_time = now.time()
        
        # --- NEU 1: Wochenende und Feiertage / leere Tage abfangen ---
        if now.weekday() >= 5: # 5 = Samstag, 6 = Sonntag
            return None, "Schönes Wochenende!"
            
        if not timetable:
            # WebUntis liefert für Feiertage, Brückentage und Ferien eine leere Liste!
            return None, "Unterrichtsfrei"
        
        current_lesson = None
        for lesson in timetable:
            # 5 Minuten Vorlaufzeit (Puffer)
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
                break

        if current_lesson:
            raster = {
                "08:00": "1.", "08:50": "2.", "09:55": "3.", "10:45": "4.",
                "11:45": "5.", "12:35": "6.", "13:55": "7.", "14:45": "8."
            }
            start_str = current_lesson.start.strftime("%H:%M")
            stunde = raster.get(start_str, "")
            result_data = {
                "fach": ", ".join([s.name for s in current_lesson.subjects]),
                "lehrer": ", ".join([t.name for t in current_lesson.teachers]),
                "klasse": ", ".join([k.name for k in current_lesson.klassen]),
                "zeit": f"{start_str} - {current_lesson.end.strftime('%H:%M')}",
                "stunde": f"{stunde} Stunde" if stunde else ""
            }
            return result_data, None
            
        # --- NEU 2: Erweiterte Statusmeldungen (Pausen & Randzeiten) ---
        if datetime.time(9, 35) <= now_time < datetime.time(9, 50):
            return None, "1. Pause"
        elif datetime.time(11, 30) <= now_time < datetime.time(11, 40):
            return None, "2. Pause"
        elif datetime.time(13, 20) <= now_time < datetime.time(13, 50):
            return None, "Mittagspause"
        elif now_time < datetime.time(7, 55):
            return None, "Guten Morgen!"
        elif now_time >= datetime.time(15, 30):
            return None, "Unterrichtsende"
            
        # Nur echte Freistunden mitten am Schultag landen hier:
        return None, "Raum ist frei"
        
    except Exception as e:
        error_msg = str(e)
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg:
            return None, "Fehler: Keine WLAN/Internet-Verbindung"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Fehler: WebUntis Login falsch"
        else:
            return None, "Fehler: WebUntis nicht erreichbar"
    finally:
        if session:
            try:
                session.logout()
            except:
                pass

def update_display_logic(lesson_data, message, conf):
    if shutdown_event.is_set(): return 
    with display_lock: 
        try: 
            epd = epd2in13_V3.EPD()
            epd.init()
            image = Image.new('1', (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(image) 
            
            try: 
                f_huge = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 32)
                f_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 20) 
                f_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
                f_reg = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 14)
                f_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
            except:
                f_huge = f_large = f_med = f_reg = f_small = ImageFont.load_default()

            now = datetime.datetime.now()
            
            # --- 1. KOPFZEILE ---
            draw.rectangle((0, 0, 250, 28), fill=0)
            draw.text((5, 4), conf.get('ROOM_NAME', 'Unbekannt'), font=f_large, fill=255)
            
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((110, 6), time_str, font=f_reg, fill=255)

            if lesson_data:
                # --- 2. HAUPTBEREICH (2x2 Grid) ---
                draw.text((5, 40), lesson_data['stunde'], font=f_med, fill=0)
                draw.text((120, 40), lesson_data['zeit'], font=f_med, fill=0)
                
                main_info = f"{lesson_data['fach']} | {lesson_data['klasse']}"
                draw.text((5, 75), main_info, font=f_large, fill=0)
                draw.text((120, 75), lesson_data['lehrer'], font=f_large, fill=0)
            else:
                wochentage = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
                draw.text((5, 40), wochentage[now.weekday()], font=f_med, fill=0)
                
                if "Fehler" in message:
                    draw.text((5, 70), message, font=f_reg, fill=0)
                else:
                    draw.text((5, 70), message, font=f_huge, fill=0)

            epd.display(epd.getbuffer(image))
            epd.sleep()
        except Exception as e:
            print(f"Hardware-Fehler: {e}")

def clear_display_once():
    if shutdown_event.is_set(): return
    with display_lock:
        try:
            epd = epd2in13_V3.EPD()
            epd.init()
            epd.Clear(0xFF)
            epd.sleep()
        except: pass

# ==========================================
# HINTERGRUND-THREADS
# ==========================================

def background_loop():
    global force_update_flag
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    
    # NEU: Speichert das Datum des letzten Feiertags/Wochenendes
    last_static_date = None
    
    update_times = [
        "07:55", "08:00", 
        "08:45", "08:50", 
        "09:35", "09:50", "09:55", 
        "10:40", "10:45", 
        "11:30", "11:40", "11:45", 
        "12:30", "12:35", 
        "13:20", "13:50", "13:55", 
        "14:40", "14:45", 
        "15:30"
    ]

    while not shutdown_event.is_set():
        conf = load_config()
        if not conf:
            shutdown_event.wait(5)
            continue

        now_time = time.time()
        current_hm = datetime.datetime.now().strftime("%H:%M")
        
        is_exact_time = (current_hm in update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900))

        # --- I2C POLLING ---
        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time - last_touch_time > 5.0:
                print(f"\n[TOUCH {datetime.datetime.now().strftime('%H:%M:%S')}] Display berührt! Update wird vorbereitet...")
                force_update_flag = True
            last_touch_time = now_time

        # --- UPDATE-LOGIK ---
        if force_update_flag or is_interval_reached or is_exact_time:
            if is_exact_time:
                last_minute_triggered = current_hm 
                print(f"[{current_hm}] Stundenwechsel/Pause erkannt!")
            elif force_update_flag:
                print(f"[{current_hm}] MANUELLES Update!")
            
            # Zustand speichern, bevor das Flag zurückgesetzt wird
            is_manual = force_update_flag 
            force_update_flag = False
            
            if conf.get('DISPLAY_ACTIVE', True):
                data, err = get_current_lesson(conf)
                
                # --- NEU: Ruhemodus an Wochenenden und Feiertagen ---
                current_date = datetime.date.today().strftime("%Y-%m-%d")
                is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"]
                
                skip_update = False
                if is_static_day and not is_manual:
                    if last_static_date == current_date:
                        print(f"[{current_hm}] Ruhemodus aktiv ({err}). Display-Update uebersprungen.")
                        skip_update = True
                    else:
                        last_static_date = current_date # Einmaliges Update pro Tag zulassen
                else:
                    last_static_date = None # Reset an normalen Schultagen
                    
                if not skip_update:
                    update_display_logic(data, err, conf)
            else:
                clear_display_once()
                
            last_update = time.time()
            time.sleep(1.5)
            clear_touch_interrupt_via_i2c()
            last_touch_time = time.time()
            
        shutdown_event.wait(0.5)

# ==========================================
# WEB-INTERFACE
# ==========================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Türschild-Admin</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #1e293b; margin: 0; padding: 20px; display: flex; justify-content: center; }
        .card { background: white; max-width: 400px; width: 100%; border-radius: 20px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1); overflow: hidden; margin-top: 20px; }
        .header { background-color: #0f172a; color: white; padding: 30px; }
        .header h1 { margin: 0; font-size: 24px; letter-spacing: -1px; text-transform: uppercase; }
        .header p { margin: 5px 0 0; opacity: 0.6; font-size: 12px; font-weight: bold; }
        .content { padding: 30px; }
        .btn-group { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 25px; }
        .btn { display: block; text-decoration: none; text-align: center; padding: 15px; border-radius: 12px; font-weight: bold; color: white; transition: transform 0.1s; border: none; cursor: pointer; }
        .btn:active { transform: scale(0.98); }
        
        /* HIER SIND DIE NEUEN, EINFACHEN FARBEN */
        .btn-update { background-color: #007BFF; } /* Blau */
        .btn-off { background-color: #DC3545; }    /* Rot */
        .btn-on { background-color: #28A745; }     /* Grün */
        
        .btn-save { background-color: #0f172a; width: 100%; font-size: 16px; margin-top: 10px; color: white; padding: 15px; border-radius: 12px; font-weight: bold; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 10px; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }
        input { width: 100%; box-sizing: border-box; background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 12px; border-radius: 10px; font-size: 14px; font-weight: 600; outline: none; }
        .timetable-section { margin-top: 35px; padding-top: 25px; border-top: 1px solid #e2e8f0; }
        .empty-state { text-align: center; color: #94a3b8; font-size: 13px; padding: 20px; background: #f8fafc; border-radius: 10px; margin-top: 10px; font-weight: bold; }
        .error-msg { background-color: #fee2e2; color: #dc2626; padding: 15px; border-radius: 10px; font-size: 13px; font-weight: bold; text-align: center; margin-bottom: 20px; }
        .footer { text-align: center; font-size: 10px; color: #cbd5e1; margin-top: 35px; text-transform: uppercase; letter-spacing: 1px; }
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
                <div class="error-msg">
                    <strong>Konfigurationsfehler!</strong><br>
                    Die Datei 'config.json' konnte nicht gelesen werden.
                </div>
            {% endif %}
            <div class="btn-group">
                <a href="/update" class="btn btn-update">Update</a>
                <a href="/toggle" class="btn {% if conf.get('DISPLAY_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                    {% if conf.get('DISPLAY_ACTIVE', True) %}Display aus{% else %}Display an{% endif %}
                </a>
                <a href="/toggle_touch" class="btn {% if conf.get('TOUCH_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}" style="grid-column: span 2;">
                    {% if conf.get('TOUCH_ACTIVE', True) %}Touch-Funktion aus{% else %}Touch-Funktion an{% endif %}
                </a>
            </div>
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
            <div class="timetable-section">
                <label>Aktuelle Belegung ({{ conf.get('ROOM_NAME', '') }})</label>
                {% if lesson %}
                    <div style="background: #f8fafc; border-radius: 10px; padding: 15px; margin-top: 10px; border: 1px solid #e2e8f0;">
                        <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 8px;">
                            <strong style="color: #0f172a; font-size: 14px;">{{ lesson.stunde }}</strong>
                            <span style="color: #64748b; font-size: 12px; font-weight: bold;">{{ lesson.zeit }}</span>
                        </div>
                        <div style="font-size: 18px; font-weight: 800; color: #1e293b; margin-bottom: 4px;">
                            {{ lesson.fach }} <span style="color: #cbd5e1; margin: 0 4px;">|</span> {{ lesson.klasse }}
                        </div>
                        <div style="font-size: 13px; color: #475569; font-weight: 600;">Lehrkraft: {{ lesson.lehrer }}</div>
                    </div>
                {% else %}
                    <div class="empty-state">{{ msg }}</div>
                {% endif %}
            </div>
            <p class="footer">Status: {{ now }}</p>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    conf = load_config()
    current_data, message = None, ""
    if conf:
        current_data, message = get_current_lesson(conf)
    return render_template_string(HTML_TEMPLATE, conf=conf, lesson=current_data, msg=message, now=datetime.datetime.now().strftime("%H:%M:%S"))

@app.route('/save', methods=['POST'])
def save():
    conf = load_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        conf['AUTO_UPDATE_SECONDS'] = int(request.form.get('AUTO_UPDATE_SECONDS'))
        save_config(conf)
        global force_update_flag
        force_update_flag = True
    return redirect('/')

@app.route('/update')
def trigger_update():
    global force_update_flag
    force_update_flag = True
    return redirect('/')

@app.route('/toggle')
def toggle_display():
    conf = load_config()
    if conf:
        conf['DISPLAY_ACTIVE'] = not conf.get('DISPLAY_ACTIVE', True)
        save_config(conf)
        global force_update_flag
        force_update_flag = True
    return redirect('/')

@app.route('/toggle_touch')
def toggle_touch():
    conf = load_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        global force_update_flag
        force_update_flag = True
    return redirect('/')

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
        local_ip = '0.0.0.0'
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            local_ip = s.getsockname()[0]
            s.close()
        except: pass
            
        print(f" * Admin-Interface: http://{local_ip}:5000")
        serve(app, host='0.0.0.0', port=5000)
        
    except KeyboardInterrupt:
        shutdown_event.set()
    finally:
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