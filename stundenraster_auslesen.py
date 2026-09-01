#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
==============================================================================
Hilfsmittel: Den Stundenraster aus WebUntis auslesen
==============================================================================
Ruft den in WebUntis hinterlegten Zeitraster ab und schlaegt daraus einen
fertigen SCHEDULE-Block fuer die config.json vor.

WOZU DAS GUT IST:
SCHEDULE wird bisher von Hand eingetragen. Dabei entsteht regelmaessig der
Fehler, der am schwersten zu finden ist: "8:00" statt "08:00". WebUntis liefert
die Zeiten als echte Uhrzeiten, hier formatiert - der Fehler kann also gar
nicht erst entstehen.

WAS DIESES SKRIPT NICHT TUT:
Es aendert nichts. Die config.json wird nur gelesen, nie geschrieben; der
Vorschlag geht auf die Konsole. Was davon uebernommen wird, entscheidet ein
Mensch - und das ist Absicht: Ob der Raster in WebUntis gepflegt ist, haengt an
der Schule, und die Namen der Pausen ("Mittagspause") kennt WebUntis gar nicht.

Das Tuerschild selbst ruft den Raster NICHT ab. SCHEDULE wird gerade dann
gebraucht, wenn WebUntis nicht erreichbar ist - die Schleife entscheidet damit,
wann sie aktualisiert und was bei fehlendem Unterricht auf dem Schild steht.
Käme es aus dem Netz, stuende das Geraet beim ersten Start ohne Verbindung
ohne Plan da.

AUFRUF:
    source webuntis/bin/activate
    python3 stundenraster_auslesen.py

    --tag montag        Wochentag, aus dem der Vorschlag gebaut wird
                        (ohne Angabe: der Tag mit den meisten Stunden)
    --vorlauf 5         Minuten, die DAY_START vor der ersten Stunde liegt
    --kleinste-pause 10 Luecken darunter gelten nicht als Pause
"""
import argparse
import datetime
import json
import os
import socket
import sys

# ACHTUNG, DIESE ZEILE MUSS VOR DEM IMPORT DES PAKETS STEHEN:
# Das Paket bindet beim Laden GPIO, I2C und den Displaytreiber ein, und der
# Waveshare-Treiber belegt die Pins bereits beim Import. Laeuft das Tuerschild
# gerade, wuerde dieses Skript sonst mit "GPIO busy" abbrechen - und liefe es
# nicht, griffe es selbst nach den Pins. Es braucht die Hardware nicht.
os.environ["TUERSCHILD_OHNE_HARDWARE"] = "1"

from tuerschild.konfiguration import (CONFIG_FILE, pruefe_stundenplan,  # noqa: E402
                                      formatiere_dauer)

WOCHENTAGE = {
    2: "Montag", 3: "Dienstag", 4: "Mittwoch", 5: "Donnerstag",
    6: "Freitag", 7: "Samstag", 1: "Sonntag",
}


def lies_zugangsdaten():
    """Liest die Zugangsdaten aus der config.json - ohne sie je zu veraendern."""
    if not os.path.exists(CONFIG_FILE):
        raus(f"config.json nicht gefunden: {CONFIG_FILE}\n"
             "Siehe Installationsanleitung, Schritt 5.")
    try:
        with open(CONFIG_FILE, encoding="utf-8") as datei:
            conf = json.load(datei)
    except json.JSONDecodeError as e:
        raus(f"config.json ist kein gültiges JSON: {e}")

    fehlend = [s for s in ("UNTIS_SERVER", "UNTIS_SCHOOL", "UNTIS_USER", "UNTIS_PASS")
               if not conf.get(s)]
    if fehlend:
        raus("In der config.json fehlen Zugangsdaten: " + ", ".join(fehlend))
    return conf


def hole_zeitraster(conf):
    """Meldet sich an, holt den Raster und meldet sich wieder ab."""
    try:
        import webuntis
    except ImportError:
        raus("Die Bibliothek 'webuntis' fehlt. Virtuelle Umgebung aktiviert?\n"
             "  source webuntis/bin/activate")

    # Ohne Zeitlimit haengt der Aufruf bei einer Netzstoerung unbegrenzt.
    altes_limit = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    sitzung = None
    try:
        sitzung = webuntis.Session(
            server=conf["UNTIS_SERVER"],
            username=conf["UNTIS_USER"],
            password=conf["UNTIS_PASS"],
            school=conf["UNTIS_SCHOOL"],
            useragent="WebUntis-Tuerschild",
        )
        sitzung.login()
        # Die Bibliothek laedt manche Werte erst beim Zugriff nach. Deshalb
        # wird hier vollstaendig ausgelesen, SOLANGE DIE SITZUNG NOCH LEBT.
        return [(tag.day, [(einheit.name, einheit.start, einheit.end)
                           for einheit in tag.time_units])
                for tag in sitzung.timegrid_units()]
    except Exception as e:
        raus(f"Der Abruf ist fehlgeschlagen: {type(e).__name__}: {e}\n\n"
             "Häufige Ursachen:\n"
             "  - falsche Zugangsdaten oder falscher Schulname in der config.json\n"
             "  - keine Netzwerkverbindung\n"
             "  - das Konto darf den Zeitraster nicht lesen")
    finally:
        # Erst abmelden, dann das Zeitlimit zuruecksetzen - sonst haengt auch
        # das Abmelden ohne Begrenzung.
        if sitzung:
            try:
                sitzung.logout()
            except Exception:
                pass
        socket.setdefaulttimeout(altes_limit)


def als_text(uhrzeit: datetime.time) -> str:
    """Immer zweistellig - genau darum geht es bei diesem Skript."""
    return uhrzeit.strftime("%H:%M")


def minuten(von: datetime.time, bis: datetime.time) -> int:
    a = datetime.datetime.combine(datetime.date(2000, 1, 1), von)
    b = datetime.datetime.combine(datetime.date(2000, 1, 1), bis)
    return int((b - a).total_seconds() // 60)


def baue_vorschlag(einheiten, vorlauf=5, kleinste_pause=10):
    """
    Macht aus den Zeiteinheiten eines Tages einen SCHEDULE-Block.

    Die Stunden kommen unveraendert aus WebUntis. Abgeleitet werden:

    - BREAKS: die Luecken zwischen zwei Stunden. Rechnerisch eindeutig, die
      NAMEN aber nicht - WebUntis kennt keine "Mittagspause". Es steht deshalb
      ueberall "Pause"; wer eigene Namen will, traegt sie danach ein.
      Sehr kurze Luecken (Wechselzeiten) werden uebersprungen, sonst stuende
      auf dem Schild alle 45 Minuten fuer fuenf Minuten "Pause".
    - DAY_START: die erste Stunde abzueglich eines Vorlaufs. Der Vorlauf ist
      eine bewusste Zugabe und steht in keinen Daten - vorher zeigt das Schild
      "Guten Morgen!".
    - DAY_END: das Ende der letzten Stunde.
    """
    if not einheiten:
        return None, []

    stunden = [{"start": als_text(start), "end": als_text(ende),
                "name": str(name).strip()}
               for name, start, ende in einheiten]

    pausen = []
    uebersprungen = []
    for vorige, naechste in zip(einheiten, einheiten[1:]):
        luecke = minuten(vorige[2], naechste[1])
        if luecke <= 0:
            continue
        if luecke < kleinste_pause:
            uebersprungen.append(f"{als_text(vorige[2])}-{als_text(naechste[1])} "
                                 f"({luecke} Min.)")
            continue
        pausen.append({"start": als_text(vorige[2]),
                       "end": als_text(naechste[1]), "name": "Pause"})

    erster_beginn = datetime.datetime.combine(datetime.date(2000, 1, 1), einheiten[0][1])
    tagesbeginn = (erster_beginn - datetime.timedelta(minutes=vorlauf)).time()

    return {
        "DAY_START": als_text(tagesbeginn),
        "DAY_END": als_text(einheiten[-1][2]),
        "LESSONS": stunden,
        "BREAKS": pausen,
    }, uebersprungen


def als_json_block(vorschlag) -> str:
    """
    Formatiert den Vorschlag so, wie der Block in der config.json aussieht:
    eine Stunde pro Zeile.

    json.dumps(indent=4) wuerde jede Stunde auf fuenf Zeilen aufblaettern - bei
    acht Stunden und drei Pausen sind das ueber fuenfzig Zeilen statt zwoelf.
    Der Block soll sich einfuegen lassen und danach genauso lesbar sein wie die
    mitgelieferte Vorlage.
    """
    def eintraege(schluessel):
        zeilen = [f'            {json.dumps(e, ensure_ascii=False)}'
                  for e in vorschlag[schluessel]]
        if not zeilen:
            return f'        "{schluessel}": []'
        return f'        "{schluessel}": [\n' + ",\n".join(zeilen) + "\n        ]"

    return ("    \"SCHEDULE\": {\n"
            f'        "DAY_START": "{vorschlag["DAY_START"]}",\n'
            f'        "DAY_END": "{vorschlag["DAY_END"]}",\n'
            + eintraege("LESSONS") + ",\n"
            + eintraege("BREAKS") + "\n"
            "    }")


def raus(meldung, code=1):
    print(f"\n[FEHLER] {meldung}", file=sys.stderr)
    sys.exit(code)


def main():
    zerleger = argparse.ArgumentParser(
        description="Liest den Stundenraster aus WebUntis und schlägt einen "
                    "SCHEDULE-Block für die config.json vor. Es wird nichts "
                    "gespeichert.")
    zerleger.add_argument("--tag", help="Wochentag für den Vorschlag "
                                        "(montag, dienstag, ...). Ohne Angabe "
                                        "der Tag mit den meisten Stunden.")
    zerleger.add_argument("--vorlauf", type=int, default=5,
                          help="Minuten, die DAY_START vor der ersten Stunde "
                               "liegt (Voreinstellung: 5)")
    zerleger.add_argument("--kleinste-pause", type=int, default=10, dest="kleinste_pause",
                          help="Lücken unter dieser Dauer gelten nicht als "
                               "Pause (Voreinstellung: 10)")
    argumente = zerleger.parse_args()

    conf = lies_zugangsdaten()
    print(f"Frage {conf['UNTIS_SERVER']} nach dem Zeitraster der Schule "
          f"'{conf['UNTIS_SCHOOL']}' ...\n")
    raster = hole_zeitraster(conf)

    if not raster or not any(einheiten for _, einheiten in raster):
        raus("WebUntis liefert keinen Zeitraster. Die Schule hat ihn "
             "offenbar nicht gepflegt - dann bleibt nur der Eintrag von Hand.")

    # --- Was tatsaechlich hinterlegt ist ---
    print("In WebUntis hinterlegt:\n")
    for tagesnummer, einheiten in raster:
        name = WOCHENTAGE.get(tagesnummer, f"Tag {tagesnummer}")
        if not einheiten:
            print(f"  {name:<12} (keine Stunden)")
            continue
        spanne = f"{als_text(einheiten[0][1])}-{als_text(einheiten[-1][2])}"
        print(f"  {name:<12} {len(einheiten):>2} Stunden   {spanne}")

    # --- Tag auswaehlen ---
    tage = {WOCHENTAGE.get(nummer, "").lower(): (nummer, einheiten)
            for nummer, einheiten in raster}
    if argumente.tag:
        gewaehlt = tage.get(argumente.tag.strip().lower())
        if not gewaehlt:
            raus(f"Unbekannter Wochentag: {argumente.tag}")
        tagesnummer, einheiten = gewaehlt
    else:
        tagesnummer, einheiten = max(raster, key=lambda eintrag: len(eintrag[1]))

    tagesname = WOCHENTAGE.get(tagesnummer, f"Tag {tagesnummer}")
    abweichend = [WOCHENTAGE.get(n, str(n)) for n, e in raster
                  if e and len(e) != len(einheiten)]

    print(f"\nVorschlag auf Grundlage von: {tagesname}")
    if abweichend:
        # SCHEDULE ist EIN Tagesmuster, der Raster in WebUntis dagegen pro
        # Wochentag. Wo sich die Tage unterscheiden, muss ein Mensch entscheiden.
        print(f"  Achtung: Diese Tage haben eine andere Stundenzahl: "
              f"{', '.join(abweichend)}.")
        print("  SCHEDULE kennt nur ein Tagesmuster. Bitte prüfen, ob der "
              "gewählte Tag passt.")

    vorschlag, uebersprungen = baue_vorschlag(
        einheiten, vorlauf=argumente.vorlauf, kleinste_pause=argumente.kleinste_pause)

    if uebersprungen:
        print(f"  Als Wechselzeit übersprungen (unter {argumente.kleinste_pause} Min.): "
              f"{', '.join(uebersprungen)}")

    # --- Gegen die eigene Pruefung halten ---
    _, fehler = pruefe_stundenplan(vorschlag)
    if fehler:
        print(f"\n  Achtung: Der Vorschlag besteht die eigene Prüfung nicht: {fehler}")
        print("  Bitte vor dem Übernehmen von Hand berichtigen.")
    else:
        dauer = formatiere_dauer(
            minuten(datetime.datetime.strptime(vorschlag["DAY_START"], "%H:%M").time(),
                    datetime.datetime.strptime(vorschlag["DAY_END"], "%H:%M").time()) * 60)
        print(f"  Geprüft: in Ordnung. Schultag {vorschlag['DAY_START']} bis "
              f"{vorschlag['DAY_END']} ({dauer}), "
              f"{len(vorschlag['LESSONS'])} Stunden, {len(vorschlag['BREAKS'])} Pausen.")

    # --- Der fertige Block ---
    print("\n" + "=" * 70)
    print("Zum Übernehmen in die config.json (ersetzt den bisherigen SCHEDULE-Block):")
    print("=" * 70 + "\n")
    print(als_json_block(vorschlag))
    print("\n" + "=" * 70)
    print("Es wurde NICHTS gespeichert. Die Namen der Pausen kennt WebUntis nicht -")
    print("aus \"Pause\" lässt sich von Hand \"1. Pause\" oder \"Mittagspause\" machen.")


if __name__ == "__main__":
    main()
