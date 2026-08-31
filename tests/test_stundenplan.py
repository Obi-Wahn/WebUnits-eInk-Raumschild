"""
Tests fuer das Auslesen des Tagesplans und die Auswahl von JETZT und DANACH.

Das ist die Kernlogik des Tuerschilds: Aus einer Liste von Stunden muss zu
jedem Zeitpunkt die richtige Stunde herausfallen.
"""
import datetime

import tuerschild as R
from conftest import MONTAG, RohStunde, uhrzeit


# ==============================================================================
# resolve_timetable: rohe Objekte in eigene Datensaetze umwandeln
# ==============================================================================
def test_alle_stunden_werden_ausgelesen(rohplan, conf):
    _, roh = rohplan
    ergebnis = R.resolve_timetable(roh, conf)
    assert len(ergebnis) == 3


def test_ergebnis_ist_chronologisch_sortiert(conf):
    # Absichtlich in falscher Reihenfolge uebergeben
    roh = [RohStunde(9, 55, 10, 40, "Englisch"),
           RohStunde(8, 0, 8, 45, "Mathematik"),
           RohStunde(8, 50, 9, 35, "Deutsch")]
    ergebnis = R.resolve_timetable(roh, conf)
    assert [e.lesson.fach for e in ergebnis] == ["Mathematik", "Deutsch", "Englisch"]


def test_felder_werden_vollstaendig_aufgeloest(stundenplan):
    erste = stundenplan[0].lesson
    assert erste.fach == "Mathematik"
    assert erste.lehrer == "Ab"
    assert erste.klasse == "9B"
    assert erste.zeit == "08:00 - 08:45"


def test_stundenname_kommt_aus_der_konfiguration(stundenplan):
    """Die Uhrzeit 08:00 muss dem Namen '1. Std.' aus SCHEDULE zugeordnet werden."""
    assert stundenplan[0].lesson.stunde == "1. Std."


def test_eintrag_ohne_zeitangabe_wird_uebersprungen(conf):
    class OhneZeit:
        start = None
        end = None

    assert R.resolve_timetable([OhneZeit()], conf) == []


def test_kaputte_einzelstunde_verwirft_nicht_den_ganzen_tag(conf):
    """
    Eine unlesbare Stunde darf nicht dazu fuehren, dass das Display den
    kompletten Tag verliert - die uebrigen Stunden bleiben nutzbar.
    """
    class Kaputt(RohStunde):
        @property
        def subjects(self):
            raise ValueError("beschaedigter Eintrag")

    roh = [RohStunde(8, 0, 8, 45, "Mathematik"),
           Kaputt(8, 50, 9, 35, "Defekt"),
           RohStunde(9, 55, 10, 40, "Englisch")]
    ergebnis = R.resolve_timetable(roh, conf)
    assert [e.lesson.fach for e in ergebnis] == ["Mathematik", "Englisch"]


# ==============================================================================
# select_lessons: Auswahl von JETZT und DANACH
# ==============================================================================
def test_waehrend_der_ersten_stunde(stundenplan, conf):
    daten, meldung = R.select_lessons(stundenplan, conf, uhrzeit(8, 20))
    assert daten["current"].fach == "Mathematik"
    assert daten["next"].fach == "Deutsch"
    assert meldung == ""


def test_fuenf_minuten_vorlauf_greift(stundenplan, conf):
    """
    Um 08:47 laeuft eigentlich noch keine Stunde - die zweite beginnt erst um
    08:50. Das Display soll aber bereits umschalten, damit es beim Klingeln
    schon aktuell ist.
    """
    daten, _ = R.select_lessons(stundenplan, conf, uhrzeit(8, 47))
    assert daten["current"].fach == "Deutsch"


def test_vor_dem_vorlauf_laeuft_noch_die_alte_stunde(stundenplan, conf):
    """Gegenprobe: Eine Minute frueher gilt der Vorlauf noch nicht."""
    daten, _ = R.select_lessons(stundenplan, conf, uhrzeit(8, 44))
    assert daten["current"].fach == "Mathematik"


def test_status_code_wird_durchgereicht(stundenplan, conf):
    daten, _ = R.select_lessons(stundenplan, conf, uhrzeit(9, 0))
    assert daten["current"].status_code == "cancelled"


def test_in_der_pause_erscheint_der_pausenname(stundenplan, conf):
    daten, meldung = R.select_lessons(stundenplan, conf, uhrzeit(9, 40))
    assert daten["current"] is None
    assert meldung == "1. Pause"


def test_vor_schulbeginn(stundenplan, conf):
    _, meldung = R.select_lessons(stundenplan, conf, uhrzeit(7, 0))
    assert meldung == "Guten Morgen!"


def test_nach_schulschluss(stundenplan, conf):
    _, meldung = R.select_lessons(stundenplan, conf, uhrzeit(16, 0))
    assert meldung == "Unterrichtsende"


def test_freistunde_zwischen_den_stunden(conf):
    """Zeit ausserhalb jeder Stunde und ausserhalb jeder Pause."""
    plan = R.resolve_timetable([RohStunde(8, 0, 8, 45, "Mathematik")], conf)
    _, meldung = R.select_lessons(plan, conf, uhrzeit(10, 30))
    assert meldung == "Raum ist frei"


def test_leerer_plan_bedeutet_unterrichtsfrei(conf):
    daten, meldung = R.select_lessons([], conf, uhrzeit(10, 0))
    assert daten == {"current": None, "next": None}
    assert meldung == "Unterrichtsfrei"


def test_nach_der_letzten_stunde_gibt_es_kein_danach(stundenplan, conf):
    daten, _ = R.select_lessons(stundenplan, conf, uhrzeit(10, 20))
    assert daten["current"].fach == "Englisch"
    assert daten["next"] is None


def test_kaputte_zeitangaben_in_der_konfiguration_stuerzen_nicht_ab(stundenplan):
    """Ein unbrauchbarer SCHEDULE-Eintrag darf das Display nicht lahmlegen."""
    kaputt = {"SCHEDULE": {"DAY_START": "voellig kaputt", "DAY_END": "auch"}}
    _, meldung = R.select_lessons(stundenplan, kaputt, uhrzeit(12, 0))
    assert meldung == "Raum ist frei"
