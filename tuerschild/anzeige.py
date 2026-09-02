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
                         UI_BLOCK_DANACH_Y, UI_BLOCK_JETZT_Y, UI_ELLIPSIS,
                         UI_HEADER_GAP, UI_HEADER_HEIGHT, UI_HEIGHT, UI_LINE_Y,
                         UI_MARGIN, UI_STALE_ZEICHEN, UI_WIDTH,
                         WOCHENTAGE_KURZ)
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

def zeichne_meldung(draw: ImageDraw.ImageDraw, message: str, schriften) -> None:
    """
    Setzt eine Meldung ("Raum ist frei", "Unterrichtsende") so gross wie
    moeglich und mittig in den freien Bereich unter der Kopfzeile.

    WARUM DAS NOETIG WAR:
    Frueher stand hier eine feste Schriftgroesse von 16 Pixeln, und der Text
    begann bei y=60. Darueber blieben 36, darunter 46 Pixel leer. Ausgerechnet
    der Zustand, den man ausserhalb der Unterrichtszeiten am haeufigsten sieht,
    nutzte das Panel also am schlechtesten - und aus zwei Metern Entfernung war
    er unnoetig klein. Die groesste geladene Schrift (24 Pixel) kam im
    Zeichencode ueberhaupt nicht vor.

    'schriften' kommt von gross nach klein. Genommen wird die erste, in die
    JEDE Zeile passt: "Unterrichtsende" braucht bei 24 Pixeln 220 von 240
    verfuegbaren und bleibt gross, "Schoenes Wochenende!" braeuchte 305 und
    faellt deshalb eine Stufe zurueck.
    """
    zeilen = (message or "").split("\n")
    verfuegbar = UI_WIDTH - 2 * UI_MARGIN

    for schrift in schriften:
        if all(get_text_width(draw, z, schrift) <= verfuegbar for z in zeilen):
            break
    else:
        # Auch die kleinste Stufe reicht nicht - dann wird gekuerzt.
        schrift = schriften[-1]
        zeilen = [truncate_to_width(draw, z, schrift, verfuegbar) for z in zeilen]

    zeilenhoehe = schrift.size + 5
    frei = UI_HEIGHT - UI_HEADER_HEIGHT
    y = UI_HEADER_HEIGHT + (frei - zeilenhoehe * len(zeilen)) // 2

    for zeile in zeilen:
        breite = get_text_width(draw, zeile, schrift)
        x = (UI_WIDTH - breite) / 2 if breite < UI_WIDTH else 2
        draw.text((x, y), zeile, font=schrift, fill=0)
        y += zeilenhoehe


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

def zeichne_anzeige(data: Optional[Dict[str, Optional[Lesson]]], message: str, conf: Dict[str, Any], stale: bool = False) -> Image.Image:
    """
    Zeichnet das Bild, das auf dem E-Paper steht - und nur das. Es wird hier
    weder Hardware angesprochen noch etwas gesendet.

    WARUM DAS VOM SENDEN GETRENNT IST:
    Das Web-Interface zeigt eine Vorschau der aktuellen Anzeige. Frueher war
    das eine freie Nachbildung in HTML: aehnlich, aber eben nicht dasselbe -
    zwei Fassungen desselben Layouts, die zwangslaeufig auseinanderlaufen. Vor
    allem zeigte die Nachbildung nicht, was auf 250x122 Pixeln tatsaechlich
    Platz hat; gerade die Kuerzungen sah man dort nie.
    Seit das Zeichnen fuer sich steht, benutzt die Vorschau genau diese
    Funktion und zeigt Pixel fuer Pixel dasselbe Bild wie das Schild.

    'stale' markiert Daten, die aus der Offline-Rücklage stammen (WebUntis war
    nicht erreichbar). In dem Fall setzen wir ein kleines Ausrufezeichen in die
    Kopfzeile: Der Plan stimmt sehr wahrscheinlich noch, könnte aber eine
    kurzfristige Änderung von heute nicht enthalten.
    """
    message = message or ""

    # Die Maße kommen aus den Konstanten, nicht vom Treiber. Das gesamte
    # Layout darunter rechnet ohnehin mit UI_WIDTH und UI_HEIGHT; ein Bild in
    # Treibergröße zu erzeugen und dann in fester Größe zu bemalen, wäre nur
    # scheinbar allgemeiner. So lässt sich außerdem ohne Display zeichnen.
    image = Image.new('1', (UI_WIDTH, UI_HEIGHT), 255)
    draw = ImageDraw.Draw(image)

    init_fonts()
    f_mega = app_state.global_fonts['mega']
    f_huge = app_state.global_fonts['huge']
    f_large = app_state.global_fonts['large']
    f_med = app_state.global_fonts['med']
    f_reg = app_state.global_fonts['reg']
    f_small = app_state.global_fonts['small']

    now = get_now()

    # --- KOPFZEILE ---
    # Von rechts nach links aufgebaut: erst das Ausrufezeichen, dann die
    # Uhrzeit, und der Raumname bekommt, was uebrig bleibt.
    #
    # WARUM NICHT MEHR MIT FESTEN X-WERTEN: Die Uhrzeit stand frueher starr auf
    # x=120, und der Raumname wurde gar nicht gekuerzt. Ab etwa neun breiten
    # Zeichen schrieben sich beide uebereinander - im Formular erlaubt sind
    # aber 40 Zeichen. "Chemie-Vorbereitung" ergab unlesbaren Matsch.
    draw.rectangle((0, 0, UI_WIDTH, UI_HEADER_HEIGHT), fill=0)

    rechter_rand = UI_WIDTH - UI_MARGIN

    # Offline-Hinweis ganz rechts in der Kopfzeile: ein invertiertes
    # Warndreieck. Die Kastenbreite folgt der Zeichenbreite, damit ein anderes
    # Zeichen in UI_STALE_ZEICHEN nicht aus dem Kasten laeuft.
    if stale:
        zeichen_breite = get_text_width(draw, UI_STALE_ZEICHEN, f_med)
        kasten_links = rechter_rand - zeichen_breite - 2 * UI_BADGE_PADDING
        draw.rectangle((kasten_links, 4, rechter_rand, UI_HEADER_HEIGHT - 5), fill=255)
        draw.text((kasten_links + UI_BADGE_PADDING, 3), UI_STALE_ZEICHEN,
                  font=f_med, fill=0)
        rechter_rand = kasten_links - UI_BADGE_GAP

    # Der Wochentag steht vorn: Im Schulalltag ist er die Angabe, die man am
    # ehesten aus dem Blick verliert.
    time_str = (f"{WOCHENTAGE_KURZ[now.weekday()]} "
                f"{now.strftime('%d.%m.%Y %H:%M')}")
    zeit_x = rechter_rand - get_text_width(draw, time_str, f_small)
    draw.text((zeit_x, 5), time_str, font=f_small, fill=255)

    raum = truncate_to_width(draw, conf.get('ROOM_NAME', 'Unbekannt'), f_med,
                             zeit_x - UI_MARGIN - UI_HEADER_GAP)
    draw.text((UI_MARGIN, 3), raum, font=f_med, fill=255)

    # --- HAUPTBEREICH (Unterricht) ---
    if data and (data.get('current') or data.get('next')):
        curr_lesson = data.get('current')
        next_lesson = data.get('next')

        if curr_lesson:
            draw_lesson_block(draw, curr_lesson, UI_BLOCK_JETZT_Y, "JETZT:", f_small, f_reg, f_med)
        else:
            draw.text((UI_MARGIN, UI_BLOCK_JETZT_Y + 8), message, font=f_large, fill=0)

        draw.line((UI_MARGIN, UI_LINE_Y, UI_WIDTH - UI_MARGIN, UI_LINE_Y), fill=0, width=1)

        if next_lesson:
            draw_lesson_block(draw, next_lesson, UI_BLOCK_DANACH_Y, "DANACH:", f_small, f_reg, f_med)
        else:
            msg_text = "Kein Unterricht mehr heute." if "Unterrichtsende" not in message else "Bis morgen!"
            draw.text((UI_MARGIN, UI_BLOCK_DANACH_Y), "DANACH:", font=f_small, fill=0)
            draw.text((UI_MARGIN, UI_BLOCK_DANACH_Y + 16), msg_text, font=f_reg, fill=0)

    # --- HAUPTBEREICH (Freistunde / Ferien) ---
    else:
        # Mehrzeilige Texte (wie "Unterrichtsfrei!\n(Ferienzeit)") werden
        # ebenso behandelt wie einzeilige: so gross wie moeglich, mittig.
        zeichne_meldung(draw, message, (f_huge, f_large, f_mega))

    return image


def update_display_logic(data: Optional[Dict[str, Optional[Lesson]]], message: str, conf: Dict[str, Any], stale: bool = False) -> None:
    """
    Zeichnet die Anzeige und überträgt sie an den Waveshare-Controller.
    Das Zeichnen selbst steht in zeichne_anzeige().
    """
    if app_state.shutdown_event.is_set(): return

    if hardware.epd2in13_V3 is None:
        logging.info(f"Display-Update (Simulation): {message or ''}")
        return

    # Thread-Lock: Garantiert, dass wir das Display nicht versehentlich
    # von zwei Threads gleichzeitig flashen (SPI-Kollision).
    with app_state.display_lock:
        try:
            epd = hardware.epd2in13_V3.EPD()
            epd.init()

            image = zeichne_anzeige(data, message, conf, stale)

            # Das fertige Bitmap an den Hardware-Controller übertragen
            epd.display(epd.getbuffer(image))
            # EXTREM WICHTIG: Das Display am Ende in den Deep-Sleep schicken!
            # Steht das E-Paper dauerhaft unter Spannung, brennt die E-Tinte ein.
            epd.sleep()
        except Exception as e:
            logging.error(f"Hardware-Fehler (Display): {e}")

