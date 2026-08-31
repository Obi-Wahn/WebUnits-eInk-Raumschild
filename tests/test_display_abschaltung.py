"""
Tests fuer das Verhalten bei abgeschaltetem Display.

Ein Loeschvorgang auf E-Paper ist ein vollstaendiger Refresh-Zyklus: Das Panel
blitzt mehrfach schwarz-weiss auf und braucht dafuer Zeit und Strom. Das mehrfach
taeglich auf einem ohnehin leeren Panel zu wiederholen, ist unnoetiger Verschleiss.
"""
import threading
import time

import pytest

import raumanzeige as R
from conftest import uhrzeit


@pytest.fixture
def schleife(conf, monkeypatch):
    """Startet die Hintergrundschleife ohne Netzwerkzugriff."""
    monkeypatch.setattr(R, "get_current_lesson",
                        lambda c: ({"current": None, "next": None}, ""))
    monkeypatch.setattr(R, "BACKGROUND_ERROR_PAUSE", 0.05)

    R.app_state.shutdown_event.clear()
    thread = threading.Thread(target=R.background_loop, daemon=True)

    # Die Schleife wartet nach jedem Update 1,5 Sekunden. Die Laufzeiten unten
    # sind deshalb bewusst grosszuegig - in einem kurzen Fenster kaeme gar kein
    # zweiter Update-Zyklus zustande, und die Tests wuerden aus dem falschen
    # Grund bestehen.
    def starten(dauer=4.0):
        thread.start()
        time.sleep(dauer)

    yield starten

    R.app_state.shutdown_event.set()
    if thread.is_alive():
        thread.join(timeout=5)
    R.app_state.shutdown_event.clear()


def test_abgeschaltetes_display_wird_nur_einmal_geloescht(conf, monkeypatch,
                                                          display_attrappe, schleife):
    """
    Frueher lief der Loeschvorgang bei jedem Intervall und an jeder
    Stundengrenze - also alle paar Minuten, den ganzen Tag.
    """
    aus = {**conf, "DISPLAY_ACTIVE": False}
    monkeypatch.setattr(R, "get_cached_config", lambda: aus)

    # Die reguläre Auslösung ist das Abrufintervall - NICHT der Knopf im
    # Web-Interface. Wir verkuerzen das Intervall auf null, damit in wenigen
    # Sekunden mehrere Zyklen zustande kommen, und setzen eine Uhrzeit
    # innerhalb der Schulzeit, weil das Intervall sonst gar nicht greift.
    monkeypatch.setattr(R, "get_update_interval", lambda c: 0)
    R.app_state.simulated_datetime = uhrzeit(10, 0)
    R.app_state.simulation_started_at = time.time()

    schleife()

    assert display_attrappe.anzahl_anzeigen == 0, \
        "Bei DISPLAY_ACTIVE=false darf nichts gezeichnet werden"
    assert display_attrappe.anzahl_loeschen == 1, \
        f"Display wurde {display_attrappe.anzahl_loeschen}-mal geloescht statt genau einmal"


def test_manuelles_update_loescht_erneut(conf, monkeypatch, display_attrappe, schleife):
    """
    Bewusste Ausnahme: Ueber den Knopf im Web-Interface soll sich ein
    verschmutztes Panel von Hand bereinigen lassen, auch wenn das Display
    bereits abgeschaltet ist.
    """
    aus = {**conf, "DISPLAY_ACTIVE": False}
    monkeypatch.setattr(R, "get_cached_config", lambda: aus)

    def zweimal_druecken():
        for _ in range(3):
            with R.app_state.state_lock:
                R.app_state.force_update_flag = True
            time.sleep(1.6)          # Die Schleife wartet nach jedem Update 1,5 s

    ausloeser = threading.Thread(target=zweimal_druecken, daemon=True)
    ausloeser.start()
    schleife(dauer=4.0)
    ausloeser.join(timeout=6)

    assert display_attrappe.anzahl_loeschen >= 2, \
        "Ein manuelles Update sollte auch bei abgeschaltetem Display loeschen"


def test_beim_abschalten_wird_geloescht(conf, monkeypatch, display_attrappe, schleife):
    """Der Wechsel von an auf aus muss das Panel einmal leeren."""
    zustand = {"conf": dict(conf)}
    monkeypatch.setattr(R, "get_cached_config", lambda: zustand["conf"])

    def umschalten():
        time.sleep(0.4)
        zustand["conf"] = {**conf, "DISPLAY_ACTIVE": False}
        with R.app_state.state_lock:
            R.app_state.force_update_flag = True

    schalter = threading.Thread(target=umschalten, daemon=True)
    schalter.start()
    schleife(dauer=3.5)
    schalter.join(timeout=5)

    assert display_attrappe.anzahl_loeschen >= 1, "Beim Abschalten wurde nicht geloescht"


def test_bei_aktivem_display_wird_nicht_geloescht(conf, monkeypatch,
                                                  display_attrappe, schleife):
    monkeypatch.setattr(R, "get_cached_config", lambda: conf)
    schleife(dauer=2.0)
    assert display_attrappe.anzahl_loeschen == 0
    assert display_attrappe.anzahl_anzeigen >= 1
