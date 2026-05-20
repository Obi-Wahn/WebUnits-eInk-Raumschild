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
"""

# ==============================================================================
# 1. IMPORTS & HARDWARE-SETUP
# ==============================================================================
import sys
import os
import time
import datetime
import json
import threading     # Für asynchrone Prozesse (Hintergrund-Schleife)
import socket        # Für Netzwerk-Timeouts
import tempfile      # Für sicheres (atomares) Speichern von Dateien
import webuntis      # Die offizielle WebUntis API-Schnittstelle
from functools import wraps
from flask import Flask, render_template_string, request, redirect, Response
from waitress import serve  # Ein sicherer, produktionsreifer WSGI-Webserver
from PIL import Image, ImageDraw, ImageFont # Pillow: Für die Bildgenerierung

# ------------------------------------------------------------------------------
# Hardware-Schnittstellen (GPIO & I2C) laden. 
# 
# PÄDAGOGISCHER HINTERGRUND: "Graceful Degradation" (Gutmütiges Herabstufen)
# Try-Except-Blöcke ermöglichen es, das Skript auch auf einem normalen 
# Windows/Mac-Rechner zu testen und weiterzuentwickeln. Fehlt die Raspberry-
# Hardware, fangen wir den Fehler ab und setzen die Variablen auf None, 
# anstatt das Programm sofort mit einem harten Absturz zu beenden.
# ------------------------------------------------------------------------------
try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warnung: RPi.GPIO ist nicht installiert. (Entwicklungsmodus?)")
    GPIO = None

try:
    import smbus2 as smbus
    i2c_bus = smbus.SMBus(1)
except ImportError:
    print("Warnung: smbus2 ist nicht installiert. (Entwicklungsmodus?)")
    smbus = None
    i2c_bus = None

# Pfad zu den E-Paper-Treibern des Herstellers (Waveshare) dynamisch hinzufügen
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

try:
    from waveshare_epd import epd2in13_V3
except ImportError:
    print("Warnung: waveshare_epd Treiber nicht gefunden.")
    epd2in13_V3 = None


# ==============================================================================
# 2. KONSTANTEN & GLOBALE VARIABLEN (ZUSTAND/STATE)
# ==============================================================================
app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')

# Hardware-Pins (für das 2.13 Zoll Waveshare Touch-Display)
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14                # Hexadezimale Adresse des Touch-Controllers auf dem I2C-Bus

# Steuerungsvariablen für die Kommunikation zwischen Webserver und Hintergrund-Loop
force_update_flag = True             # Startet auf True, damit beim Booten direkt ein Update erfolgt
show_demo_once = False               # Schalter für den Präsentations-Modus (zeigt simulierte Daten)
test_mode_active = False             # Blockiert reguläre Updates, solange der Display-Test läuft
shutdown_event = threading.Event()   # Thread-sicheres Signal zum sauberen Beenden des Programms (STRG+C)

# ------------------------------------------------------------------------------
# THREADING-LOCKS (Schutz vor "Race Conditions")
# 
# PÄDAGOGISCHER HINTERGRUND: In diesem Skript laufen zwei Prozesse gleichzeitig:
# 1. Der Web-Server (Flask/Waitress), der Klicks der Benutzer verarbeitet.
# 2. Der Hintergrund-Loop (background_loop), der die Uhrzeit prüft.
# Wenn beide gleichzeitig eine Variable ändern oder auf das Display schreiben wollen,
# kommt es zu Datenmüll oder Abstürzen (Race Condition).
# Ein "Lock" (Mutex) wirkt wie ein Schlüssel zu einem Raum: Wer den Schlüssel hat, 
# darf arbeiten. Der andere Thread muss solange an der Tür warten.
# ------------------------------------------------------------------------------
display_lock = threading.Lock()      # Verhindert, dass zwei Prozesse gleichzeitig aufs SPI-Display schreiben
state_lock = threading.Lock()        # Schützt unsere globalen Steuerungs-Flags (z.B. force_update_flag)

# Variablen für die Zeit-Simulation (Time-Travel-Feature für das Testen im Webinterface)
simulated_datetime = None

# Globaler Cache, damit das Webinterface nicht bei jedem Neuladen eine langsame API-Abfrage startet
current_display_data = None
current_display_msg = "Warte auf erstes Update..."

# Performance-Caching für Dateien (Schützt die empfindliche MicroSD-Karte!)
_cached_config = {}
_last_config_mtime = 0
GLOBAL_FONTS = {}


# ==============================================================================
# 3. KONFIGURATIONS-VERWALTUNG & CACHING
# ==============================================================================
def get_cached_config():
    """
    Lädt die Config nur neu, wenn sie sich wirklich auf der SD-Karte geändert hat.
    
    PÄDAGOGISCHER HINTERGRUND (I/O-Bottleneck & Caching): 
    Ein ständiger Zugriff auf die Festplatte/SD-Karte ist im Vergleich zum RAM 
    extrem langsam und schadet der Hardware-Lebensdauer (Wear-out). 
    Durch das Auslesen des Datei-Zeitstempels (getmtime) wissen wir, ob sich ein 
    teurer, erneuter Lese-Vorgang überhaupt lohnt.
    """
    global _cached_config, _last_config_mtime
    if not os.path.exists(CONFIG_FILE): return {}
    try:
        # getmtime liefert die "Modified Time" als Timestamp (Zahl)
        mtime = os.path.getmtime(CONFIG_FILE)
        if mtime > _last_config_mtime:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                _cached_config = json.loads(content) if content else {}
            _last_config_mtime = mtime
    except Exception as e:
        print(f"FEHLER beim Laden der config.json: {e}")
    return _cached_config

def save_config(config):
    """
    Speichert Einstellungen stromausfallsicher ab (Atomarer Schreibvorgang).
    
    PÄDAGOGISCHER HINTERGRUND (Atomizität):
    Würde der Raspberry Pi genau während eines standardmäßigen 'open(file, w)' 
    Vorgangs den Strom verlieren (weil jemand den Stecker zieht), wäre die Datei 
    korrupt (0 Byte) und das Schild beim nächsten Booten kaputt.
    Wir schreiben daher erst in eine versteckte, temporäre Datei und tauschen 
    sie am Ende nahtlos (atomar) auf Betriebssystemebene aus.
    """
    try:
        dir_name = os.path.dirname(CONFIG_FILE)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        # Der magische Moment: Überschreibt die alte Datei sicher und unwiderruflich
        os.replace(temp_path, CONFIG_FILE)
        
        # Den RAM-Cache sofort invalidieren (auf 0 setzen), 
        # damit das Skript beim nächsten Mal weiß, dass es neu von der Karte lesen muss.
        global _last_config_mtime
        _last_config_mtime = 0 
    except Exception as e:
        print(f"FEHLER beim Speichern der config.json: {e}")


# ==============================================================================
# 3.5 ZENTRALE UHR (Abstraktion der Zeit für Testzwecke)
# ==============================================================================
def get_now():
    """
    Gibt das aktuelle Datum/Uhrzeit zurück.
    Wenn im Web-Interface die "Time Travel"-Funktion genutzt wurde, 
    liefern wir stattdessen die simulierte Zeit zurück, um die WebUntis-API 
    auszutricksen.
    """
    global simulated_datetime
    with state_lock: # Thread-Safe lesen, da der Wert über das Web verändert werden kann
        if simulated_datetime:
            return simulated_datetime
    return datetime.datetime.now()


# ==============================================================================
# 4. HARDWARE-EBENE (TOUCH, DISPLAY RESET & FONTS)
# ==============================================================================
def init_fonts():
    """
    Lädt die Schriftarten beim Programmstart einmalig in den RAM.
    
    PÄDAGOGISCHER HINTERGRUND (Lazy Loading / Singleton-Pattern ähnlich):
    Anstatt bei jedem Display-Refresh die schweren TrueType-Dateien neu 
    in den Speicher zu laden, machen wir das genau einmal, sobald sie 
    gebraucht werden, und cachen sie im globalen Dictionary.
    """
    global GLOBAL_FONTS
    if GLOBAL_FONTS: return # Schon geladen? Dann nichts tun!
    try: 
        GLOBAL_FONTS['mega'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
        GLOBAL_FONTS['huge'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)
        GLOBAL_FONTS['large'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18) 
        GLOBAL_FONTS['med'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
        GLOBAL_FONTS['reg'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
        GLOBAL_FONTS['small'] = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
    except Exception:
        # Fallback auf eine hässliche, aber stets verfügbare Bitmap-Schriftart
        default = ImageFont.load_default()
        GLOBAL_FONTS = {k: default for k in ['mega', 'huge', 'large', 'med', 'reg', 'small']}

def check_touch_via_i2c():
    """
    Prüft direkt über den I2C-Bus, ob ein Finger das kapazitive Display berührt.
    I2C ist ein serielles Datenbus-System für die Nahbereichs-Kommunikation.
    """
    if not i2c_bus or not smbus: return False
    try:
        # Schreibt auf Register 0x814E und liest die Antwort aus
        write_msg = smbus.i2c_msg.write(TOUCH_I2C_ADDR, [0x81, 0x4E])
        read_msg = smbus.i2c_msg.read(TOUCH_I2C_ADDR, 1)
        i2c_bus.i2c_rdwr(write_msg, read_msg)
        
        # Bit-Maskierung: Wenn das höchste Bit (0x80) gesetzt ist, gibt es einen Touch
        if list(read_msg)[0] & 0x80:
            # Quittungssignal senden, damit der Chip seinen internen Alarm zurücksetzt
            i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
            return True
    except Exception: 
        pass # Stummes Ignorieren für reibungslosen Ablauf, falls Display schläft
    return False

def clear_touch_interrupt_via_i2c():
    """Setzt den Touch-Chip manuell zurück (z.B. beim Bootvorgang)."""
    if not i2c_bus: return
    try: 
        i2c_bus.write_i2c_block_data(TOUCH_I2C_ADDR, 0x81, [0x4E, 0x00])
    except Exception as e: 
        print(f"I2C Reset Fehler: {e}")

def clear_display_once():
    """
    Löscht das E-Paper-Display komplett weiß.
    Wird aufgerufen, wenn das Display per Web-Schalter deaktiviert wird.
    """
    if shutdown_event.is_set(): return
    if epd2in13_V3 is None: return # Hardware-Schutz, falls auf dem PC getestet wird
    
    with display_lock:
        try:
            epd = epd2in13_V3.EPD()
            epd.init()
            epd.Clear(0xFF) # 0xFF ist hexadezimal für Weiß
            epd.sleep()
        except Exception as e: 
            print(f"Display Clear Fehler: {e}")


# ==============================================================================
# 5. DATEN-EBENE: WEBUNTIS API
# ==============================================================================
def parse_lesson(lesson, conf):
    """
    Hilfsfunktion: Nimmt ein komplexes, rohes WebUntis-Klassenobjekt und 
    extrahiert genau die Strings, die wir für das Display (Frontend) brauchen.
    """
    # Sicherheit (Input Validation): Falls ein kaputtes Objekt übergeben wird
    if not lesson or not getattr(lesson, 'start', None) or not getattr(lesson, 'end', None): 
        return None
    
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

    # Vertretungs- oder Zusatzinfos auslesen (z.B. "Aufgaben in IServ bearbeiten")
    info_parts = []
    for attr in ['info', 'lstext', 'substText']:
        val = getattr(lesson, attr, '')
        if val and str(val).strip() and str(val).strip() not in info_parts:
            info_parts.append(str(val).strip())
    
    stunden_info = " | ".join(info_parts)

    return {
        # Listen Comprehensions (Pädagogisch): Verkürzt for-Schleifen auf eine Zeile
        "fach": ", ".join([s.name for s in getattr(lesson, 'subjects', [])]),
        "lehrer": ", ".join([t.name for t in getattr(lesson, 'teachers', [])]),
        "klasse": ", ".join([k.name for k in getattr(lesson, 'klassen', [])]),
        "zeit": f"{start_str} - {lesson.end.strftime('%H:%M')}",
        "stunde": stunde_name,
        "status_code": getattr(lesson, 'code', None), # Gibt z.B. 'cancelled' zurück
        "stunden_info": stunden_info 
    }

def get_current_lesson(conf):
    """
    Verbindet sich mit der WebUntis-API, holt den Tagesplan und filtert ihn
    chronologisch nach "JETZT" und "DANACH".
    """
    # PÄDAGOGISCHER HINTERGRUND (Input Validation): 
    # Niemals Netzwerk/API-Calls starten, wenn die Config-Pflichtdaten fehlen!
    req_keys = ['UNTIS_SERVER', 'UNTIS_USER', 'UNTIS_PASS', 'UNTIS_SCHOOL', 'ROOM_NAME']
    if not conf or any(not conf.get(k) for k in req_keys):
        return None, "Konfiguration unvollständig."
    
    session = None
    # Timeout schützt das Skript davor, endlos zu hängen, wenn das Schul-WLAN klemmt
    socket.setdefaulttimeout(30) 
    
    try:
        # 1. Login bei WebUntis initialisieren
        session = webuntis.Session(
            server=conf.get('UNTIS_SERVER'),
            username=conf.get('UNTIS_USER'),
            password=conf.get('UNTIS_PASS'),
            school=conf.get('UNTIS_SCHOOL'),
            useragent='WebUntis-Tuerschild'
        )
        session.login()
        
        # 2. Raum in der Untis-Datenbank suchen
        rooms = session.rooms().filter(name=conf.get('ROOM_NAME'))
        if not rooms:
            return None, f"Raum {conf.get('ROOM_NAME')} fehlt."
        
        now = get_now()
        today = now.date()
        now_time = now.time()
        
        # Am Wochenende wird das E-Paper und die API serverseitig geschont
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        # 3. Stundenplan laden
        timetable = session.timetable(room=rooms[0], start=today, end=today)
        if not timetable:
            return {"current": None, "next": None}, "Unterrichtsfrei"
            
        # PÄDAGOGISCHER HINTERGRUND (Lambda-Funktionen):
        # Wir sortieren die Liste der Stunden chronologisch. Die anonyme Lambda-Funktion
        # sagt dem sort-Algorithmus: "Nutze das Attribut 'start' als Sortier-Kriterium".
        timetable = sorted(timetable, key=lambda l: getattr(l, 'start', datetime.datetime.max))
        
        current_lesson = None
        next_lesson = None
        
        # 4. Aktuelle und nächste Stunde anhand der genauen Uhrzeit bestimmen
        for lesson in timetable:
            # Defektes Untis-Objekt? Überspringen!
            if getattr(lesson, 'start', None) is None or getattr(lesson, 'end', None) is None:
                continue
                
            # 5-Minuten-Vorlauf: Das Display schaltet bereits 5 Min vor dem Klingeln um
            lesson_start_buffered = lesson.start - datetime.timedelta(minutes=5)
            
            if lesson_start_buffered <= now <= lesson.end:
                current_lesson = lesson
            elif lesson.start > now and next_lesson is None:
                next_lesson = lesson

        # 5. Pausen- und Freizeit-Texte ermitteln, wenn der Raum gerade nicht gebucht ist
        message = ""
        if current_lesson is None:
            schedule = conf.get("SCHEDULE", {})
            try:
                # String "07:55" mit map in zwei Integer (Stunden, Minuten) splitten
                ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
                de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
                
                if now_time < datetime.time(ds_h, ds_m):
                    message = "Guten Morgen!"
                elif now_time >= datetime.time(de_h, de_m):
                    message = "Unterrichtsende"
                else:
                    message = "Raum ist frei"
                    # Prüfen, ob wir uns exakt in einer Pause (laut Config) befinden
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
        # Graceful Degradation: Sprechende Fehlermeldungen für das E-Paper, 
        # wenn die Netzwerkschicht zickt.
        error_msg = str(e)
        print(f"WebUntis API Fehler: {error_msg}")
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg or "timeout" in error_msg.lower():
            return None, "Kein WLAN/Internet"
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Untis-Login falsch"
        else:
            return None, "WebUntis offline"
    finally:
        # PÄDAGOGISCHER HINTERGRUND: Der 'finally'-Block wird IMMER ausgeführt, 
        # egal ob ein Fehler auftrat oder nicht. Perfekt, um Ressourcen aufzuräumen.
        if session:
            try: session.logout()
            except Exception: pass


# ==============================================================================
# 6. DARSTELLUNGS-EBENE: E-PAPER LAYOUT & CANVAS
# ==============================================================================
def get_text_width(draw, text, font):
    """
    Hilfsfunktion zur Abwärtskompatibilität. 
    Da sich die Pillow-Bibliothek über die Jahre stark gewandelt hat, fangen wir 
    fehlende Methoden (z.B. das alte textsize) hierarchisch über Try-Except ab.
    """
    try: return draw.textlength(text, font=font) # Pillow >= 8.0.0
    except AttributeError:
        try: return draw.textbbox((0,0), text, font=font)[2] # Pillow >= 9.2.0 Fallback
        except AttributeError: return draw.textsize(text, font=font)[0] # Pillow Legacy

def draw_lesson_block(draw, lesson_data, y_offset, label_text, f_small, f_reg, f_med):
    """
    Zeichnet einen einzelnen Unterrichtsblock (JETZT oder DANACH) als Grafik.
    """
    header_text = f"{label_text} {lesson_data['stunde']} ({lesson_data['zeit']})"
    draw.text((5, y_offset), header_text, font=f_small, fill=0) # fill=0 bedeutet Schwarz
    
    status = lesson_data.get('status_code')
    y_content = y_offset + 16
    
    # Priorität 1: Ausfall (Invertierter schwarzer Block für hohe Sichtbarkeit im Vorbeigehen)
    if status == 'cancelled':
        draw.rectangle((5, y_content, 85, y_content + 18), fill=0)
        draw.text((8, y_content+2), "FÄLLT AUS", font=f_small, fill=255) # fill=255 bedeutet Weiß
        draw.text((90, y_content), f"{lesson_data['klasse']}", font=f_reg, fill=0)
        
    # Priorität 2: Vertretung (Invertiertes Label)
    elif status == 'irregular':
        draw.rectangle((5, y_content, 90, y_content + 18), fill=0)
        draw.text((8, y_content+2), "VERTRETUNG", font=f_small, fill=255)
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((95, y_content), main_info, font=f_reg, fill=0)
        
    # Standard-Darstellung für regulären Unterricht
    else:
        main_info = f"{lesson_data['fach']} | {lesson_data['klasse']} ({lesson_data['lehrer']})"
        draw.text((5, y_content), main_info, font=f_reg, fill=0)

def update_display_logic(data, message, conf):
    """
    Baut das komplette visuelle Layout (die Leinwand) zusammen und sendet 
    es an das Hardware-E-Paper über den SPI-Bus.
    """
    if shutdown_event.is_set(): return 
    message = message or "" # Schutz vor TypeError (NoneType), falls API leer antwortet

    if epd2in13_V3 is None: 
        print(f"INFO: Display-Update (Simulation): {message}")
        return
        
    with display_lock: # Sperrt das SPI-Interface für andere Threads
        try: 
            epd = epd2in13_V3.EPD()
            epd.init()
            
            # Erstellt ein leeres, weißes (255) 1-Bit-Bild (Schwarzweiß) mit Displaymaßen
            image = Image.new('1', (epd.height, epd.width), 255) 
            draw = ImageDraw.Draw(image) 
            
            # Schriften laden
            init_fonts()
            f_mega = GLOBAL_FONTS['mega']
            f_large = GLOBAL_FONTS['large']
            f_med = GLOBAL_FONTS['med']
            f_reg = GLOBAL_FONTS['reg']
            f_small = GLOBAL_FONTS['small']

            now = get_now()
            
            # ---------------- KOPFZEILE ----------------
            draw.rectangle((0, 0, 250, 24), fill=0)
            draw.text((5, 3), conf.get('ROOM_NAME', 'Unbekannt'), font=f_med, fill=255)
            time_str = now.strftime("%d.%m.%Y %H:%M")
            draw.text((120, 5), time_str, font=f_small, fill=255)

            # ---------------- HAUPTBEREICH ----------------
            if data and isinstance(data, dict) and (data.get('current') or data.get('next')):
                curr_lesson = data.get('current')
                next_lesson = data.get('next')
                
                if curr_lesson:
                    draw_lesson_block(draw, curr_lesson, 30, "JETZT:", f_small, f_reg, f_med)
                else:
                    draw.text((5, 35), message, font=f_large, fill=0)
                
                # Horizontale Trennlinie in der Mitte
                draw.line((5, 68, 245, 68), fill=0, width=1)
                
                if next_lesson:
                    draw_lesson_block(draw, next_lesson, 74, "DANACH:", f_small, f_reg, f_med)
                else:
                    msg_text = "Kein Unterricht mehr heute." if "Unterrichtsende" not in message else "Bis morgen!"
                    draw.text((5, 74), "DANACH:", font=f_small, fill=0)
                    draw.text((5, 90), msg_text, font=f_reg, fill=0)
            else:
                # Aufgeräumte Einzelmeldung zentrieren (z.B. am Wochenende)
                text_w = get_text_width(draw, message, f_mega)
                x_pos = (250 - text_w) / 2 if text_w < 250 else 2
                draw.text((x_pos, 60), message, font=f_mega, fill=0)

            # Das fertige Bitmap-Bild über das SPI-Kabel an den Controller des E-Papers flashen
            epd.display(epd.getbuffer(image))
            
            # WICHTIG: Das E-Paper in den Deep-Sleep schicken. Spart Strom und schont das Panel!
            epd.sleep()
        except Exception as e:
            print(f"Hardware-Fehler (Display): {e}")


# ==============================================================================
# 7. STEUERUNGS-EBENE: HINTERGRUND-LOOP & TEST-ROUTINE
# ==============================================================================
def run_display_test_sequence():
    """
    Spielt Test-Szenarien nacheinander auf dem Hardware-Display ab.
    Dient der Diagnose und um Schülern zu zeigen, wie das Interface reagiert.
    """
    global test_mode_active, current_display_data, current_display_msg, force_update_flag, state_lock
    
    with state_lock:
        test_mode_active = True
        
    conf = get_cached_config()
    
    # Hartcodierte Test-Daten, orientiert an deinen Fächern für ein realistisches UI
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
        time.sleep(4) # Bild für 4 Sekunden stehen lassen
        
    # Nach dem Test den Normalbetrieb wieder aufnehmen
    with state_lock:
        test_mode_active = False
        force_update_flag = True

def background_loop():
    """
    Der Herzmuskel der Software. Eine Endlosschleife (im eigenen Thread), die asynchron 
    Zeiten vergleicht, I2C-Sensoren überwacht und Updates ausführt.
    """
    global force_update_flag, show_demo_once, current_display_data, current_display_msg, test_mode_active, state_lock
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None

    while not shutdown_event.is_set():
        # Sicheres Auslesen des Test-Status über unser Lock
        with state_lock:
            is_testing = test_mode_active
            
        if is_testing:
            shutdown_event.wait(1)
            continue

        conf = get_cached_config()
        if not conf:
            shutdown_event.wait(5)
            continue

        # 1. Wir generieren eine Liste von Uhrzeiten, an denen sich auf dem Plan etwas ändert
        schedule = conf.get("SCHEDULE", {})
        lessons_conf = schedule.get("LESSONS", [])
        dyn_update_times = set() # Ein "Set" verhindert doppelte Einträge automatisch
        
        if isinstance(lessons_conf, list):
            for l in lessons_conf:
                start_t = l.get("start")
                end_t = l.get("end")
                if start_t: 
                    dyn_update_times.add(start_t)
                    try:
                        # Auch hier: Wir berechnen den 5-Minuten Vorlauf für das Display
                        h, m = map(int, str(start_t).split(":"))
                        dt = datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=5)
                        dyn_update_times.add(dt.strftime("%H:%M"))
                    except Exception: 
                        pass # Leere oder falsche Startzeiten abfangen (Bug-Fix)
                if end_t: 
                    dyn_update_times.add(end_t)
        
        # Auch Pausenzeiten und Schulbeginn/-ende ins Set aufnehmen
        for b in schedule.get("BREAKS", []):
            if b.get("start"): dyn_update_times.add(b.get("start"))
            if b.get("end"): dyn_update_times.add(b.get("end"))
            
        dyn_update_times.add(schedule.get("DAY_START", "07:55"))
        dyn_update_times.add(schedule.get("DAY_END", "15:30"))
        
        update_times = list(dyn_update_times)

        # 2. Uhrzeiten vergleichen
        now_time_system = time.time() 
        current_dt = get_now()
        current_hm = current_dt.strftime("%H:%M")
        current_time_obj = current_dt.time()
        
        # PÄDAGOGISCHER HINTERGRUND: Ressourcen schonen
        # Falls wir außerhalb der Schulzeit sind, blockieren wir regelmäßige Auto-Updates
        # Das schont nachts das E-Paper-Panel. Ein manueller Touch funktioniert aber weiterhin!
        try:
            ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
            de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
            active_start = datetime.time(max(0, ds_h - 1), ds_m)
            active_end = datetime.time(min(23, de_h + 1), de_m)
            is_active_hours = active_start <= current_time_obj <= active_end
        except Exception:
            is_active_hours = True 

        # 3. Logik: Haben wir einen exakten Treffer oder ist der Intervall abgelaufen?
        is_exact_time = (current_hm in update_times) and (last_minute_triggered != current_hm)
        is_interval_reached = (now_time_system - last_update >= conf.get('AUTO_UPDATE_SECONDS', 900)) and is_active_hours

        # 4. Hardware Touch-Logik
        if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
            if now_time_system - last_touch_time > 5.0: # 5 Sekunden Cooldown (Entprellen)
                print(f"\n[TOUCH {datetime.datetime.now().strftime('%H:%M:%S')}] Display beruehrt! Update wird vorbereitet...")
                with state_lock:
                    force_update_flag = True
            last_touch_time = now_time_system

        # Sicheres Auslesen der Flags für diesen Durchlauf
        with state_lock:
            current_force_update = force_update_flag
            current_show_demo = show_demo_once

        # 5. Der finale Update-Befehl
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
                
                # Cache-Daten für die Flask Web-Oberfläche thread-sicher aktualisieren
                with state_lock:
                    current_display_data = data
                    current_display_msg = err

                current_date = current_dt.strftime("%Y-%m-%d")
                is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"]
                
                # Wenn am Wochenende nichts passiert, überspringen wir unnötige Display-Draws
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
            
        # Kurze Pause (500ms), um die CPU nicht mit 100% auszulasten (Schont den kleinen Pi Zero)
        shutdown_event.wait(0.5)


# ==============================================================================
# 8. WEB-EBENE: FLASK ADMIN-INTERFACE & ROUTEN
# ==============================================================================
def check_auth(username, password):
    """Überprüft die Benutzerdaten für das Webinterface über den Datei-Cache."""
    conf = get_cached_config()
    u = conf.get('ADMIN_USER', 'admin')
    p = conf.get('ADMIN_PASS', 'tuerschild')
    return username == u and password == p

def authenticate():
    """Stellt die Anmeldeanforderung an den Browser (HTTP 401 Basic Auth)."""
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
            
            <div class="section-title">Gerätesteuerung</div>
            <div class="btn-group">
                <!-- 
                PÄDAGOGISCHER HINTERGRUND: CSRF-Schutz (Cross-Site Request Forgery)
                Aktionen, die den Serverzustand verändern, nutzen hier POST statt GET. 
                Das ist eine elementare Sicherheitsregel, damit externe, bösartige Links
                nicht heimlich Updates oder Schalter am Türschild betätigen können.
                -->
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
            
            <p class="footer">Status: {{ now }}{% if sim_active %} <br><strong style="color: #dc2626;">(ZEIT WIRD SIMULIERT)</strong>{% endif %}</p>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
@requires_auth
def index():
    """Die Hauptseite liefert den aktuellen Cache aus, ohne eine langsame API-Anfrage zu erzwingen."""
    conf = get_cached_config()
    global simulated_datetime, state_lock
    
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
    """Ermöglicht das 'Zeitreisen' zur Evaluation von zukünftigen Stundenplänen."""
    global simulated_datetime, force_update_flag, state_lock
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
    """Setzt die simulierte Uhr wieder auf die echte Systemzeit zurück."""
    global simulated_datetime, force_update_flag, state_lock
    with state_lock:
        simulated_datetime = None
        force_update_flag = True
    return redirect('/')

@app.route('/save', methods=['POST'])
@requires_auth
def save():
    """Speichert Konfigurationen über POST. Verhindert Manipulation über URL-Parameter."""
    global force_update_flag, state_lock
    conf = get_cached_config()
    if conf:
        conf['ROOM_NAME'] = request.form.get('ROOM_NAME')
        try:
            val = int(request.form.get('AUTO_UPDATE_SECONDS', 900))
            conf['AUTO_UPDATE_SECONDS'] = max(60, min(val, 86400)) # Schutz vor zu kleinen Intervallen
        except Exception:
            pass
        save_config(conf)
        with state_lock:
            force_update_flag = True
    return redirect('/')

@app.route('/update', methods=['POST'])
@requires_auth
def trigger_update():
    global force_update_flag, state_lock
    with state_lock:
        force_update_flag = True
    return redirect('/')

@app.route('/demo', methods=['POST'])
@requires_auth
def trigger_demo():
    """Lädt Präsentationsdaten (simuliert) auf das E-Paper."""
    global force_update_flag, show_demo_once, state_lock
    with state_lock:
        show_demo_once = True
        force_update_flag = True
    return redirect('/')

@app.route('/test_all', methods=['POST'])
@requires_auth
def trigger_test_all():
    """Triggert den Anzeigetest in einem separaten Thread, damit das Web-Interface nicht blockiert."""
    global test_mode_active, state_lock
    with state_lock:
        is_testing = test_mode_active
        
    if not is_testing:
        threading.Thread(target=run_display_test_sequence, daemon=True).start()
    return redirect('/')

@app.route('/toggle', methods=['POST'])
@requires_auth
def toggle_display():
    global force_update_flag, state_lock
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
    global force_update_flag, state_lock
    conf = get_cached_config()
    if conf:
        conf['TOUCH_ACTIVE'] = not conf.get('TOUCH_ACTIVE', True)
        save_config(conf)
        with state_lock:
            force_update_flag = True
    return redirect('/')


# ==============================================================================
# 9. START-EBENE: HAUPTPROGRAMM (ENTRY POINT)
# ==============================================================================
if __name__ == '__main__':
    try:
        # Einmaliger Hardware-Reset beim Start, falls sich der Touch-Controller 
        # durch eine Spannungsschwankung aufgehängt hat.
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

        # Der Hintergrund-Loop wird gestartet (daemon=True bedeutet, er wird 
        # automatisch beendet, wenn das Hauptprogramm endet).
        threading.Thread(target=background_loop, daemon=True).start()
            
        print(f" * Admin-Interface (Localhost): http://127.0.0.1:5000")
        
        # Start des sicheren Produktions-Webservers Waitress.
        # PÄDAGOGISCHER HINTERGRUND (WSGI vs Proxy): 
        # Flask's eigener 'app.run()' Server ist nicht für echte Netzwerke gedacht. 
        # Waitress wickelt als WSGI-Server die Python-HTTP-Requests performant ab.
        # Nginx (wie in der Anleitung eingerichtet) fungiert dann als noch sichererer 
        # Proxy davor und wickelt die SSL/HTTPS-Verschlüsselung ab.
        serve(app, host='127.0.0.1', port=5000)
        
    except KeyboardInterrupt:
        # Wird der Prozess manuell gestoppt (Strg+C), geben wir das Event-Signal
        shutdown_event.set()
    finally:
        # PÄDAGOGISCHER HINTERGRUND: Sauberer Exit
        # Wenn das Programm beendet wird (ob durch Fehler oder absichtlich), 
        # MÜSSEN Hardware-Ressourcen (GPIO-Pins, SPI) wieder freigegeben werden, 
        # sonst bleibt das System beim nächsten Start blockiert.
        shutdown_event.set()
        if GPIO: GPIO.cleanup()
        
        # Sicherstellen, dass epd2in13_V3 geladen ist, bevor module_exit aufgerufen wird
        if epd2in13_V3 is not None:
            with display_lock:
                try:
                    epd = epd2in13_V3.EPD()
                    epd.init()
                    epd.Clear(0xFF) # Aus Datenschutz-Gründen das E-Paper beim Ausschalten "löschen"
                    epd.sleep()
                    epd2in13_V3.epdconfig.module_exit()
                except Exception: 
                    pass
        sys.exit(0)
