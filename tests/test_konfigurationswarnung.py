"""
Tests fuer die Warnungen beim Laden der config.json.

WARUM BEIM LADEN GEWARNT WIRD:
Die Datei wird von Hand bearbeitet - per SSH, so beschreibt es die
Installationsanleitung. Genau dieser Weg kam an keiner Pruefung vorbei, und die
Fehler, die dabei entstehen, aeussern sich nicht als Absturz:

  * "8:00" statt "08:00" - der Stundenname bleibt leer. Kein Mensch empfindet
    diese Schreibweise als falsch, parse_lesson() findet sie aber nie, weil
    dort Zeichenketten verglichen werden.
  * ein leerer Raumname - auf dem Schild steht "Raum None fehlt.".

Beides sieht am Geraet nach allem Moeglichen aus, nur nicht nach seiner
Ursache. Eine Zeile im Protokoll spart die lange Suche.

Gewarnt wird nur, abgelehnt nichts: Ein Tuerschild, das wegen eines
Kommafehlers gar nicht erst startet, waere die schlechtere Loesung.
"""
import json
import logging
import os
import tempfile

import pytest

import tuerschild as R
from tuerschild import konfiguration


# Kennzeichen dafuer, dass ein Feld ganz fehlen soll (None waere ein gueltiger Wert)
ENTFERNEN = object()


@pytest.fixture
def konfigdatei(conf):
    """
    Legt eine config.json an und liefert eine Funktion, die sie neu beschreibt
    und danach frisch einlesen laesst.
    """
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
    datei.close()
    konfiguration.CONFIG_FILE = datei.name

    def schreiben(**abweichungen):
        inhalt = dict(conf)
        for schluessel, wert in abweichungen.items():
            if wert is ENTFERNEN:
                inhalt.pop(schluessel, None)
            else:
                inhalt[schluessel] = wert
        with open(datei.name, "w", encoding="utf-8") as f:
            json.dump(inhalt, f, ensure_ascii=False)
        # Neu einlesen erzwingen - sonst greift der Zwischenspeicher
        R.app_state.last_config_mtime = 0
        return R.get_cached_config()

    yield schreiben
    os.unlink(datei.name)


def test_eine_saubere_konfiguration_wird_nicht_beanstandet(konfigdatei, caplog):
    with caplog.at_level(logging.WARNING):
        konfigdatei()
    assert not [e for e in caplog.records if "config.json" in e.message]


def test_kurze_uhrzeit_im_stundenplan_wird_gemeldet(konfigdatei, caplog):
    """Der Fehler, der ohne diese Warnung am schwersten zu finden ist."""
    with caplog.at_level(logging.WARNING):
        konfigdatei(SCHEDULE={
            "DAY_START": "07:55", "DAY_END": "15:30",
            "LESSONS": [{"start": "8:00", "end": "08:45", "name": "1. Std."}],
            "BREAKS": []})

    meldungen = [e.message for e in caplog.records if "config.json" in e.message]
    assert meldungen, "Die kurze Schreibweise wurde nicht bemerkt"
    assert "08:00" in meldungen[0], "Die Meldung muss die richtige Schreibweise nennen"
    assert "Eintrag 1" in meldungen[0], "Die Meldung muss den Eintrag benennen"


def test_leerer_raumname_wird_gemeldet(konfigdatei, caplog):
    with caplog.at_level(logging.WARNING):
        konfigdatei(ROOM_NAME="")

    assert any("ROOM_NAME" in e.message for e in caplog.records)


def test_fehlender_stundenplan_wird_gemeldet(konfigdatei, caplog):
    with caplog.at_level(logging.WARNING):
        konfigdatei(SCHEDULE=ENTFERNEN)

    assert any("SCHEDULE fehlt" in e.message for e in caplog.records)


def test_die_warnung_haelt_das_programm_nicht_auf(konfigdatei):
    """
    Gewarnt wird, abgelehnt nichts. Die fehlerhafte Konfiguration muss
    vollstaendig zurueckkommen - das Schild soll weiterlaufen und den Rest
    anzeigen, statt am Kommafehler zu scheitern.
    """
    conf = konfigdatei(ROOM_NAME="")
    assert conf["UNTIS_SERVER"]
    assert conf["ROOM_NAME"] == ""


def test_es_wird_nicht_bei_jedem_zugriff_gewarnt(konfigdatei, caplog):
    """
    get_cached_config() wird mehrmals pro Sekunde aufgerufen. Stuende die
    Warnung jedes Mal im Protokoll, liefe das Journal an einem Schultag voll -
    und die wirklich wichtigen Meldungen gingen darin unter.
    """
    konfigdatei(ROOM_NAME="")
    # Die Warnung beim Anlegen ist bereits im Protokoll und wuerde mitgezaehlt.
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            R.get_cached_config()

    assert not [e for e in caplog.records if "config.json" in e.message]
