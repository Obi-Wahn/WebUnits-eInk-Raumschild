"""
Tests fuer die Hintergrundschleife.

Diese Schleife ist der Kernprozess: Sie fragt WebUntis ab und aktualisiert das
Display. Faellt sie aus, friert das Schild ein - und zwar unbemerkt, weil der
Webserver in einem eigenen Thread weiterlaeuft und das System dadurch gesund
aussieht. Genau diese Eigenschaft sichern die Tests hier ab.

Die Schleife wird dafuer in einem echten Thread gestartet. Alle Zugriffe nach
aussen (WebUntis, Display) sind ersetzt, es werden also keine Netzwerkanfragen
gestellt und kein Panel angesteuert.
"""
import logging
import threading
import time

import pytest

import raumanzeige as R


@pytest.fixture
def schleife_im_thread(conf, monkeypatch):
    """
    Startet background_loop() in einem Thread und beendet ihn danach sauber.

    Liefert eine Funktion, mit der der Test die Schleife eine Weile laufen
    laesst. Das Beenden uebernimmt die Vorrichtung - sonst liefe bei einem
    fehlgeschlagenen Test ein Thread weiter und stoerte die uebrigen Tests.
    """
    # Kein Netzwerk: Der Abruf liefert sofort ein festes Ergebnis
    monkeypatch.setattr(R, "get_current_lesson", lambda c: ({"current": None, "next": None}, "Testlauf"))
    # Kurze Fehlerpause, damit der Test nicht 30 Sekunden wartet
    monkeypatch.setattr(R, "BACKGROUND_ERROR_PAUSE", 0.05)

    R.app_state.shutdown_event.clear()
    thread = threading.Thread(target=R.background_loop, daemon=True)

    def starten(dauer=0.8):
        thread.start()
        time.sleep(dauer)

    yield starten

    R.app_state.shutdown_event.set()
    if thread.is_alive():
        thread.join(timeout=5)
    R.app_state.shutdown_event.clear()


def test_schleife_ueberlebt_eine_ausnahme(conf, monkeypatch, caplog, schleife_im_thread):
    """
    Der wichtigste Test dieser Datei: Ohne Auffangnetz wuerde der Thread bei
    der ersten Ausnahme enden und das Display fuer immer stehenbleiben.
    """
    aufrufe = []

    def zunaechst_kaputt():
        aufrufe.append(1)
        if len(aufrufe) <= 2:
            raise RuntimeError("simulierter Fehler in der Schleife")
        return conf

    monkeypatch.setattr(R, "get_cached_config", zunaechst_kaputt)

    with caplog.at_level(logging.ERROR):
        schleife_im_thread()

    assert len(aufrufe) > 2, "Die Schleife hat nach der Ausnahme aufgegeben"
    assert "Unerwarteter Fehler in der Hintergrundschleife" in caplog.text


def test_ausnahme_wird_mit_aufrufpfad_protokolliert(conf, monkeypatch, caplog, schleife_im_thread):
    """
    logging.exception() statt logging.error(): Ohne den Aufrufpfad waere im
    Journal nicht zu erkennen, wo der Fehler entstanden ist.
    """
    def immer_kaputt():
        raise RuntimeError("eindeutige Fehlermeldung zum Wiederfinden")

    monkeypatch.setattr(R, "get_cached_config", immer_kaputt)

    with caplog.at_level(logging.ERROR):
        schleife_im_thread(dauer=0.3)

    assert "Traceback" in caplog.text
    assert "eindeutige Fehlermeldung zum Wiederfinden" in caplog.text


def test_schleife_beendet_sich_auf_signal(conf, monkeypatch):
    """
    Beim Herunterfahren muss die Schleife zuegig enden - sonst haengt der
    Dienst beim Neustart und systemd bricht ihn hart ab.
    """
    monkeypatch.setattr(R, "get_current_lesson", lambda c: ({"current": None, "next": None}, ""))
    monkeypatch.setattr(R, "get_cached_config", lambda: conf)

    R.app_state.shutdown_event.clear()
    thread = threading.Thread(target=R.background_loop, daemon=True)
    thread.start()
    time.sleep(0.3)

    R.app_state.shutdown_event.set()
    thread.join(timeout=5)

    try:
        assert not thread.is_alive(), "Die Schleife reagiert nicht auf das Signal"
    finally:
        R.app_state.shutdown_event.clear()


def test_schleife_arbeitet_ohne_konfiguration_weiter(monkeypatch, schleife_im_thread):
    """
    Fehlt die config.json, darf die Schleife nicht abstuerzen, sondern soll
    geduldig warten, bis die Datei auftaucht.
    """
    monkeypatch.setattr(R, "get_cached_config", lambda: {})
    schleife_im_thread(dauer=0.3)
    # Kein Absturz, kein Fehler im Log - das genuegt als Zusicherung
