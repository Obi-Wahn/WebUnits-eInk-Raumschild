"""
Tests fuer den Abruf bei WebUntis.

Es wird keine echte Verbindung aufgebaut: Die Sitzung ist durch eine Attrappe
ersetzt. Geprueft wird, wie das Programm auf verschiedene Antworten und Fehler
reagiert - und dass es sich in jedem Fall sauber wieder abmeldet.
"""
import datetime
import socket

import pytest

import tuerschild as R
from tuerschild import untis
from conftest import MONTAG, RohStunde, uhrzeit


class Raumliste:
    """Bildet das Ergebnisobjekt von session.rooms() nach."""

    def __init__(self, treffer):
        self._treffer = treffer

    def filter(self, **kwargs):
        return self._treffer


class SitzungsAttrappe:
    """
    Ersatz fuer webuntis.Session. Notiert, was mit ihr geschehen ist, damit die
    Tests das Verhalten des Programms nachvollziehen koennen.
    """

    def __init__(self, notizblock, stunden=None, fehler_beim_login=None,
                 fehler_beim_plan=None, raum_gefunden=True):
        self.notizen = notizblock
        self._stunden = stunden if stunden is not None else []
        self._fehler_login = fehler_beim_login
        self._fehler_plan = fehler_beim_plan
        self._raum_gefunden = raum_gefunden

    def login(self):
        self.notizen["angemeldet"] = True
        if self._fehler_login:
            raise self._fehler_login
        return self

    def rooms(self):
        return Raumliste(["Raum101"] if self._raum_gefunden else [])

    def holidays(self):
        return []

    def timetable(self, room, start, end):
        if self._fehler_plan:
            raise self._fehler_plan
        return self._stunden

    def logout(self):
        # Der Kernpunkt: Welches Zeitlimit gilt in diesem Moment?
        self.notizen["timeout_bei_logout"] = socket.getdefaulttimeout()
        self.notizen["abgemeldet"] = True


@pytest.fixture
def sitzung(monkeypatch):
    """
    Ersetzt webuntis.Session. Liefert den Notizblock und eine Funktion, mit der
    der Test das gewuenschte Verhalten der Attrappe festlegt.
    """
    notizen = {}

    def einrichten(**kwargs):
        monkeypatch.setattr(untis.webuntis, "Session",
                            lambda **_: SitzungsAttrappe(notizen, **kwargs))
        R.app_state.simulated_datetime = uhrzeit(8, 20)
        return notizen

    return einrichten


# ==============================================================================
# Der eigentliche Abruf
# ==============================================================================
def test_erfolgreicher_abruf_liefert_die_laufende_stunde(sitzung, conf):
    sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik")])
    daten, meldung = R.get_current_lesson(conf)
    assert daten["current"].fach == "Mathematik"


def test_abruf_fuellt_die_offline_ruecklage(sitzung, conf):
    """Ohne diesen Schritt gaebe es bei einem spaeteren Ausfall nichts zu zeigen."""
    sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik"),
                     RohStunde(8, 50, 9, 35, "Deutsch")])
    R.get_current_lesson(conf)

    assert R.app_state.cached_lessons_date == MONTAG
    assert len(R.app_state.cached_lessons) == 2
    assert R.app_state.last_successful_sync is not None


def test_ruecklage_enthaelt_alle_stunden_nicht_nur_die_beiden_aktuellen(sitzung, conf):
    """
    Frueher wurden nur die gerade benoetigten Stunden ausgelesen. Fuer die
    Ruecklage muss der ganze Tag vorliegen, sonst fehlt spaeter der Rest.
    """
    sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik"),
                     RohStunde(8, 50, 9, 35, "Deutsch"),
                     RohStunde(9, 55, 10, 40, "Englisch"),
                     RohStunde(10, 45, 11, 30, "Biologie")])
    R.get_current_lesson(conf)
    assert len(R.app_state.cached_lessons) == 4


# ==============================================================================
# Abmeldung
# ==============================================================================
def test_es_wird_immer_abgemeldet(sitzung, conf):
    notizen = sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik")])
    R.get_current_lesson(conf)
    assert notizen.get("abgemeldet") is True


def test_auch_nach_einem_fehler_wird_abgemeldet(sitzung, conf):
    notizen = sitzung(fehler_beim_plan=RuntimeError("irgendein Fehler"))
    R.get_current_lesson(conf)
    assert notizen.get("abgemeldet") is True


def test_abmeldung_laeuft_noch_mit_zeitlimit(sitzung, conf):
    """
    Wird das Zeitlimit vor der Abmeldung zurueckgesetzt, laeuft logout() ohne
    jede Begrenzung. Reisst die Verbindung genau dann ab, wartet es endlos -
    und mit ihm die gesamte Hintergrundschleife.
    """
    notizen = sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik")])
    R.get_current_lesson(conf)

    assert notizen["timeout_bei_logout"] is not None, \
        "logout() lief ohne Zeitlimit - Reihenfolge im finally-Block pruefen"
    assert notizen["timeout_bei_logout"] == 30


def test_zeitlimit_wird_danach_wiederhergestellt(sitzung, conf):
    """Das Zeitlimit gilt prozessweit - es darf nicht dauerhaft gesetzt bleiben."""
    vorher = socket.getdefaulttimeout()
    sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik")])
    R.get_current_lesson(conf)
    assert socket.getdefaulttimeout() == vorher


# ==============================================================================
# Fehlerbehandlung
# ==============================================================================
def test_unvollstaendige_konfiguration_wird_gemeldet():
    daten, meldung = R.get_current_lesson({"UNTIS_SERVER": "beispiel.de"})
    assert daten is None
    assert meldung == "Konfiguration unvollständig."


def test_fehlender_raum_wird_gemeldet(sitzung, conf):
    sitzung(raum_gefunden=False)
    daten, meldung = R.get_current_lesson(conf)
    assert daten is None
    assert "Raum101" in meldung


def test_netzwerkfehler_wird_als_solcher_erkannt(sitzung, conf):
    fehler = Exception("HTTPSConnectionPool(host='x'): Max retries exceeded")
    sitzung(fehler_beim_login=fehler)
    daten, meldung = R.get_current_lesson(conf)

    assert daten is None
    assert meldung == R.ERR_NO_NETWORK


def test_falsche_zugangsdaten_werden_erkannt(sitzung, conf):
    """
    Diese Meldung darf spaeter NICHT durch die Offline-Ruecklage ersetzt
    werden - sonst behebt niemand das falsche Passwort.
    """
    sitzung(fehler_beim_login=Exception("LoginError: bad credentials"))
    daten, meldung = R.get_current_lesson(conf)

    assert meldung == "Untis-Login falsch"
    assert meldung not in R.TRANSIENT_ERRORS


def test_gesperrter_kalender_gilt_als_ferienzeit(sitzung, conf):
    """WebUntis sperrt den Stundenplan ausserhalb des Schuljahres hart ab."""
    sitzung(fehler_beim_plan=Exception("no valid schoolyear found"))
    daten, meldung = R.get_current_lesson(conf)
    assert "Ferien" in meldung


def test_am_wochenende_wird_kein_plan_abgerufen(sitzung, conf):
    """API-Schonung: Samstag und Sonntag gar nicht erst anfragen."""
    notizen = sitzung(stunden=[RohStunde(8, 0, 8, 45, "Mathematik")])
    samstag = datetime.date(2026, 9, 5)
    R.app_state.simulated_datetime = datetime.datetime.combine(samstag, datetime.time(10, 0))

    daten, meldung = R.get_current_lesson(conf)
    assert meldung == "Schönes Wochenende!"
    assert R.app_state.cached_lessons is None
