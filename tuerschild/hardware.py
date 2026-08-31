"""
==============================================================================
Hardware-Ebene: GPIO, I2C-Touch, Displaytreiber und Schriftarten
==============================================================================
Hier - und nur hier - werden die Bibliotheken der Hardware eingebunden.

TECHNISCHER HINTERGRUND: "Graceful Degradation" (Gutmuetiges Herabstufen)
Die Try-Except-Bloecke ermoeglichen es, das Programm auch auf einem normalen
Windows- oder Mac-Rechner ohne GPIO-Pins zu starten und weiterzuentwickeln.
Fehlt die Raspberry-Hardware, fangen wir den Fehler ab und protokollieren ihn
als Warnung, statt das Programm abstuerzen zu lassen. Genau darauf beruht auch,
dass die Testsuite ohne Raspberry Pi lauffaehig ist.
"""
import logging
import os
import sys

from PIL import ImageFont

from .konstanten import TOUCH_I2C_ADDR, WAVESHARE_LIB
from .zustand import app_state

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

# Pfad zu den E-Paper-Treibern des Herstellers (Waveshare) dynamisch hinzufügen.
# Der Pfad kommt aus konstanten.py und ist dort am Projektverzeichnis
# ausgerichtet - NICHT an diesem Modul. Wuerde er hier aus __file__ gebildet,
# zeigte er in das Paketverzeichnis und damit ins Leere.
if os.path.exists(WAVESHARE_LIB):
    sys.path.append(WAVESHARE_LIB)
else:
    # Ohne diesen Hinweis aeussert sich ein falscher Pfad nur als stiller
    # Simulationsmodus: Das Programm laeuft normal weiter, das Display bleibt
    # aber dunkel. Ein Fehler, der ohne Meldung sehr lange unentdeckt bleibt.
    logging.warning(f"Treiberverzeichnis nicht gefunden: {WAVESHARE_LIB} — "
                    "wurde das e-Paper-Repository geklont? "
                    "Siehe Installationsanleitung, Schritt 3.")

try:
    from waveshare_epd import epd2in13_V3
except ImportError as e:
    logging.warning(f"waveshare_epd Treiber nicht gefunden. ({e})")
    epd2in13_V3 = None


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

