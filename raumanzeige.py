#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
==============================================================================
WebUntis E-Paper Tuerschild - Einstiegspunkt
==============================================================================
Ein Projekt fuer den schulischen Einsatz (Raspberry Pi Zero 2 W)

Dieses Skript startet das Tuerschild. Der eigentliche Programmcode liegt im
Paket 'tuerschild', aufgeteilt in Ebenen, die aufeinander aufbauen:

    konstanten     feste Werte, keine Abhaengigkeiten
    zustand        Datenstrukturen und gemeinsamer Zustand
    konfiguration  config.json lesen und schreiben, Uhrzeit
    hardware       GPIO, I2C, Displaytreiber, Schriftarten
    anzeige        Layout und Zeichnen auf dem E-Paper
    untis          Abruf und Auswertung der Stundenplandaten
    web            Flask-Oberflaeche zur Administration
    steuerung      Hintergrundschleife, die alles zusammenfuehrt

Hier bleibt nur, was zum Starten gehoert: Hardware vorbereiten, Schleife und
Webserver anwerfen, und beim Beenden alles sauber schliessen.

Technische Schwerpunkte der Architektur:
- Nebenlaeufigkeit (Multithreading) & Ressourcen-Sperren (Locks)
- Kryptographie (Passwort-Hashing & CSRF-Tokens)
- Sicherheit (XSS-Vermeidung, Brute-Force Rate-Limiting)
- Objektorientierung (State-Kapselung in AppState, Dataclasses)
- Ausfallsicherheit (Atomare Dateizugriffe, Graceful Degradation)
- Prinzip der geringsten Privilegien (PoLP fuer Systembefehle)
"""
import logging
import sys
import threading
import time

# ------------------------------------------------------------------------------
# LOGGING KONFIGURATION
# ------------------------------------------------------------------------------
# Das Standard-Logging von Python. Ersetzt simple print()-Befehle.
# INFO zeigt normale Systemereignisse an. Fuer eine tiefe Hardware-Fehlersuche
# (z.B. I2C Bus Aussetzer) kann das Level auf logging.DEBUG gestellt werden.
#
# ACHTUNG, DIE REIHENFOLGE IST WESENTLICH:
# Diese Konfiguration muss VOR dem Import des Pakets stehen. Beim Laden von
# tuerschild.hardware werden bereits Warnungen ausgegeben, falls die
# Hardware-Bibliotheken fehlen. Die erste Log-Ausgabe eines Programms legt die
# Voreinstellung fest - danach bleibt basicConfig() wirkungslos. Stuenden die
# Importe zuerst, verloeren wir fuer den gesamten Programmlauf die Zeitstempel
# und saemtliche INFO-Meldungen, einschliesslich der Startausgabe mit der
# Netzwerkadresse. Die Testdatei tests/test_startausgabe.py haelt das fest.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from waitress import serve  # noqa: E402

from tuerschild import hardware  # noqa: E402
from tuerschild.hardware import (GPIO, clear_touch_interrupt_via_i2c,  # noqa: E402
                                 init_fonts)
from tuerschild.konfiguration import get_cached_config  # noqa: E402
from tuerschild.konstanten import TOUCH_RST_PIN  # noqa: E402
from tuerschild.steuerung import background_loop  # noqa: E402
from tuerschild.web import app, get_local_ip  # noqa: E402
from tuerschild.zustand import app_state  # noqa: E402


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
            
        # An welche Netzwerkkarte binden wir uns? Voreinstellung ist der
        # Localhost: Dann ist das Interface nur auf dem Pi selbst erreichbar,
        # und der Zugriff von aussen laeuft zwingend ueber den Reverse Proxy
        # (Nginx), der die Verbindung verschluesselt. Wer keinen Proxy
        # einsetzt, kann in der config.json "WEB_HOST": "0.0.0.0" setzen -
        # siehe die Warnung weiter unten.
        conf_start = get_cached_config()
        web_host = str(conf_start.get('WEB_HOST', '127.0.0.1')).strip() or '127.0.0.1'
        is_loopback = web_host in ('127.0.0.1', 'localhost', '::1')
        local_ip = get_local_ip()

        # Optionale, fest vorgegebene Adresse fuer abweichende Proxy-Aufbauten
        # (anderer Port, eigener Hostname, nur HTTP). Fehlt das Schema, ergaenzen
        # wir https:// - sonst erkennt das Terminal die Zeile nicht als Adresse.
        public_url = str(conf_start.get('WEB_PUBLIC_URL', '')).strip()
        if public_url and "://" not in public_url:
            public_url = f"https://{public_url}"

        # Alle Adressen werden bewusst als vollstaendige URL mit Schema
        # ausgegeben. Nur dann erkennen sie die gaengigen Terminals als Link
        # und man kann sie mit Strg+Klick direkt oeffnen.
        logging.info(" * Admin-Interface (auf dem Pi):  http://127.0.0.1:5000")

        if public_url:
            logging.info(f" * Admin-Interface (im Netzwerk): {public_url}")
        elif not is_loopback:
            # Direkt im Netz erreichbar: Hier kennen wir das Protokoll sicher,
            # denn Waitress selbst spricht ausschliesslich unverschluesseltes HTTP.
            if local_ip:
                logging.info(f" * Admin-Interface (im Netzwerk): http://{local_ip}:5000")
            logging.warning(
                " * ACHTUNG: WEB_HOST ist offen konfiguriert. Das Interface ist ohne "
                "Verschluesselung im Netzwerk erreichbar; das Admin-Passwort wird bei "
                "jedem Aufruf praktisch im Klartext uebertragen."
            )
        elif local_ip:
            # Hinter dem Reverse Proxy aus der Installationsanleitung: Der
            # lauscht auf Port 443 mit HTTPS. Weicht der eigene Aufbau davon ab,
            # laesst sich die Adresse per WEB_PUBLIC_URL fest vorgeben.
            logging.info(f" * Admin-Interface (im Netzwerk): https://{local_ip}  (ueber den Reverse Proxy)")
        else:
            logging.warning(" * Keine Netzwerkadresse gefunden - besteht eine WLAN-Verbindung?")

        # Flasks eingebauter Server ist nicht netzwerksicher. Daher wickelt
        # 'Waitress' als robuster WSGI-Server die HTTP-Requests ab.
        serve(app, host=web_host, port=5000)
        
    except KeyboardInterrupt:
        # Fängt das STRG+C Signal des Nutzers im Terminal ab
        app_state.shutdown_event.set()
    finally:
        # Wird immer ausgeführt (auch bei Abstürzen oder beim sudo-Reboot), 
        # um die Hardware sicher herunterzufahren und Ressourcen freizugeben.
        app_state.shutdown_event.set()
        if GPIO: GPIO.cleanup()
        
        if hardware.epd2in13_V3 is not None:
            # 5 Sekunden Timeout verhindern, dass das Skript endlos hängt (Deadlock)
            if app_state.display_lock.acquire(timeout=5):
                try:
                    epd = hardware.epd2in13_V3.EPD()
                    epd.init()
                    epd.Clear(0xFF)
                    epd.sleep()  # Deep-Sleep (Bewahrt die Tinte vor dem Einbrennen)
                    hardware.epd2in13_V3.epdconfig.module_exit()
                except OSError as e: 
                    logging.debug(f"Fehler beim finalen Display-Clear: {e}")
                finally:
                    app_state.display_lock.release()
            else:
                logging.error("WARNUNG: Display-Lock konnte beim Beenden nicht erlangt werden.")
        sys.exit(0)
