"""
Tests fuer die Meldung bei laenger andauernden Stoerungen.

WARUM ES DIESE MELDUNG GIBT:
Die Offline-Ruecklage ist ein Segen und zugleich das Problem. Faellt WebUntis
aus, zeigt das Schild den zuletzt abgerufenen Plan von heute weiter an und
sieht dabei vollkommen gesund aus - ein plausibler Stundenplan, nur eben einer,
in dem seit Stunden keine Vertretung mehr nachgetragen wurde. Ohne Meldung
faellt das erst auf, wenn eine Klasse vor der falschen Tuer steht.

Ein kurzer Ausfall ist dagegen Alltag und soll niemanden behelligen. Deshalb
die Schwelle STALE_ALERT_SECONDS.
"""
import logging
import time

import pytest

import threading

import tuerschild as R
from tuerschild import steuerung
from tuerschild.konstanten import ERR_NO_NETWORK, STALE_ALERT_SECONDS


def stoerung_seit(sekunden):
    """Versetzt eine laufende Stoerung um die angegebene Dauer in die Vergangenheit."""
    R.app_state.stoerung_seit = time.time() - sekunden


# ==============================================================================
# Dauer messen
# ==============================================================================
def test_erste_stoerung_startet_die_messung():
    assert R.app_state.stoerung_seit is None
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    assert R.app_state.stoerung_seit is not None


def test_der_beginn_bleibt_ueber_mehrere_durchlaeufe_stehen():
    """
    Wuerde bei jedem Schleifendurchlauf neu gestartet, erreichte die Dauer nie
    die Schwelle - die Meldung kaeme dann niemals.
    """
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    beginn = R.app_state.stoerung_seit

    for _ in range(3):
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    assert R.app_state.stoerung_seit == beginn


def test_ein_geglueckter_abruf_setzt_die_messung_zurueck():
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    steuerung.melde_stoerungsdauer(False, "")
    assert R.app_state.stoerung_seit is None
    assert R.app_state.stoerung_gemeldet is False


# ==============================================================================
# Die Meldung selbst
# ==============================================================================
def test_kurze_stoerung_meldet_nichts(caplog):
    with caplog.at_level(logging.ERROR):
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
        stoerung_seit(STALE_ALERT_SECONDS - 60)
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    assert caplog.records == [], "Ein kurzer Aussetzer ist Alltag und keine Meldung wert"
    assert R.app_state.stoerung_gemeldet is False


def test_lange_stoerung_wird_gemeldet(caplog):
    with caplog.at_level(logging.ERROR):
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
        stoerung_seit(STALE_ALERT_SECONDS + 60)
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    text = caplog.records[0].message
    assert "3 Std." in text, "Die Meldung muss die Dauer nennen"
    assert ERR_NO_NETWORK in text, "Die Meldung muss die Ursache nennen"


def test_die_meldung_wiederholt_sich_nicht(caplog):
    """
    Die Schleife laeuft mehrmals pro Minute. Ohne diese Sperre stuenden bis zum
    Abend zehntausende gleichlautende Zeilen im Journal - und die wirklich
    interessanten Meldungen gingen darin unter.
    """
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    stoerung_seit(STALE_ALERT_SECONDS + 60)

    with caplog.at_level(logging.ERROR):
        for _ in range(20):
            steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    assert len(caplog.records) == 1


def test_die_entwarnung_wird_protokolliert(caplog):
    """Wer die Stoerungsmeldung im Journal findet, muss auch ihr Ende finden."""
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    stoerung_seit(STALE_ALERT_SECONDS + 60)
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    with caplog.at_level(logging.INFO):
        steuerung.melde_stoerungsdauer(False, "")

    assert any("wieder erreichbar" in eintrag.message for eintrag in caplog.records)


def test_ohne_vorherige_meldung_gibt_es_keine_entwarnung(caplog):
    """Ein Aussetzer, den niemand gemeldet bekam, braucht auch keine Entwarnung."""
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    with caplog.at_level(logging.INFO):
        steuerung.melde_stoerungsdauer(False, "")

    assert not any("wieder erreichbar" in eintrag.message for eintrag in caplog.records)


def test_eine_neue_stoerung_wird_erneut_gemeldet(caplog):
    """Nach einer Entwarnung muss die naechste lange Stoerung wieder auffallen."""
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    stoerung_seit(STALE_ALERT_SECONDS + 60)
    steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
    steuerung.melde_stoerungsdauer(False, "")
    # Die erste Meldung ist bereits im Protokoll und wuerde mitgezaehlt.
    caplog.clear()

    with caplog.at_level(logging.ERROR):
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)
        stoerung_seit(STALE_ALERT_SECONDS + 60)
        steuerung.melde_stoerungsdauer(True, ERR_NO_NETWORK)

    assert len(caplog.records) == 1


# ==============================================================================
# Darstellung der Dauer
# ==============================================================================
@pytest.mark.parametrize("sekunden, erwartet", [
    (0, "0 Min."),
    (59, "0 Min."),
    (60, "1 Min."),
    (3600, "1 Std."),
    (3660, "1 Std. 1 Min."),
    (12000, "3 Std. 20 Min."),
])
def test_dauer_wird_lesbar_dargestellt(sekunden, erwartet):
    assert R.formatiere_dauer(sekunden) == erwartet


# ==============================================================================
# Anzeige im Web-Interface
# ==============================================================================
def test_kurze_stoerung_erscheint_als_gelber_hinweis(webclient):
    client, kopf = webclient
    R.app_state.stoerung_seit = time.time() - 600
    R.app_state.data_is_stale = True

    seite = client.get("/", headers=kopf).get_data(as_text=True)

    assert "nicht erreichbar" in seite
    assert "warn-msg" in seite
    assert "Netzwerkverbindung" not in seite


def test_lange_stoerung_erscheint_als_rote_meldung(webclient):
    client, kopf = webclient
    R.app_state.stoerung_seit = time.time() - (STALE_ALERT_SECONDS + 60)
    R.app_state.data_is_stale = True

    seite = client.get("/", headers=kopf).get_data(as_text=True)

    assert "error-msg" in seite
    assert "3 Std." in seite, "Die Dauer muss auf der Seite stehen"
    assert "prüfen" in seite


def test_ohne_stoerung_erscheint_kein_hinweis(webclient):
    client, kopf = webclient
    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "nicht erreichbar" not in seite


# ==============================================================================
# Verdrahtung in der Hintergrundschleife
# ==============================================================================
def test_die_schleife_meldet_eine_stoerung_wirklich(conf, monkeypatch):
    """
    Prueft die VERDRAHTUNG, nicht die Funktion.

    Alle Tests darueber rufen melde_stoerungsdauer() selbst auf. Sie wuerden
    nicht bemerken, wenn der Aufruf in background_loop() fehlte - die
    Stoerungsdauer wuerde dann nie gemessen und nie gemeldet, und die ganze
    Datei hier waere gruen fuer nichts.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: (None, ERR_NO_NETWORK))
    monkeypatch.setattr(steuerung, "get_offline_fallback", lambda c: None)
    monkeypatch.setattr(steuerung, "update_display_logic",
                        lambda *args, **kwargs: None)

    R.app_state.shutdown_event.clear()
    thread = threading.Thread(target=R.background_loop, daemon=True)
    thread.start()
    try:
        time.sleep(0.8)
        assert R.app_state.stoerung_seit is not None, (
            "Die Schleife ruft melde_stoerungsdauer() nicht auf - eine Störung "
            "würde nie gemeldet."
        )
    finally:
        R.app_state.shutdown_event.set()
        thread.join(timeout=5)
        R.app_state.shutdown_event.clear()


def test_die_schleife_setzt_nach_erfolg_zurueck(conf, monkeypatch):
    """Gegenprobe: Ein geglueckter Abruf muss die Messung wirklich beenden."""
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": None, "next": None}, "Raum ist frei"))
    monkeypatch.setattr(steuerung, "update_display_logic",
                        lambda *args, **kwargs: None)
    R.app_state.stoerung_seit = time.time() - 600

    R.app_state.shutdown_event.clear()
    thread = threading.Thread(target=R.background_loop, daemon=True)
    thread.start()
    try:
        time.sleep(0.8)
        assert R.app_state.stoerung_seit is None
    finally:
        R.app_state.shutdown_event.set()
        thread.join(timeout=5)
        R.app_state.shutdown_event.clear()
