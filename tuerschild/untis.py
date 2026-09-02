"""
==============================================================================
Daten-Ebene: Abruf und Auswertung der WebUntis-Daten
==============================================================================
Holt den Tagesplan fuer den konfigurierten Raum und entscheidet, welche Stunde
gerade laeuft und welche folgt. Enthaelt ausserdem die Offline-Ruecklage, die
das Schild bei einer Netzstoerung arbeitsfaehig haelt.
"""
import datetime
import logging
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import webuntis

from .konfiguration import get_now
from .konstanten import (ERR_NO_NETWORK, ERR_UNTIS_OFFLINE,
                         HOLIDAYS_CACHE_SECONDS)
from .zustand import Lesson, TimedLesson, app_state

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
            
    # Auch den langen Fach-Namen aus der API extrahieren
    fach_kurz = ", ".join([s.name for s in getattr(lesson, 'subjects', [])])
    fach_lang = ", ".join([getattr(s, 'long_name', '') for s in getattr(lesson, 'subjects', []) if getattr(s, 'long_name', '')])
    
    # Rückgabe als sicher typisierte Dataclass
    return Lesson(
        fach=fach_kurz,
        fach_lang=fach_lang if fach_lang else fach_kurz,
        lehrer=", ".join([t.name for t in getattr(lesson, 'teachers', [])]),
        klasse=", ".join([k.name for k in getattr(lesson, 'klassen', [])]),
        zeit=f"{start_str} - {lesson.end.strftime('%H:%M')}",
        stunde=stunde_name,
        status_code=getattr(lesson, 'code', None),
        stunden_info=" | ".join(info_parts)
    )

def resolve_timetable(timetable, conf: Dict[str, Any]) -> List[TimedLesson]:
    """
    Wandelt die rohen WebUntis-Objekte eines Tages einmalig in eigene
    Datensätze um und sortiert sie chronologisch.

    Das muss geschehen, SOLANGE DIE SITZUNG NOCH BESTEHT. Die Bibliothek liest
    Fach, Lehrkraft und Klasse erst beim Zugriff nach und stellt dafür nötigen-
    falls eine Netzwerkanfrage - auch dann, wenn man sie ausdrücklich um den
    Zwischenspeicher bittet. Nach dem Abmelden oder ohne Verbindung würde ein
    solcher Zugriff fehlschlagen oder unbegrenzt warten.

    Anders als früher lesen wir hier ALLE Stunden des Tages aus, nicht nur die
    beiden gerade benötigten. Nur so ist die Offline-Rücklage später vollständig
    verwendbar.
    """
    aufgeloest: List[TimedLesson] = []
    for rohdaten in timetable:
        start = getattr(rohdaten, 'start', None)
        ende = getattr(rohdaten, 'end', None)
        # Beschädigte Einträge ohne Zeitangabe überspringen
        if start is None or ende is None:
            continue
        try:
            stunde = parse_lesson(rohdaten, conf)
        except Exception as e:
            # Eine einzelne unlesbare Stunde darf nicht den ganzen Tag verwerfen
            logging.warning(f"Stunde konnte nicht ausgelesen werden, wird übersprungen: {e}")
            continue
        if stunde is not None:
            aufgeloest.append(TimedLesson(start=start, end=ende, lesson=stunde))

    aufgeloest.sort(key=lambda eintrag: eintrag.start)
    return aufgeloest

def select_lessons(lessons: List[TimedLesson], conf: Dict[str, Any], now: datetime.datetime) -> Tuple[Dict[str, Optional[Lesson]], str]:
    """
    Wählt aus einem bereits ausgelesenen Tagesplan die Stunde aus, die JETZT
    läuft, und die, die DANACH folgt. Erzeugt für unterrichtsfreie Zeiträume
    die passende Ersatzmeldung (Pause, Freistunde, Unterrichtsende).

    TECHNISCHER HINTERGRUND (Trennung von Laden und Auswerten):
    Diese Funktion arbeitet auf fertigen Daten und rührt weder Netzwerk noch
    WebUntis-Sitzung an. Genau deshalb ist sie von get_current_lesson()
    getrennt: Bei einem WLAN- oder WebUntis-Ausfall können wir sie erneut auf
    den zuletzt gespeicherten Tagesplan anwenden. Das Display wechselt dann auch
    offline zur richtigen Zeit auf die nächste Stunde, statt bei den Daten des
    Ausfallzeitpunkts stehenzubleiben.
    """
    if not lessons:
        return {"current": None, "next": None}, "Unterrichtsfrei"

    now_time = now.time()

    current_lesson = None
    next_lesson = None

    for eintrag in lessons:
        # 5-Minuten-Vorlauf: Das Display schaltet bereits 5 Min vor dem Klingeln auf die neue Stunde um
        lesson_start_buffered = eintrag.start - datetime.timedelta(minutes=5)

        if lesson_start_buffered <= now <= eintrag.end:
            current_lesson = eintrag.lesson
        elif eintrag.start > now and next_lesson is None:
            next_lesson = eintrag.lesson

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

    # Die Stunden sind bereits ausgelesen - hier wird nur noch ausgewählt.
    return {"current": current_lesson, "next": next_lesson}, message

def get_offline_fallback(conf: Dict[str, Any]) -> Optional[Tuple[Dict[str, Optional[Lesson]], str]]:
    """
    Liefert die Anzeige aus dem zuletzt erfolgreich abgerufenen Tagesplan.

    PÄDAGOGISCHER HINTERGRUND (Ausfallsicherheit):
    Ein kurzer WLAN-Aussetzer soll den kompletten Stundenplan nicht durch eine
    Fehlermeldung ersetzen - für die Person vor der Tür ist ein leicht
    veralteter Plan deutlich nützlicher als "WebUntis offline".
    Wir geben den Plan nur zurück, wenn er vom selben Kalendertag stammt.
    Der Plan von gestern wäre schlicht falsch, und eine falsche Anzeige ist
    schlechter als gar keine.

    Rückgabe: (daten, meldung) oder None, wenn keine brauchbare Rücklage existiert.
    """
    now = get_now()
    with app_state.state_lock:
        cached_lessons = app_state.cached_lessons
        cached_date = app_state.cached_lessons_date

    # Ausdrücklich auf None prüfen: Eine leere Liste ist eine gültige Rücklage
    # und bedeutet "heute findet nachweislich kein Unterricht statt".
    if cached_lessons is None or cached_date != now.date():
        return None

    # Neu auswerten statt die alte Anzeige zu konservieren: So stimmt auch
    # während des Ausfalls noch, welche Stunde gerade läuft. Die Daten sind
    # bereits vollständig ausgelesen, es wird also nichts nachgeladen.
    return select_lessons(cached_lessons, conf, now)

def ist_ferien_fehler(fehler: Exception) -> bool:
    """
    Erkennt die Fehler, mit denen WebUntis einen gesperrten Kalender meldet.

    Ausserhalb des Schuljahres gibt der Server den Stundenplan nicht heraus,
    sondern antwortet mit einem Fehler. Fuer das Schild ist das keine Stoerung,
    sondern schlicht: Ferien.
    """
    text = str(fehler).lower()
    return any(hinweis in text for hinweis in
               ("schoolyear", "schuljahr", "no valid", "date", "notallowed"))

def hole_stundenplan(session, raum, tag):
    """
    Holt den Tagesplan des Raums - moeglichst in der erweiterten Fassung.

    WARUM ERWEITERT: Die Bemerkungsfelder (info, lstext, substText) gibt
    WebUntis nur heraus, wenn der Abruf sie ausdruecklich anfordert. Der
    einfache Aufruf liefert sie nicht - an einer echten Schule nachgemessen:
    75 Stunden, kein einziges Bemerkungsfeld, waehrend dieselben 75 Stunden
    erweitert abgerufen Texte wie "Vocabulary and grammar test" enthielten.
    Das Tuerschild bereitet solche Texte auf und kuerzt sie bei Platzmangel
    gestaffelt - nur kamen sie bis dahin nie an. Beide Aufrufe gehen an
    dieselbe Schnittstelle und brauchen dieselben Rechte; der erweiterte
    schickt nur zusaetzliche Optionen mit.

    WARUM MIT RUECKFALL: Lehnt ein Server diese Optionen ab, waere ohne
    Ruecklage der ganze Plan weg. Ein leeres Schild ist ein schlechter Tausch
    fuer ein Bemerkungsfeld - dann lieber der einfache Aufruf ohne die Texte.
    Ein gesperrter Kalender wird davon ausgenommen: Dort ist der zweite
    Versuch zwecklos, und der Fehler soll die Ferien-Erkennung erreichen.
    """
    try:
        return session.timetable_extended(room=raum, start=tag, end=tag)
    except Exception as fehler:
        if ist_ferien_fehler(fehler):
            raise
        logging.warning("Erweiterter Stundenplan-Abruf fehlgeschlagen (%s) - "
                        "einfacher Abruf ohne Bemerkungsfelder.", fehler)
        return session.timetable(room=raum, start=tag, end=tag)

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

        # ----------------------------------------------------------------------
        # PÄDAGOGISCHER HINTERGRUND: Ferien-Erkennung & API-Schonung
        # Da sich Ferien nicht stündlich ändern, sparen wir teure API-Aufrufe, 
        # indem wir die Ferientermine für 24 Stunden im RAM cachen.
        # ----------------------------------------------------------------------
        now_ts = time.time()
        if app_state.cached_holidays is not None and (now_ts - app_state.last_holidays_fetch) < HOLIDAYS_CACHE_SECONDS:
            holidays = app_state.cached_holidays
        else:
            try:
                # Aktuelle Ferien abrufen (Standardabfrage ohne 'schoolyear')
                holidays = session.holidays()
                app_state.cached_holidays = holidays
                app_state.last_holidays_fetch = now_ts
            except Exception as e:
                logging.warning(f"Fehler beim Abrufen der Ferien: {e}")
                holidays = []
                
        for holiday in holidays:
            h_start = holiday.start.date() if isinstance(holiday.start, datetime.datetime) else holiday.start
            h_end = holiday.end.date() if isinstance(holiday.end, datetime.datetime) else holiday.end
            
            if h_start <= today <= h_end:
                return {"current": None, "next": None}, f"Schöne Ferien!\n({holiday.name})"
        
        # Am Wochenende (Samstag=5, Sonntag=6) API schonen
        if now.weekday() >= 5: 
            return {"current": None, "next": None}, "Schönes Wochenende!"
            
        try:
            timetable = hole_stundenplan(session, rooms[0], today)
        except Exception as e:
            # WebUntis sperrt oft den Kalender in den Sommerferien hart ab.
            # Statt eines Absturzes werten wir den Error-String aus und zeigen Ferien an.
            if ist_ferien_fehler(e):
                return {"current": None, "next": None}, "Unterrichtsfrei!\n(Ferienzeit)"
            logging.error(f"Unerwarteter WebUntis Stundenplan-Fehler: {e}")
            raise e
            
        # Den Tagesplan JETZT vollständig auslesen - hier besteht die Sitzung
        # noch. Danach sind die Daten von Netz und Sitzung unabhängig.
        lessons = resolve_timetable(timetable, conf)

        # Abruf erfolgreich: Tagesplan als Offline-Rücklage sichern, damit ein
        # späterer Netzausfall die Anzeige nicht wertlos macht.
        with app_state.state_lock:
            app_state.cached_lessons = lessons
            app_state.cached_lessons_date = today
            app_state.last_successful_sync = now

        return select_lessons(lessons, conf, now)

    except Exception as e:
        # PÄDAGOGISCHER HINTERGRUND: Spezifisches Error-Handling
        # Hier geben wir je nach Fehlerbild sprechende Strings an das E-Paper zurück.
        error_msg = str(e)
        logging.error(f"WebUntis API Fehler: {error_msg}")
        if "HTTPSConnectionPool" in error_msg or "NameResolutionError" in error_msg or "Max retries" in error_msg or "timeout" in error_msg.lower():
            return None, ERR_NO_NETWORK
        elif "LoginError" in error_msg or "Unauthorized" in error_msg:
            return None, "Untis-Login falsch"
        else:
            return None, ERR_UNTIS_OFFLINE
    finally:
        # WICHTIG: Erst abmelden, dann das Zeitlimit zurücksetzen.
        # Umgekehrt liefe die Abmeldung ohne jede Begrenzung. Reisst die
        # Verbindung genau in diesem Moment ab, wartet logout() dann endlos
        # auf eine Antwort - und mit ihm die gesamte Hintergrundschleife.
        # Das Display friere ein, waehrend das Web-Interface weiterlaeuft und
        # das System dadurch gesund aussieht.
        if session:
            try: session.logout()
            except Exception: pass
        socket.setdefaulttimeout(old_timeout)

