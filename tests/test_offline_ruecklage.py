"""
Tests fuer die Offline-Ruecklage.

Faellt WLAN oder WebUntis aus, soll das Schild den zuletzt abgerufenen Plan
weiterzeigen statt einer Fehlermeldung. Zwei Eigenschaften sind dabei
entscheidend und werden hier festgehalten:

1. Die Ruecklage muss OHNE Sitzung und OHNE Netz auswertbar sein.
2. Sie wird bei jedem Update neu ausgewertet, nicht eingefroren - das Display
   wechselt also auch waehrend der Stoerung zur richtigen Zeit weiter.
"""
import datetime

import pytest

import raumanzeige as R
from conftest import MONTAG, RohStunde, uhrzeit


def test_ruecklage_funktioniert_nach_ende_der_sitzung(rohplan, conf):
    """
    Der Kernpunkt: Nach dem Abmelden wuerde ein Zugriff auf die rohen Objekte
    fehlschlagen. Weil beim Abruf alles ausgelesen wurde, stoert das nicht.
    """
    lebt, roh = rohplan
    R.app_state.cached_lessons = R.resolve_timetable(roh, conf)
    R.app_state.cached_lessons_date = MONTAG

    lebt[0] = False  # Sitzung beendet, Netz weg

    R.app_state.simulated_datetime = uhrzeit(8, 20)
    daten, _ = R.get_offline_fallback(conf)
    assert daten["current"].fach == "Mathematik"


def test_gegenprobe_rohobjekt_scheitert_ohne_sitzung(rohplan):
    """
    Beweist, dass die Attrappe den echten Fehlerfall nachbildet: Ohne diese
    Eigenschaft waere der Test oben wertlos, weil er nichts absichern wuerde.
    """
    lebt, roh = rohplan
    lebt[0] = False
    with pytest.raises(ConnectionError):
        _ = roh[0].subjects


def test_ruecklage_wechselt_zur_naechsten_stunde(rohplan, conf):
    """
    Ohne Neuauswertung bliebe das Display beim Stand des Ausfallzeitpunkts
    stehen. Hier wandert die Uhr ueber zwei Stundengrenzen.
    """
    lebt, roh = rohplan
    R.app_state.cached_lessons = R.resolve_timetable(roh, conf)
    R.app_state.cached_lessons_date = MONTAG
    lebt[0] = False

    for stunde, minute, erwartet in [(8, 20, "Mathematik"),
                                     (9, 0, "Deutsch"),
                                     (10, 0, "Englisch")]:
        R.app_state.simulated_datetime = uhrzeit(stunde, minute)
        daten, _ = R.get_offline_fallback(conf)
        assert daten["current"].fach == erwartet, f"um {stunde:02d}:{minute:02d}"


def test_ruecklage_wechselt_auch_auf_die_pause(stundenplan, conf):
    R.app_state.cached_lessons = stundenplan
    R.app_state.cached_lessons_date = MONTAG
    R.app_state.simulated_datetime = uhrzeit(9, 40)

    _, meldung = R.get_offline_fallback(conf)
    assert meldung == "1. Pause"


def test_ohne_ruecklage_kein_ergebnis(conf):
    R.app_state.cached_lessons = None
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    assert R.get_offline_fallback(conf) is None


def test_ruecklage_von_gestern_wird_verworfen(stundenplan, conf):
    """Ein Plan vom Vortag waere schlicht falsch - schlimmer als keine Anzeige."""
    R.app_state.cached_lessons = stundenplan
    R.app_state.cached_lessons_date = MONTAG - datetime.timedelta(days=1)
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    assert R.get_offline_fallback(conf) is None


def test_leere_liste_ist_eine_gueltige_ruecklage(conf):
    """
    Wichtige Unterscheidung: Eine leere Liste bedeutet "heute ist nachweislich
    kein Unterricht" und darf nicht mit "keine Ruecklage vorhanden"
    verwechselt werden.
    """
    R.app_state.cached_lessons = []
    R.app_state.cached_lessons_date = MONTAG
    R.app_state.simulated_datetime = uhrzeit(10, 0)

    ergebnis = R.get_offline_fallback(conf)
    assert ergebnis is not None
    assert ergebnis[1] == "Unterrichtsfrei"


# ==============================================================================
# Welche Fehler duerfen ueberhaupt auf die Ruecklage zurueckgreifen?
# ==============================================================================
def test_netzfehler_gelten_als_voruebergehend():
    assert R.ERR_NO_NETWORK in R.TRANSIENT_ERRORS
    assert R.ERR_UNTIS_OFFLINE in R.TRANSIENT_ERRORS


def test_dauerhafte_fehler_bleiben_sichtbar():
    """
    Ein falsches Passwort oder eine unvollstaendige Konfiguration darf sich
    nicht hinter altem Plan verstecken - sonst behebt es nie jemand.
    """
    assert "Untis-Login falsch" not in R.TRANSIENT_ERRORS
    assert "Konfiguration unvollständig." not in R.TRANSIENT_ERRORS
