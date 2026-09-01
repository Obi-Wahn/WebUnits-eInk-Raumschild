"""
Tests fuer stundenraster_auslesen.py.

Der Abruf selbst laesst sich hier nicht pruefen - dafuer braeuchte es einen
echten WebUntis-Server. Er ist dafuer einmal am Geraet erprobt worden: Die
Ableitung traf die Zeiten der Schule auf die Minute genau.

Geprueft wird hier, was danach passiert: aus den Zeiteinheiten, die WebUntis
liefert, einen SCHEDULE-Block zu machen. Dort steckt die Logik, die schiefgehen
kann.
"""
import datetime

import pytest

import stundenraster_auslesen as S
from tuerschild.konfiguration import pruefe_stundenplan


def uhr(stunde, minute):
    return datetime.time(stunde, minute)


@pytest.fixture
def schultag():
    """
    Ein Schultag, wie WebUntis ihn liefert - nachgebildet nach dem echten
    Abruf: Namen sind blosse Ziffern, zwischen den Stunden liegen mal fuenf
    Minuten Wechselzeit, mal eine richtige Pause.
    """
    return [
        ("1", uhr(8, 0), uhr(8, 45)),
        ("2", uhr(8, 50), uhr(9, 35)),     # 5 Min. Wechselzeit davor
        ("3", uhr(9, 55), uhr(10, 40)),    # 20 Min. Pause davor
        ("4", uhr(10, 45), uhr(11, 30)),
        ("5", uhr(11, 45), uhr(12, 30)),   # 15 Min. Pause davor
    ]


# ==============================================================================
# Der Vorschlag
# ==============================================================================
def test_die_stunden_kommen_unveraendert_durch(schultag):
    vorschlag, _ = S.baue_vorschlag(schultag)
    assert len(vorschlag["LESSONS"]) == 5
    assert vorschlag["LESSONS"][0] == {"start": "08:00", "end": "08:45", "name": "1"}


def test_die_zeiten_sind_immer_zweistellig():
    """
    Der eigentliche Zweck des Skripts. WebUntis liefert echte Uhrzeiten, hier
    formatiert - der Fehler "8:00", der den Stundennamen leer laesst, kann so
    gar nicht erst entstehen.
    """
    vorschlag, _ = S.baue_vorschlag([("1", uhr(8, 0), uhr(8, 45))])
    assert vorschlag["LESSONS"][0]["start"] == "08:00"
    assert vorschlag["DAY_START"] == "07:55"


def test_pausen_werden_aus_den_luecken_abgeleitet(schultag):
    vorschlag, _ = S.baue_vorschlag(schultag)
    assert vorschlag["BREAKS"] == [
        {"start": "09:35", "end": "09:55", "name": "Pause"},
        {"start": "11:30", "end": "11:45", "name": "Pause"},
    ]


def test_kurze_wechselzeiten_gelten_nicht_als_pause(schultag):
    """
    Sonst stuende auf dem Schild alle 45 Minuten fuer fuenf Minuten "Pause" -
    statt der Information, die dort hingehoert.
    """
    vorschlag, uebersprungen = S.baue_vorschlag(schultag)
    assert not any(p["start"] == "08:45" for p in vorschlag["BREAKS"])
    assert uebersprungen, "Die übersprungene Lücke muss gemeldet werden"
    assert "08:45-08:50" in uebersprungen[0]


def test_die_grenze_fuer_pausen_ist_einstellbar(schultag):
    vorschlag, uebersprungen = S.baue_vorschlag(schultag, kleinste_pause=1)
    assert len(vorschlag["BREAKS"]) == 4
    assert not uebersprungen


def test_der_tagesbeginn_bekommt_einen_vorlauf(schultag):
    """
    Vor DAY_START zeigt das Schild "Guten Morgen!". Der Vorlauf ist eine
    bewusste Zugabe und steht in keinen Daten aus WebUntis.
    """
    assert S.baue_vorschlag(schultag, vorlauf=5)[0]["DAY_START"] == "07:55"
    assert S.baue_vorschlag(schultag, vorlauf=0)[0]["DAY_START"] == "08:00"
    assert S.baue_vorschlag(schultag, vorlauf=30)[0]["DAY_START"] == "07:30"


def test_das_tagesende_ist_das_ende_der_letzten_stunde(schultag):
    assert S.baue_vorschlag(schultag)[0]["DAY_END"] == "12:30"


def test_ein_leerer_tag_ergibt_keinen_vorschlag():
    vorschlag, uebersprungen = S.baue_vorschlag([])
    assert vorschlag is None
    assert uebersprungen == []


def test_ueberlappende_stunden_erzeugen_weder_pause_noch_meldung():
    """
    Kommt vor, wenn eine Schule parallele Bänder im Raster fuehrt. Eine
    Ueberlappung ist keine Luecke - sie darf weder als Pause erscheinen noch
    als uebersprungene Wechselzeit, sonst stuende in der Ausgabe eine Zeile
    wie "09:30-08:45 (-45 Min.)" und verwirrte mehr, als sie hilft.
    """
    vorschlag, uebersprungen = S.baue_vorschlag([
        ("1", uhr(8, 0), uhr(9, 30)),
        ("2", uhr(8, 45), uhr(10, 15)),
    ])
    assert vorschlag["BREAKS"] == []
    assert uebersprungen == []


def test_lange_namen_aus_webuntis_werden_uebernommen():
    """Manche Schulen benennen die Einheiten "1. Std.", andere nur "1"."""
    vorschlag, _ = S.baue_vorschlag([("1. Std.", uhr(8, 0), uhr(8, 45))])
    assert vorschlag["LESSONS"][0]["name"] == "1. Std."


# ==============================================================================
# Die Selbstkontrolle des Skripts
# ==============================================================================
def test_die_selbstkontrolle_findet_nichts_an_einem_guten_vorschlag(schultag):
    assert S.pruefe(S.baue_vorschlag(schultag)[0]) == []


def test_die_selbstkontrolle_erkennt_eine_verdrehte_stunde():
    assert S.pruefe({"DAY_START": "08:00", "DAY_END": "13:00",
                     "LESSONS": [{"start": "08:45", "end": "08:00", "name": "1"}],
                     "BREAKS": []})


def test_die_selbstkontrolle_erkennt_eine_einstellige_zeit():
    maengel = S.pruefe({"DAY_START": "8:00", "DAY_END": "13:00",
                        "LESSONS": [], "BREAKS": []})
    assert any("zweistellig" in m for m in maengel)


def test_der_vorschlag_besteht_auch_die_pruefung_des_programms(schultag):
    """
    Das Entscheidende: Was hier herauskommt, muss die Prüfung aus
    konfiguration.py bestehen - sonst warnte das Programm beim naechsten Start
    ueber genau die Daten, die dieses Skript vorgeschlagen hat.

    Das Skript bringt seine eigene, schlanke Kontrolle mit, weil es
    eigenstaendig bleiben soll. Dieser Test haelt beide zusammen.
    """
    vorschlag, _ = S.baue_vorschlag(schultag)
    ergebnis, fehler = pruefe_stundenplan(vorschlag)
    assert fehler is None, fehler
    assert ergebnis["LESSONS"] == vorschlag["LESSONS"]


# ==============================================================================
# Die Ausgabe zum Hineinkopieren
# ==============================================================================
def test_der_block_laesst_sich_als_json_lesen(schultag):
    """
    Er ist zum Einfuegen in die config.json gedacht. Waere er kein gueltiges
    JSON, faende der Fehler erst beim naechsten Start des Programms statt.
    """
    import json

    vorschlag, _ = S.baue_vorschlag(schultag)
    assert json.loads("{" + S.als_json_block(vorschlag) + "}")["SCHEDULE"] == vorschlag


def test_eine_stunde_steht_auf_einer_zeile(schultag):
    """
    json.dumps(indent=4) blaettert jede Stunde auf fuenf Zeilen auf. Der Block
    soll aussehen wie die mitgelieferte config.example.json.
    """
    block = S.als_json_block(S.baue_vorschlag(schultag)[0])
    assert '{"start": "08:00", "end": "08:45", "name": "1"}' in block
    assert len(block.split("\n")) < 20


def test_leere_pausen_brechen_den_block_nicht():
    import json
    block = S.als_json_block(S.baue_vorschlag([("1", uhr(8, 0), uhr(8, 45))])[0])
    assert json.loads("{" + block + "}")["SCHEDULE"]["BREAKS"] == []


# ==============================================================================
# Eigenstaendigkeit
# ==============================================================================
def test_das_skript_fasst_die_hardware_nicht_an():
    """
    Es laeuft womoeglich, waehrend das Tuerschild arbeitet. Wuerde es das Paket
    einbinden, belegte schon der Import die GPIO-Pins und braeche mit
    "GPIO busy" ab - der Waveshare-Treiber greift bereits beim Laden zu.
    """
    import os
    import re

    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "stundenraster_auslesen.py")
    with open(pfad, encoding="utf-8") as datei:
        quelltext = datei.read()

    assert not re.search(r"^\s*(from|import)\s+tuerschild", quelltext, re.MULTILINE), (
        "Das Skript bindet das Paket ein und würde damit die GPIO-Pins belegen."
    )


def test_das_skript_gibt_keine_zugangsdaten_aus():
    """
    Der Bericht ist zum Weitergeben gedacht. Benutzername und Passwort dürfen
    darin unter keinen Umständen auftauchen.
    """
    import os

    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "stundenraster_auslesen.py")
    with open(pfad, encoding="utf-8") as datei:
        zeilen = datei.read().split("\n")

    ausgaben = [z for z in zeilen if "schreibe(" in z and not z.strip().startswith("#")]
    for zeile in ausgaben:
        assert "UNTIS_PASS" not in zeile, f"Passwort in einer Ausgabe: {zeile.strip()}"
        assert "UNTIS_USER" not in zeile, f"Benutzername in einer Ausgabe: {zeile.strip()}"


def test_der_bericht_ist_von_der_versionskontrolle_ausgenommen():
    """Er enthaelt Servername und Schulkuerzel und gehoert nicht ins Repository."""
    import os

    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".gitignore")
    with open(pfad, encoding="utf-8") as datei:
        assert S.BERICHT_VORGABE in datei.read()
