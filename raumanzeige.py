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
force_update_flag = True     # NEU: Startet auf True, damit sofort beim Booten geladen wird
show_demo_once = False
shutdown_event = threading.Event()   
display_lock = threading.Lock()      

# NEU: Globaler Cache für das Web-Interface
current_display_data = None
current_display_msg = "Warte auf erstes Update..."

# Hardware-Konfiguration (Touch)
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14 # GT1151 Chip Adresse

# ==========================================
# KONFIGURATION LADEN / SPEICHERN
# ==========================================

def load_config():
    if not os.path.exists(CONFIG_FILE): return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
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
    if not i2c_bus: return False
    try:
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        if list(read_msg)[0] & 0x80:
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except: pass
    return False

def clear_touch_interrupt_via_i2c():
    if not i2c_bus: return
    try: i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except: pass

# ==========================================
# WEBUNTIS & DISPLAY LOGIK
# ==========================================

def parse_lesson(lesson, conf):
    if not lesson: return None
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

    return {
        "fach": ", ".join([s.name for s in lesson.subjects]),
        "lehrer": ", ".join([t.name for t in lesson.teachers]),
        "klasse": ", ".join([k.name for k in lesson.klassen]),
        "zeit": f"{start_str} - {lesson.end.strftime('%H:%M')}",
        "stunde": stunde_name,
        "status_code": getattr(lesson, 'code', None)
    }

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
        now = datetime.datetime.now()
        now_time = now.time()
        
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        timetable = session.timetable(room=rooms[0], start=today, end=today)
        if not timetable:
            return {"current": None, "next": None}, "Unterrichtsfrei"
            
        timetable = sorted(timetable, key=lambda l: l.start)
        current_lesson = None
        next_lesson = None
        
        for lesson in timetable:
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
            elif lesson.start > now and next_lesson is None:
                next_lesson = lesson

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
                    for b in breaks:
                        bs_h, bs_m = map(int, b.get("start", "00:00").split(":"))
                        be_h, be_m = map(int, b.get("end", "00:00").split(":"))
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
        error_msg = str(e)
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg:
            return None, "Fehler: Keine WLAN/Internet-Verbindung"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Fehler: WebUntis Login falsch"
        else:
            return None, "Fehler: WebUntis nicht erreichbar"
    finally:
        if session:
            try: session.logout()
            except: pass

def draw_lesson_block(draw, lesson_data, y_offset, label_text, f_small, f_reg, f_med):
    header_text = f"{label_text} {lesson_data['stunde']} ({lesson_data['zeit']})"
    draw.text((5, y_offset), header_text, font=f_small, fill=0)
    
    status = lesson_data.get('status_code')
    y_content = y_offset + 16
    
    if status == 'cancelled':
        draw.rectangle((5, y_content, 85, y_content + 18), fill=0)
        draw.text((8, y_content+2), "FÄLLT AUS", font=f_small, fill=255)
        draw.text((90, y_content), f"{lesson_data['klasse']}", font=f_reg, fill=0)
    elif status == 'irregular':
        draw.rectangle((5, y_content, 90, y_content + 18), fill=0)
        draw.text((8, y_content+2), "VERTRETUNG", font=f_small, fill=255)
        # HIER IST DIE KORREKTUR: Die Klasse wurde eingefuegt
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((95, y_content), main_info, font=f_reg, fill=0)
    else:
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((5, y_content), main_info, font=f_reg, fill=0)

def update_display_logic(data, message, conf):
    if shutdown_event.is_set(): return 
    with display_lock: 
        try: 
            epd = epd2in13_V3.EPD()
            epd.init()
            image = Image.new('1', (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(image) 
            
            try: 
                f_huge = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
                f_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18) 
                f_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
                f_reg = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
                f_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
            except:
                f_huge = f_large = f_med = f_reg = f_small = ImageFont.load_default()

            now = datetime.datetime.now()
            
            draw.rectangle((0, 0, 250, 24), fill=0)
            draw.text((5, 3), conf.get('ROOM_NAME', 'Unbekannt'), font=f_med, fill=255)
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((120, 5), time_str, font=f_small, fill=255)

            # NEU: Prüfe, ob es überhaupt Unterricht zum Anzeigen gibt
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
                # NEU: Einzelne, aufgeräumte Meldung ohne Split-Screen
                draw.text((5, 45), message, font=f_large, fill=0)

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
    global force_update_flag, show_demo_once, current_display_data, current_display_msg
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None

    while not shutdown_event.is_set():
        conf = load_config()
        if not conf:
            shutdown_event.wait(5)
            continue

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
        
        is_exact_time = (current_hm in update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900))

        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time - last_touch_time > 5.0:
                print(f"\n[TOUCH {datetime.datetime.now().strftime('%H:%M:%S')}] Display beruehrt! Update wird vorbereitet...")
                force_update_flag = True
            last_touch_time = now_time

        if force_update_flag or is_interval_reached or is_exact_time:
            if is_exact_time: last_minute_triggered = current_hm 
            
            is_manual = force_update_flag 
            force_update_flag = False
            
            if conf.get('DISPLAY_ACTIVE', True):
                if show_demo_once:
                    data = {
                        # HIER WURDE DIE KLASSE AUF 11B GEÄNDERT
                        "current": {"fach": "Informatik", "lehrer": "Ab", "klasse": "11B", "zeit": "09:55 - 10:40", "stunde": "3. Std.", "status_code": "irregular"},
                        "next": {"fach": "Geschichte", "lehrer": "Cd", "klasse": "9B", "zeit": "10:45 - 11:30", "stunde": "4. Std.", "status_code": None}
                    }
                    err = ""
                    show_demo_once = False
                    print(f"[{current_hm}] DEMO-DATEN generiert!")
                else:
                    data, err = get_current_lesson(conf)
                
                # NEU: Daten im globalen Speicher ablegen (für das Web-Interface)
                current_display_data = data
                current_display_msg = err

                current_date = datetime.date.today().strftime("%Y-%m-%d")
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
        .btn { display: block; text-decoration: none; text-align: center; padding: 15px; border-radius: 12px; font-weight: bold; color: white; transition: transform 0.1s; border: none; cursor: pointer; font-size: 14px;}
        .btn:active { transform: scale(0.98); }
        .btn-update { background-color: #007BFF; } 
        .btn-demo { background-color: #6f42c1; } 
        .btn-off { background-color: #DC3545; }    
        .btn-on { background-color: #28A745; }     
        .btn-save { background-color: #0f172a; width: 100%; font-size: 16px; margin-top: 10px; color: white; padding: 15px; border-radius: 12px; font-weight: bold; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 10px; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }
        input { width: 100%; box-sizing: border-box; background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 12px; border-radius: 10px; font-size: 14px; font-weight: 600; outline: none; }
        .timetable-section { margin-top: 35px; padding-top: 25px; border-top: 1px solid #e2e8f0; }
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
        <div class="header">
            <h1>Display-Control</h1>
            <p>{{ conf.get('ROOM_NAME', 'Unbekannt') }} | Raumanzeige</p>
        </div>
        <div class="content">
            {% if conf|length == 0 %}
                <div class="error-msg">Konfigurationsfehler! Die Datei 'config.json' konnte nicht gelesen werden.</div>
            {% endif %}
            
            <div class="btn-group">
                <a href="/update" class="btn btn-update">Update</a>
                <a href="/demo" class="btn btn-demo">Demo-Daten</a>
                <a href="/toggle" class="btn {% if conf.get('DISPLAY_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                    {% if conf.get('DISPLAY_ACTIVE', True) %}Display aus{% else %}Display an{% endif %}
                </a>
                <a href="/toggle_touch" class="btn {% if conf.get('TOUCH_ACTIVE', True) %}btn-off{% else %}btn-on{% endif %}">
                    {% if conf.get('TOUCH_ACTIVE', True) %}Touch aus{% else %}Touch an{% endif %}
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
                <label>Aktuelle Anzeige ({{ conf.get('ROOM_NAME', '') }})</label>
                
                <!-- NEU: Auch in der Weboberfläche die Zweiteilung ausblenden, wenn kein Unterricht ist -->
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
                    <!-- NEU: Größere und mittige Schrift für die Feiertags/Wochenend-Meldung -->
                    <div class="empty-state" style="font-size: 16px; padding: 30px 20px;">{{ msg }}</div>
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
    # NEU: Das Webinterface liest jetzt einfach den Cache aus, statt selbst WebUntis zu blockieren
    return render_template_string(
        HTML_TEMPLATE, 
        conf=conf, 
        data=current_display_data, 
        msg=current_display_msg, 
        now=datetime.datetime.now().strftime("%H:%M:%S")
    )

@app.route('/save', methods=['POST'])
def save():
    conf = load_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        conf['AUTO_UPDATE_SECONDS'] = int(request.form.get('AUTO_UPDATE_SECONDS'))
        save_config(conf)
        global force_update_flag
        force_update_flag = True
        time.sleep(0.5) # Dem Hintergrundprozess kurz Zeit geben
    return redirect('/')

@app.route('/update')
def trigger_update():
    global force_update_flag
    force_update_flag = True
    time.sleep(0.5) # Dem Hintergrundprozess kurz Zeit geben
    return redirect('/')

@app.route('/demo')
def trigger_demo():
    global force_update_flag, show_demo_once
    show_demo_once = True
    force_update_flag = True
    time.sleep(0.5) # Dem Hintergrundprozess kurz Zeit geben, um den Cache zu füllen!
    return redirect('/')

@app.route('/toggle')
def toggle_display():
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
    conf = load_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        global force_update_flag
        force_update_flag = True
        time.sleep(0.5)
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
