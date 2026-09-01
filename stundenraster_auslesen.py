#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
==============================================================================
Hilfsmittel: Den Stundenraster aus WebUntis auslesen
==============================================================================
Ruft den in WebUntis hinterlegten Zeitraster ab und schlaegt daraus einen
fertigen SCHEDULE-Block fuer die config.json vor.

WOZU DAS GUT IST:
SCHEDULE wird sonst von Hand eingetragen. Dabei entsteht regelmaessig der
Fehler, der am schwersten zu finden ist: "8:00" statt "08:00" - der Stundenname
bleibt dann leer, ohne jede Fehlermeldung. Hier kommen die Zeiten als echte
Uhrzeiten aus WebUntis und werden formatiert; der Fehler kann gar nicht erst
entstehen.

WAS DIESES SKRIPT NICHT TUT:
Es aendert nichts. Die config.json wird nur gelesen, der Vorschlag geht auf die
Konsole. Was davon uebernommen wird, entscheidet ein Mensch - und das ist
Absicht: Ob der Raster gepflegt ist, haengt an der Schule, und die Namen der
Pausen ("Mittagspause") kennt WebUntis nicht.

WARUM ES EIGENSTAENDIG IST:
Es importiert nichts aus dem Paket 'tuerschild'. Damit fasst es auch keine
Hardware an - der Waveshare-Treiber belegt die GPIO-Pins bereits beim Import,
und dieses Skript laeuft womoeglich, waehrend das Tuerschild arbeitet. Es
laesst sich ausserdem allein auf einen Rechner kopieren.

Das Tuerschild selbst ruft den Raster NICHT ab. SCHEDULE wird gerade dann
gebraucht, wenn WebUntis nicht erreichbar ist - die Hintergrundschleife
entscheidet damit, wann sie aktualisiert und was bei fehlendem Unterricht auf
dem Schild steht. Kaeme es aus dem Netz, stuende das Geraet beim ersten Start
ohne Verbindung ohne Plan da.

Alles, was es ausgibt, schreibt es zusaetzlich in einen Bericht:

    stundenraster_bericht.txt

Der enthaelt keine Zugangsdaten und laesst sich bei Problemen weitergeben. Er
steht in der .gitignore.

AUFRUF:
    cd ~/webuntis-display
    source webuntis/bin/activate
    python3 stundenraster_auslesen.py

    --config PFAD         andere config.json
    --bericht PFAD        anderer Dateiname fuer den Bericht
    --tag freitag         Wochentag, aus dem der Vorschlag gebaut wird
    --vorlauf 5           Minuten, die DAY_START vor der ersten Stunde liegt
    --kleinste-pause 10   Luecken darunter gelten als Wechselzeit, nicht als Pause
"""
import argparse
import datetime
import json
import os
import platform
import re
import socket
import sys
import traceback

BERICHT_VORGABE = "stundenraster_bericht.txt"

WOCHENTAGE = {1: "Sonntag", 2: "Montag", 3: "Dienstag", 4: "Mittwoch",
              5: "Donnerstag", 6: "Freitag", 7: "Samstag"}


class Mitschrift:
    """Schreibt jede Zeile auf den Bildschirm UND in den Bericht."""

    def __init__(self, pfad):
        self.pfad = pfad
        self.datei = open(pfad, "w", encoding="utf-8")

    def __call__(self, text=""):
        print(text)
        self.datei.write(text + "\n")
        self.datei.flush()

    def schliessen(self):
        self.datei.close()


def als_text(uhrzeit):
    """Immer zweistellig - genau das ist der Sinn der Uebung."""
    return uhrzeit.strftime("%H:%M")


def minuten(von, bis):
    tag = datetime.date(2000, 1, 1)
    return int((datetime.datetime.combine(tag, bis)
                - datetime.datetime.combine(tag, von)).total_seconds() // 60)


def baue_vorschlag(einheiten, vorlauf=5, kleinste_pause=10):
    """
    Macht aus den Zeiteinheiten eines Tages einen SCHEDULE-Block.

    LESSONS kommen unveraendert aus WebUntis. Abgeleitet werden:
      BREAKS      die Luecken zwischen zwei Stunden. Die NAMEN kennt WebUntis
                  nicht - es steht ueberall "Pause".
      DAY_START   erste Stunde minus Vorlauf (davor: "Guten Morgen!")
      DAY_END     Ende der letzten Stunde
    """
    if not einheiten:
        return None, []

    stunden = [{"start": als_text(a), "end": als_text(b), "name": str(n).strip()}
               for n, a, b in einheiten]

    pausen, uebersprungen = [], []
    for vorige, naechste in zip(einheiten, einheiten[1:]):
        luecke = minuten(vorige[2], naechste[1])
        if luecke <= 0:
            continue                      # Ueberlappung, keine Luecke
        if luecke < kleinste_pause:
            uebersprungen.append(f"{als_text(vorige[2])}-{als_text(naechste[1])} "
                                 f"({luecke} Min.)")
            continue
        pausen.append({"start": als_text(vorige[2]), "end": als_text(naechste[1]),
                       "name": "Pause"})

    erster = datetime.datetime.combine(datetime.date(2000, 1, 1), einheiten[0][1])
    beginn = (erster - datetime.timedelta(minutes=vorlauf)).time()

    return {"DAY_START": als_text(beginn), "DAY_END": als_text(einheiten[-1][2]),
            "LESSONS": stunden, "BREAKS": pausen}, uebersprungen


def als_json_block(vorschlag):
    """Eine Stunde pro Zeile - so, wie der Block in der config.json aussieht."""
    def liste(schluessel):
        zeilen = [f'            {json.dumps(e, ensure_ascii=False)}'
                  for e in vorschlag[schluessel]]
        if not zeilen:
            return f'        "{schluessel}": []'
        return f'        "{schluessel}": [\n' + ",\n".join(zeilen) + "\n        ]"

    return ('    "SCHEDULE": {\n'
            f'        "DAY_START": "{vorschlag["DAY_START"]}",\n'
            f'        "DAY_END": "{vorschlag["DAY_END"]}",\n'
            + liste("LESSONS") + ",\n" + liste("BREAKS") + "\n    }")


def pruefe(vorschlag):
    """Kleine Selbstkontrolle, damit der Vorschlag nicht ungeprueft dasteht."""
    maengel = []
    alle = [("DAY_START", vorschlag["DAY_START"]), ("DAY_END", vorschlag["DAY_END"])]
    for art in ("LESSONS", "BREAKS"):
        for nummer, eintrag in enumerate(vorschlag[art], 1):
            alle += [(f"{art} {nummer} start", eintrag["start"]),
                     (f"{art} {nummer} end", eintrag["end"])]
            if eintrag["end"] <= eintrag["start"]:
                maengel.append(f"{art} {nummer}: Ende liegt nicht nach dem Beginn")
    for wo, wert in alle:
        if not re.fullmatch(r"\d{2}:\d{2}", wert):
            maengel.append(f"{wo}: '{wert}' ist nicht zweistellig HH:MM")
    if vorschlag["DAY_END"] <= vorschlag["DAY_START"]:
        maengel.append("DAY_END liegt nicht nach DAY_START")
    return maengel


def main():
    zerleger = argparse.ArgumentParser(
        description="Liest den Stundenraster aus WebUntis und schlaegt daraus "
                    "einen SCHEDULE-Block fuer die config.json vor. "
                    "Es wird nichts gespeichert ausser dem Bericht.")
    zerleger.add_argument("--config", default="config.json")
    zerleger.add_argument("--bericht", default=BERICHT_VORGABE)
    zerleger.add_argument("--tag", help="Wochentag fuer den Vorschlag "
                                        "(ohne Angabe: der mit den meisten Stunden)")
    zerleger.add_argument("--vorlauf", type=int, default=5)
    zerleger.add_argument("--kleinste-pause", type=int, default=10, dest="kleinste_pause")
    argumente = zerleger.parse_args()

    schreibe = Mitschrift(argumente.bericht)
    try:
        bericht(schreibe, argumente)
    except SystemExit:
        raise
    except Exception:
        schreibe("")
        schreibe("UNERWARTETER FEHLER")
        schreibe("-" * 70)
        for zeile in traceback.format_exc().rstrip().split("\n"):
            schreibe(zeile)
    finally:
        schreibe("")
        schreibe("=" * 70)
        schreibe(f"Dieser Bericht steht in: {os.path.abspath(argumente.bericht)}")
        schreibe("Er enthaelt KEINE Zugangsdaten - kein Benutzername, kein Passwort.")
        schreibe("Enthalten sind Servername und Schulkuerzel sowie die Zeiten des")
        schreibe("Stundenrasters. Bitte kurz durchsehen, bevor du ihn weitergibst.")
        schreibe.schliessen()


def bericht(schreibe, argumente):
    schreibe("=" * 70)
    schreibe("Stundenraster aus WebUntis (getTimegridUnits)")
    schreibe("=" * 70)
    schreibe(f"Zeitpunkt : {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    schreibe(f"Python    : {platform.python_version()} auf {platform.machine()}")

    try:
        import webuntis
        fassung = getattr(webuntis, "__version__", "unbekannt")
    except ImportError:
        schreibe("")
        schreibe("FEHLER: Die Bibliothek 'webuntis' fehlt.")
        schreibe("Virtuelle Umgebung aktiviert?   source webuntis/bin/activate")
        return
    schreibe(f"webuntis  : {fassung}")

    # --- Zugangsdaten lesen, ohne sie je auszugeben ---
    if not os.path.exists(argumente.config):
        schreibe("")
        schreibe(f"FEHLER: {os.path.abspath(argumente.config)} nicht gefunden.")
        schreibe("Im Projektverzeichnis aufrufen oder --config PFAD angeben.")
        return
    try:
        with open(argumente.config, encoding="utf-8") as datei:
            conf = json.load(datei)
    except json.JSONDecodeError as e:
        schreibe("")
        schreibe(f"FEHLER: config.json ist kein gueltiges JSON: {e}")
        return

    fehlend = [s for s in ("UNTIS_SERVER", "UNTIS_SCHOOL", "UNTIS_USER", "UNTIS_PASS")
               if not conf.get(s)]
    if fehlend:
        schreibe("")
        schreibe("FEHLER: In der config.json fehlen: " + ", ".join(fehlend))
        return

    schreibe(f"Server    : {conf['UNTIS_SERVER']}")
    schreibe(f"Schule    : {conf['UNTIS_SCHOOL']}")
    schreibe("")

    # --- Abruf ---
    altes_limit = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    sitzung = None
    try:
        sitzung = webuntis.Session(
            server=conf["UNTIS_SERVER"], username=conf["UNTIS_USER"],
            password=conf["UNTIS_PASS"], school=conf["UNTIS_SCHOOL"],
            useragent="WebUntis-Tuerschild")
        sitzung.login()
        schreibe("Anmeldung : erfolgreich")

        # Vollstaendig auslesen, SOLANGE DIE SITZUNG LEBT - die Bibliothek
        # laedt manche Werte erst beim Zugriff nach.
        raster = []
        for tag in sitzung.timegrid_units():
            roh = getattr(tag, "_data", None)
            raster.append((tag.day,
                           [(e.name, e.start, e.end) for e in tag.time_units],
                           roh))
        schreibe(f"Abruf     : erfolgreich, {len(raster)} Tage geliefert")
    except Exception as e:
        schreibe("")
        schreibe(f"FEHLER beim Abruf: {type(e).__name__}: {e}")
        schreibe("")
        schreibe("Haeufige Ursachen:")
        schreibe("  - falsche Zugangsdaten oder falsches Schulkuerzel")
        schreibe("  - keine Netzwerkverbindung")
        schreibe("  - das Konto darf den Zeitraster nicht lesen")
        schreibe("")
        schreibe("Vollstaendige Fehlermeldung:")
        schreibe("-" * 70)
        for zeile in traceback.format_exc().rstrip().split("\n"):
            schreibe(zeile)
        return
    finally:
        # Erst abmelden, dann das Zeitlimit zuruecksetzen.
        if sitzung:
            try:
                sitzung.logout()
            except Exception:
                pass
        socket.setdefaulttimeout(altes_limit)

    # --- Was hinterlegt ist ---
    schreibe("")
    schreibe("-" * 70)
    schreibe("In WebUntis hinterlegt")
    schreibe("-" * 70)
    for tagesnummer, einheiten, _ in raster:
        name = WOCHENTAGE.get(tagesnummer, f"Tag {tagesnummer}")
        if not einheiten:
            schreibe(f"  {name:<12} (keine Stunden)")
            continue
        schreibe(f"  {name:<12} {len(einheiten):>2} Stunden   "
                 f"{als_text(einheiten[0][1])}-{als_text(einheiten[-1][2])}")
        for stundenname, beginn, ende in einheiten:
            schreibe(f"      {als_text(beginn)}-{als_text(ende)}  {stundenname!r}")

    # --- Rohdaten, falls oben etwas seltsam aussieht ---
    schreibe("")
    schreibe("-" * 70)
    schreibe("Rohdaten, wie WebUntis sie liefert")
    schreibe("-" * 70)
    for tagesnummer, _, roh in raster:
        schreibe(f"  Tag {tagesnummer}: {json.dumps(roh, ensure_ascii=False, default=str)}")

    if not any(einheiten for _, einheiten, _ in raster):
        schreibe("")
        schreibe("ERGEBNIS: WebUntis liefert keinen Zeitraster. Die Schule hat ihn")
        schreibe("offenbar nicht gepflegt - dann bleibt der Eintrag von Hand.")
        return

    # --- Tag waehlen ---
    tage = {WOCHENTAGE.get(n, "").lower(): (n, e) for n, e, _ in raster}
    if argumente.tag:
        gewaehlt = tage.get(argumente.tag.strip().lower())
        if not gewaehlt:
            schreibe("")
            schreibe(f"FEHLER: Unbekannter Wochentag: {argumente.tag}")
            return
        tagesnummer, einheiten = gewaehlt
    else:
        tagesnummer, einheiten, _ = max(raster, key=lambda e: len(e[1]))

    tagesname = WOCHENTAGE.get(tagesnummer, f"Tag {tagesnummer}")
    abweichend = [WOCHENTAGE.get(n, str(n)) for n, e, _ in raster
                  if e and len(e) != len(einheiten)]

    schreibe("")
    schreibe("-" * 70)
    schreibe(f"Vorschlag auf Grundlage von: {tagesname}")
    schreibe("-" * 70)
    if abweichend:
        # SCHEDULE kennt nur EIN Tagesmuster, WebUntis fuehrt es pro Wochentag.
        schreibe(f"  Achtung: Andere Stundenzahl an: {', '.join(abweichend)}.")
        schreibe("  SCHEDULE kennt nur ein Tagesmuster - bitte pruefen, ob der")
        schreibe("  gewaehlte Tag passt (--tag freitag waehlt einen anderen).")

    vorschlag, uebersprungen = baue_vorschlag(
        einheiten, argumente.vorlauf, argumente.kleinste_pause)

    if uebersprungen:
        schreibe(f"  Als Wechselzeit uebersprungen (unter "
                 f"{argumente.kleinste_pause} Min.): {', '.join(uebersprungen)}")

    maengel = pruefe(vorschlag)
    if maengel:
        schreibe("  ACHTUNG, der Vorschlag hat Maengel:")
        for mangel in maengel:
            schreibe(f"    - {mangel}")
    else:
        schreibe(f"  Geprueft: in Ordnung. Schultag {vorschlag['DAY_START']} bis "
                 f"{vorschlag['DAY_END']}, {len(vorschlag['LESSONS'])} Stunden, "
                 f"{len(vorschlag['BREAKS'])} Pausen.")

    schreibe("")
    schreibe("=" * 70)
    schreibe("Zum Uebernehmen in die config.json (ersetzt den SCHEDULE-Block):")
    schreibe("=" * 70)
    schreibe("")
    schreibe(als_json_block(vorschlag))
    schreibe("")
    schreibe('Die Namen der Pausen kennt WebUntis nicht - aus "Pause" laesst sich')
    schreibe('von Hand "1. Pause" oder "Mittagspause" machen.')


if __name__ == "__main__":
    main()
