"""
Tests fuer Konfiguration, Grenzwerte und Netzwerkadresse.

Diese Funktionen verarbeiten Werte, die von Hand in die config.json geschrieben
werden koennen. Ein unbrauchbarer Eintrag darf das Tuerschild nicht lahmlegen.
"""
import json
import os
import socket
import tempfile

import tuerschild as R
from tuerschild import konfiguration


# ==============================================================================
# get_update_interval: Grenzen des Abrufintervalls
# ==============================================================================
def test_gueltiger_wert_bleibt_erhalten():
    assert R.get_update_interval({"AUTO_UPDATE_SECONDS": 900}) == 900


def test_zu_kleiner_wert_wird_angehoben():
    """
    Schutz des WebUntis-Servers: Haengen an einer Schule viele Tuerschilder,
    summieren sich zu kurze Intervalle zu erheblicher Last.
    """
    assert R.get_update_interval({"AUTO_UPDATE_SECONDS": 60}) == R.MIN_UPDATE_SECONDS
    assert R.get_update_interval({"AUTO_UPDATE_SECONDS": 5}) == R.MIN_UPDATE_SECONDS


def test_zu_grosser_wert_wird_begrenzt():
    assert R.get_update_interval({"AUTO_UPDATE_SECONDS": 999999}) == R.MAX_UPDATE_SECONDS


def test_unbrauchbare_eintraege_fallen_auf_die_voreinstellung_zurueck():
    for wert in ["abc", None, [], {}]:
        assert R.get_update_interval({"AUTO_UPDATE_SECONDS": wert}) == R.DEFAULT_UPDATE_SECONDS


def test_fehlender_schluessel_ergibt_die_voreinstellung():
    assert R.get_update_interval({}) == R.DEFAULT_UPDATE_SECONDS


def test_mindestwert_ist_nicht_kleiner_als_eine_minute():
    """Sicherung gegen ein versehentliches Herabsetzen der Konstante."""
    assert R.MIN_UPDATE_SECONDS >= 60
    assert R.MIN_UPDATE_SECONDS < R.MAX_UPDATE_SECONDS


# ==============================================================================
# Konfiguration lesen und schreiben
# ==============================================================================
def test_speichern_und_lesen_im_wechsel(conf):
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    datei.close()
    konfiguration.CONFIG_FILE = datei.name
    R.app_state.last_config_mtime = 0
    try:
        R.save_config({**conf, "ROOM_NAME": "Testraum"})
        gelesen = R.get_cached_config()
        assert gelesen["ROOM_NAME"] == "Testraum"
    finally:
        os.unlink(datei.name)


def test_gespeicherte_datei_ist_nur_fuer_den_besitzer_lesbar(conf):
    """
    In der config.json stehen die Zugangsdaten der Schule. Das atomare
    Schreiben ueber eine temporaere Datei setzt die Rechte auf 0600 - das
    haelt dieser Test fest.
    """
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    datei.close()
    konfiguration.CONFIG_FILE = datei.name
    try:
        R.save_config(conf)
        rechte = os.stat(datei.name).st_mode & 0o777
        assert rechte == 0o600, oct(rechte)
    finally:
        os.unlink(datei.name)


def test_umlaute_bleiben_lesbar(conf):
    """ensure_ascii=False - sonst stuenden Feriennamen als \\u00e4 in der Datei."""
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    datei.close()
    konfiguration.CONFIG_FILE = datei.name
    R.app_state.last_config_mtime = 0
    try:
        R.save_config({**conf, "ROOM_NAME": "Übungsraum"})
        with open(datei.name, encoding="utf-8") as f:
            inhalt = f.read()
        assert "Übungsraum" in inhalt
    finally:
        os.unlink(datei.name)


def test_fehlende_datei_ergibt_leere_konfiguration():
    konfiguration.CONFIG_FILE = "/gibt/es/nicht/config.json"
    R.app_state.last_config_mtime = 0
    R.app_state.cached_config = {}
    assert R.get_cached_config() == {}


def test_beschaedigte_datei_legt_das_programm_nicht_lahm():
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    datei.write("{ das ist kein gueltiges JSON")
    datei.close()
    konfiguration.CONFIG_FILE = datei.name
    R.app_state.last_config_mtime = 0
    R.app_state.cached_config = {}
    try:
        # Darf keine Ausnahme werfen, sondern liefert den letzten bekannten Stand
        assert isinstance(R.get_cached_config(), dict)
    finally:
        os.unlink(datei.name)


def test_beispielkonfiguration_ist_gueltiges_json(conf):
    """Die Vorlage wird beim Einrichten kopiert - sie muss fehlerfrei sein."""
    for schluessel in ["UNTIS_SERVER", "UNTIS_SCHOOL", "ROOM_NAME", "SCHEDULE"]:
        assert schluessel in conf
    assert "LESSONS" in conf["SCHEDULE"]
    assert "BREAKS" in conf["SCHEDULE"]


# ==============================================================================
# get_local_ip: Adresse fuer die Startausgabe
# ==============================================================================
def test_liefert_eine_gueltige_adresse():
    adresse = R.get_local_ip()
    if adresse is not None:          # In Umgebungen ohne Netz ist None korrekt
        socket.inet_aton(adresse)
        assert not adresse.startswith("127.")


def test_ohne_netzwerk_kommt_none(monkeypatch):
    def kein_netz(*args, **kwargs):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(socket, "socket", kein_netz)
    assert R.get_local_ip() is None


def test_loopback_antwort_wird_zu_none(monkeypatch):
    """
    Ohne Netzwerkverbindung liefert das System die Loopback-Adresse. Die hilft
    dem Nutzer nicht weiter und darf nicht als Netzwerkadresse ausgegeben werden.
    """
    class Attrappe:
        def settimeout(self, wert): pass
        def connect(self, ziel): pass
        def getsockname(self): return ("127.0.0.1", 12345)
        def close(self): pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: Attrappe())
    assert R.get_local_ip() is None
