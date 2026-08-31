"""
==============================================================================
Darstellungs-Ebene: Layout und Zeichnen auf dem E-Paper
==============================================================================
Erzeugt aus den Plandaten ein 1-Bit-Bild und uebergibt es dem Displaytreiber.

HINWEIS ZUM ZUGRIFF AUF DEN TREIBER:
Der Treiber wird bewusst als "hardware.epd2in13_V3" angesprochen und nicht
ueber einen direkt importierten Namen. Nur so wirkt ein Ersetzen des Treibers
in hardware.py auch hier - worauf die Testsuite beruht, die das echte Display
durch eine Attrappe ersetzt.
"""
import logging
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from . import hardware
from .hardware import init_fonts
from .konfiguration import get_now
from .konstanten import (STATUS_LABELS, UI_BADGE_GAP, UI_BADGE_PADDING,
                         UI_ELLIPSIS, UI_HEADER_HEIGHT, UI_LINE_Y,
                         UI_MARGIN, UI_WIDTH)
from .zustand import Lesson, app_state

def get_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """Hilfsfunktion: Berechnet die exakte Pixelbreite eines Textes (wichtig fürs Zentrieren)."""
    try: return int(draw.textlength(text, font=font))
    except AttributeError:
        # Abwärtskompatibilität für ältere Pillow-Versionen auf älteren Linux-Distributionen
        try: return draw.textbbox((0,0), text, font=font)[2] 
        except AttributeError: return draw.textsize(text, font=font)[0] 

def truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """
    Kürzt einen Text auf die verfügbare Pixelbreite und hängt "…" an.

    Getrennt wird bevorzugt an einer Wortgrenze: "Raumaenderung nach…" ist
    lesbarer als "Raumaenderung nac…". Passt nicht einmal das erste Wort,
    wird zeichenweise gekürzt, damit auch ein einzelnes sehr langes Wort
    (etwa eine Fach-Bezeichnung ohne Leerzeichen) nicht aus dem Display läuft.
    """
    if not text:
        return ""
    if get_text_width(draw, text, font) <= max_width:
        return text

    # Wortweise aufbauen, solange der Text samt Auslassungszeichen noch passt
    zeile = ""
    for wort in text.split():
        probe = f"{zeile} {wort}".strip()
        if get_text_width(draw, probe + UI_ELLIPSIS, font) > max_width:
            break
        zeile = probe
    if zeile:
        return zeile + UI_ELLIPSIS

    # Nicht einmal das erste Wort passt: zeichenweise kürzen
    for i in range(len(text), 0, -1):
        if get_text_width(draw, text[:i] + UI_ELLIPSIS, font) <= max_width:
            return text[:i] + UI_ELLIPSIS
    return ""

def build_detail_line(draw: ImageDraw.ImageDraw, lesson: Lesson, font, max_width: int) -> str:
    """
    Setzt die Detailzeile (Klasse, Lehrkraft, Zusatzinfo) so zusammen, dass sie
    in die verfügbare Breite passt.

    PÄDAGOGISCHER HINTERGRUND (Priorisierung statt stumpfem Abschneiden):
    Die volle Zeile ist bei fast jeder Stunde mit Zusatzinfo zu lang - typisch
    sind 320 Pixel bei 240 verfügbaren. Würde man sie einfach hinten abschneiden,
    verlöre man genau das Ende des Info-Textes. Bei "Achtung: Raumaenderung nach
    In2" wäre das die Raumangabe, also die Information, für die jemand überhaupt
    vor der Tür steht.

    Deshalb geben wir gestaffelt nach. Die zweite Stufe nutzt aus, dass in
    WebUntis ohnehin nur das Kürzel der Lehrkraft hinterlegt ist ("Gk", "Ef"):
    Statt das Feld ganz zu streichen, entfällt erst nur die Beschriftung
    "Lehrkraft: ", die allein schon 57 Pixel kostet. Innerhalb der
    Schulgemeinschaft sind die Kürzel geläufig, das Kürzel für sich genommen
    ist also weiterhin verständlich.
    """
    klasse = f"Kl: {lesson.klasse}" if lesson.klasse else ""
    lehrkraft = f"Lehrkraft: {lesson.lehrer}" if lesson.lehrer else ""
    kuerzel = lesson.lehrer or ""
    info = lesson.stunden_info or ""

    for teile in ([klasse, lehrkraft, info],   # alles ausgeschrieben
                  [klasse, kuerzel, info],     # nur noch das Kuerzel der Lehrkraft
                  [klasse, info],              # Lehrkraft entfaellt ganz
                  [info]):                     # auch die Klasse entfaellt
        zeile = " | ".join(t for t in teile if t)
        if zeile and get_text_width(draw, zeile, font) <= max_width:
            return zeile

    # Auch für sich allein ist das wichtigste Stück noch zu breit
    return truncate_to_width(draw, info or klasse or lehrkraft, font, max_width)

def draw_lesson_block(draw: ImageDraw.ImageDraw, lesson: Lesson, y_offset: int, label_text: str, f_small, f_reg, f_med) -> None:
    """
    Zeichnet einen strukturierten Unterrichtsblock (JETZT oder DANACH) als Grafik.
    Wertet die Status-Codes (cancelled = Ausfall, irregular = Vertretung) aus
    und hebt diese farblich durch Invertierung (schwarzer Kasten) hervor.
    """
    header_text = f"{label_text} {lesson.stunde} ({lesson.zeit})"
    draw.text((UI_MARGIN, y_offset), header_text, font=f_small, fill=0) 
    
    status = lesson.status_code
    
    # PÄDAGOGISCHER HINTERGRUND (Layout-Optimierung):
    # Wir teilen den verfügbaren Platz im Stundenblock in zwei Zeilen auf, 
    # um lange Namen und Zusatzinfos (Lehrer/Klasse) gleichzeitig anzuzeigen.
    y_content = y_offset + 13 
    
    # Welches Fach zeigen wir? Bevorzuge den langen Namen (z.B. "Biologie-Profilkurs")
    fach_anzeige = lesson.fach_lang if lesson.fach_lang else lesson.fach
    if not fach_anzeige:
        fach_anzeige = "Kein Fach"
        
    # ZEILE 1: Tag (Ausfall/Vertretung) und das Fach
    # "FÄLLT AUS" wurde zu "AUSFALL" gekürzt, damit längere Fachnamen daneben passen.
    label = STATUS_LABELS.get(status)

    if label:
        # Die Breite des schwarzen Kastens wird aus der tatsächlichen Textbreite
        # berechnet, statt sie fest im Code zu hinterlegen. Feste Pixelwerte
        # passen immer nur zu genau einer Schriftart in genau einer Größe und
        # zu genau diesem einen Wort - schon eine Übersetzung des Etiketts oder
        # eine andere Schrift würde den Text sonst aus dem Kasten laufen lassen.
        label_w = get_text_width(draw, label, f_small)
        box_right = UI_MARGIN + label_w + 2 * UI_BADGE_PADDING
        draw.rectangle((UI_MARGIN, y_content, box_right, y_content + 14), fill=0)
        draw.text((UI_MARGIN + UI_BADGE_PADDING, y_content + 1), label, font=f_small, fill=255)
        fach_x = box_right + UI_BADGE_GAP
    else:
        # Regulärer Unterricht: Das Fach beginnt direkt am linken Rand.
        fach_x = UI_MARGIN

    # Der Platz für den Fachnamen ist der Rest bis zum rechten Rand - neben
    # einem Status-Kasten also entsprechend weniger.
    fach_anzeige = truncate_to_width(draw, fach_anzeige, f_reg, UI_WIDTH - UI_MARGIN - fach_x)
    draw.text((fach_x, y_content), fach_anzeige, font=f_reg, fill=0)

    # ZEILE 2: Zusatzinfos (Klasse, Lehrkraft, Info-Text)
    y_details = y_content + 13
    detail_str = build_detail_line(draw, lesson, f_small, UI_WIDTH - 2 * UI_MARGIN)
    draw.text((UI_MARGIN, y_details), detail_str, font=f_small, fill=0)

def update_display_logic(data: Optional[Dict[str, Optional[Lesson]]], message: str, conf: Dict[str, Any], stale: bool = False) -> None:
    """
    Erstellt ein 1-Bit (Schwarz/Weiß) Bitmap-Bild des Stundenplans und sendet
    es an den Hardware-Controller des Waveshare E-Paper-Displays.

    'stale' markiert Daten, die aus der Offline-Rücklage stammen (WebUntis war
    nicht erreichbar). In dem Fall setzen wir ein kleines Ausrufezeichen in die
    Kopfzeile: Der Plan stimmt sehr wahrscheinlich noch, könnte aber eine
    kurzfristige Änderung von heute nicht enthalten.
    """
    if app_state.shutdown_event.is_set(): return 
    message = message or "" 

    if hardware.epd2in13_V3 is None: 
        logging.info(f"Display-Update (Simulation): {message}")
        return
        
    # Thread-Lock: Garantiert, dass wir das Display nicht versehentlich 
    # von zwei Threads gleichzeitig flashen (SPI-Kollision).
    with app_state.display_lock: 
        try: 
            epd = hardware.epd2in13_V3.EPD()
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

            # Offline-Hinweis: invertiertes Ausrufezeichen ganz rechts in der Kopfzeile.
            # Bewusst sehr klein gehalten - auf 250x122 Pixeln ist jeder Pixel knapp,
            # und der Stundenplan selbst bleibt die wichtigere Information.
            if stale:
                draw.rectangle((UI_WIDTH - 13, 4, UI_WIDTH - 3, UI_HEADER_HEIGHT - 5), fill=255)
                draw.text((UI_WIDTH - 10, 4), "!", font=f_small, fill=0)

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

