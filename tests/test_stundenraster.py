"""
Tests fuer stundenraster_auslesen.py.

Der Abruf selbst laesst sich hier nicht pruefen - dafuer braeuchte es einen
echten WebUntis-Server. Geprueft wird die Ableitung: aus den Zeiteinheiten, die
WebUntis liefert, einen SCHEDULE-Block zu machen. Genau dort steckt die Logik,
die schiefgehen kann.
"""
import datetime

import pytest

import stundenraster_auslesen as S
from tuerschild.konfiguration import pruefe_stundenplan


def uhr(stunde, minute):
    return datetime.time(stunde, minute)


@pytest.fixture
def schultag():
    """Ein Schultag, wie ihn WebUntis liefert - mit Doppelstundenblöcken."""
    return [
        ("1", uhr(8, 0), uhr(8, 45)),
        ("2", uhr(8, 50), uhr(9, 35)),     # 5 Min. Wechselzeit davor
        ("3", uhr(9, 55), uhr(10, 40)),    # 20 Min. Pause davor
        ("4", uhr(10, 45), uhr(11, 30)),
        ("5", uhr(11, 45), uhr(12, 30)),   # 15 Min. Pause davor
    ]


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


def test_der_vorschlag_besteht_die_eigene_pruefung(schultag):
    """
    Das Entscheidende: Was hier herauskommt, muss die Prüfung aus
    konfiguration.py bestehen - sonst warnte das Programm beim naechsten Start
    ueber genau die Daten, die dieses Skript vorgeschlagen hat.
    """
    vorschlag, _ = S.baue_vorschlag(schultag)
    ergebnis, fehler = pruefe_stundenplan(vorschlag)
    assert fehler is None, fehler
    assert ergebnis["LESSONS"] == vorschlag["LESSONS"]


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


def test_das_skript_fasst_die_hardware_nicht_an():
    """
    Es laeuft womoeglich, waehrend das Tuerschild arbeitet. Ohne die Sperre
    wuerde schon sein Import die GPIO-Pins belegen und mit "GPIO busy"
    abbrechen - der Waveshare-Treiber greift bereits beim Laden zu.
    """
    import os
    import re

    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "stundenraster_auslesen.py"), encoding="utf-8") as datei:
        zeilen = [z for z in datei.read().split("\n")
                  if z.strip() and not z.strip().startswith("#")]

    sperre = next(i for i, z in enumerate(zeilen)
                  if z.startswith('os.environ["TUERSCHILD_OHNE_HARDWARE"]'))
    erster_import = next(i for i, z in enumerate(zeilen)
                         if re.match(r"^from tuerschild", z))
    assert sperre < erster_import, (
        "Die Hardware-Sperre steht hinter dem Import des Pakets und ist "
        "damit wirkungslos."
    )


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
    block = S.als_json_block(vorschlag)

    gelesen = json.loads("{" + block + "}")
    assert gelesen["SCHEDULE"] == vorschlag


def test_eine_stunde_steht_auf_einer_zeile(schultag):
    """
    json.dumps(indent=4) blaettert jede Stunde auf fuenf Zeilen auf. Der Block
    soll aussehen wie die mitgelieferte config.example.json.
    """
    block = S.als_json_block(S.baue_vorschlag(schultag)[0])
    assert '{"start": "08:00", "end": "08:45", "name": "1"}' in block
    assert len(block.split("\n")) < 20


def test_leere_pausen_brechen_den_block_nicht():
    block = S.als_json_block(S.baue_vorschlag([("1", uhr(8, 0), uhr(8, 45))])[0])
    import json
    assert json.loads("{" + block + "}")["SCHEDULE"]["BREAKS"] == []
