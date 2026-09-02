"""
Tests fuer die Kennzeichnung von Klassenarbeiten und Klausuren.

WebUntis speichert zu einer Pruefung keinen Raum - bekannt sind nur Datum,
Uhrzeit, Klasse und Fach. Welche Pruefung im eigenen Raum stattfindet, ist
deshalb eine Schlussfolgerung und keine Auskunft. Die Tests hier halten fest,
wie streng diese Schlussfolgerung sein muss.

Der teure Fehler waere ein falsch gesetztes Etikett: "KLASSENARBEIT" an einer
Tuer, hinter der Unterricht laeuft, macht das ganze Schild unglaubwuerdig.
Deshalb pruefen mehrere Tests nicht, dass etwas erkannt wird, sondern dass es
gerade NICHT erkannt wird - bei anderer Klasse, anderem Fach, anderer Zeit.
"""
import datetime
import time

import pytest

import tuerschild as R
from tuerschild import untis
from tuerschild.anzeige import waehle_etikett
from tuerschild.konstanten import (PRUEFUNG_KLASSENARBEIT, PRUEFUNG_KLAUSUR,
                                   STATUS_LABELS)
from tuerschild.zustand import Lesson
from conftest import MONTAG, RohStunde


def pruefung(h1, m1, h2, m2, klassen, fach):
    """Ein Pruefungseintrag in der Form, die hole_pruefungen() liefert."""
    return {
        "start": datetime.datetime.combine(MONTAG, datetime.time(h1, m1)),
        "ende": datetime.datetime.combine(MONTAG, datetime.time(h2, m2)),
        "klassen": set(klassen),
        "fach": fach,
    }


# ==============================================================================
# Das Wort: Klassenarbeit oder Klausur?
# ==============================================================================
@pytest.mark.parametrize("name,erwartet", [
    ("5a", PRUEFUNG_KLASSENARBEIT),
    ("10c", PRUEFUNG_KLASSENARBEIT),
    ("11a", PRUEFUNG_KLAUSUR),
    ("12", PRUEFUNG_KLAUSUR),
    ("13", PRUEFUNG_KLAUSUR),
])
def test_das_wort_richtet_sich_nach_dem_jahrgang(name, erwartet):
    """
    Die Grenze liegt zwischen 10 und 11. Ein Wort, das an der Schule niemand
    benutzt, faellt jeden Tag jedem auf, der am Schild vorbeigeht.
    """
    assert untis.pruefungs_etikett([name]) == erwartet


def test_ein_klassenname_ohne_ziffer_gilt_als_oberstufe():
    """"Q1" und "EF" gibt es nur in der Oberstufe."""
    assert untis.pruefungs_etikett(["Q1"]) == PRUEFUNG_KLAUSUR


def test_bei_gemischten_jahrgaengen_gilt_das_kleinere_wort():
    """
    Nur wenn ALLE Klassen in der Oberstufe sind, steht dort "KLAUSUR". Sitzt
    eine zehnte Klasse mit dabei, waere das Wort fuer sie falsch.
    """
    assert untis.pruefungs_etikett(["10c", "11a"]) == PRUEFUNG_KLASSENARBEIT


@pytest.mark.parametrize("name", ["8a", "8b", "8c"])
def test_mehrere_klassen_desselben_jahrgangs(name):
    assert untis.pruefungs_etikett(["8a", "8b", "8c"]) == PRUEFUNG_KLASSENARBEIT


# ==============================================================================
# Der Abgleich: gehoert diese Pruefung in diesen Raum?
# ==============================================================================
STUNDE = (datetime.datetime.combine(MONTAG, datetime.time(8, 0)),
          datetime.datetime.combine(MONTAG, datetime.time(8, 45)))


def test_zeit_klasse_und_fach_zusammen_ergeben_einen_treffer():
    treffer = untis.passende_pruefung(*STUNDE, {17}, {83},
                                      [pruefung(8, 0, 8, 45, [17], 83)])
    assert treffer is True


def test_eine_andere_klasse_zur_selben_zeit_ist_kein_treffer():
    """
    Der haeufigste Fall ueberhaupt: An einer Schule mit hundert Pruefungen in
    drei Wochen schreibt zu fast jeder Stunde irgendwer irgendwo.
    """
    treffer = untis.passende_pruefung(*STUNDE, {17}, {83},
                                      [pruefung(8, 0, 8, 45, [99], 83)])
    assert treffer is False


def test_dieselbe_klasse_im_anderen_fach_ist_kein_treffer():
    """
    Die gefaehrliche Konstellation: Die Klasse schreibt woanders, waehrend im
    Raum ihr naechstes Fach laeuft. Ohne den Fach-Vergleich stuende hier ein
    Etikett an der falschen Tuer.
    """
    treffer = untis.passende_pruefung(*STUNDE, {17}, {83},
                                      [pruefung(8, 0, 8, 45, [17], 126)])
    assert treffer is False


def test_eine_pruefung_zu_anderer_zeit_ist_kein_treffer():
    treffer = untis.passende_pruefung(*STUNDE, {17}, {83},
                                      [pruefung(9, 55, 10, 40, [17], 83)])
    assert treffer is False


def test_eine_doppelstunde_erfasst_beide_stunden():
    """
    Eine Arbeit von 08:00 bis 09:35 laeuft ueber zwei Stunden. Beide muessen
    das Etikett tragen, sonst verschwindet es zur zweiten Stunde.
    """
    lang = [pruefung(8, 0, 9, 35, [17], 83)]
    zweite = (datetime.datetime.combine(MONTAG, datetime.time(8, 50)),
              datetime.datetime.combine(MONTAG, datetime.time(9, 35)))

    assert untis.passende_pruefung(*STUNDE, {17}, {83}, lang) is True
    assert untis.passende_pruefung(*zweite, {17}, {83}, lang) is True


def test_direkt_angrenzende_zeiten_ueberschneiden_sich_nicht():
    """
    Eine Pruefung, die endet, wenn die Stunde beginnt, gehoert nicht dazu -
    sonst traegt die Folgestunde faelschlich das Etikett.
    """
    treffer = untis.passende_pruefung(*STUNDE, {17}, {83},
                                      [pruefung(7, 10, 8, 0, [17], 83)])
    assert treffer is False


def test_ohne_pruefungen_gibt_es_keinen_treffer():
    assert untis.passende_pruefung(*STUNDE, {17}, {83}, []) is False


# ==============================================================================
# Vom Abruf bis in den Datensatz
# ==============================================================================
def test_die_stunde_traegt_das_etikett(conf):
    roh = RohStunde(8, 0, 8, 45, "Deutsch", klasse="5a", fach_id=83,
                    klasse_id=17)
    stunde = untis.parse_lesson(roh, conf, [pruefung(8, 0, 8, 45, [17], 83)])

    assert stunde.pruefung == PRUEFUNG_KLASSENARBEIT


def test_ohne_passende_pruefung_bleibt_das_etikett_leer(conf):
    roh = RohStunde(8, 0, 8, 45, "Deutsch", klasse="5a", fach_id=83,
                    klasse_id=17)
    stunde = untis.parse_lesson(roh, conf, [pruefung(8, 0, 8, 45, [99], 83)])

    assert stunde.pruefung == ""


def test_ohne_pruefungsliste_laeuft_alles_wie_bisher(conf):
    """Die Offline-Ruecklage und die Tests rufen ohne Pruefungen auf."""
    roh = RohStunde(8, 0, 8, 45, "Deutsch", klasse="5a")
    assert untis.parse_lesson(roh, conf).pruefung == ""


def test_das_etikett_ueberlebt_in_der_offline_ruecklage(conf):
    """
    Faellt WebUntis waehrend einer Arbeit aus, soll an der Tuer weiter stehen,
    dass drinnen geschrieben wird. Deshalb steckt das Etikett im Datensatz und
    nicht nur im Bild.
    """
    roh = [RohStunde(8, 0, 8, 45, "Deutsch", klasse="5a", fach_id=83,
                     klasse_id=17)]
    plan = untis.resolve_timetable(roh, conf, [pruefung(8, 0, 8, 45, [17], 83)])

    assert plan[0].lesson.pruefung == PRUEFUNG_KLASSENARBEIT


# ==============================================================================
# Der Abruf der Pruefungen
# ==============================================================================
class PruefungsAttrappe:
    """Ein rohes Pruefungsobjekt, wie die Bibliothek es liefert."""

    def __init__(self, h1, m1, h2, m2, klassen, fach, schueler=None):
        self.start = datetime.datetime.combine(MONTAG, datetime.time(h1, m1))
        self.end = datetime.datetime.combine(MONTAG, datetime.time(h2, m2))
        self._data = {"classes": list(klassen), "subject": fach,
                      "students": schueler if schueler is not None else [],
                      "teachers": [7]}


class Pruefungssitzung:
    def __init__(self, eintraege=None, fehler=None):
        self._eintraege = eintraege if eintraege is not None else []
        self._fehler = fehler
        self.abrufe = 0

    def exams(self, start, end):
        self.abrufe += 1
        if self._fehler:
            raise self._fehler
        return self._eintraege


def test_der_abruf_wird_in_die_einfache_form_gebracht():
    sitzung = Pruefungssitzung([PruefungsAttrappe(8, 0, 8, 45, [17], 83)])
    ergebnis = untis.hole_pruefungen(sitzung, MONTAG)

    assert ergebnis[0]["klassen"] == {17}
    assert ergebnis[0]["fach"] == 83


def test_keine_einzige_schuelernummer_bleibt_liegen():
    """
    Die Antwort von WebUntis enthaelt zu jeder Pruefung die vollstaendige
    Schuelerliste - an einer Schule schnell mehrere hundert Nummern. Das
    Tuerschild braucht sie nicht, also behaelt es sie gar nicht erst.
    """
    schueler = [4711, 4712, 4713]
    sitzung = Pruefungssitzung([PruefungsAttrappe(8, 0, 8, 45, [17], 83,
                                                  schueler=schueler)])
    ergebnis = untis.hole_pruefungen(sitzung, MONTAG)

    inhalt = repr(ergebnis) + repr(R.app_state.cached_exams)
    for nummer in schueler:
        assert str(nummer) not in inhalt


def test_der_abruf_wird_zwischengespeichert():
    """API-Schonung: Klausurtermine aendern sich nicht im Minutentakt."""
    sitzung = Pruefungssitzung([PruefungsAttrappe(8, 0, 8, 45, [17], 83)])
    untis.hole_pruefungen(sitzung, MONTAG)
    untis.hole_pruefungen(sitzung, MONTAG)

    assert sitzung.abrufe == 1


def test_an_einem_neuen_tag_wird_neu_abgerufen():
    """
    Ohne das Datum im Schluessel zeigte ein ueber Mitternacht laufendes Geraet
    bis zu einer Stunde lang die Termine von gestern.
    """
    sitzung = Pruefungssitzung([PruefungsAttrappe(8, 0, 8, 45, [17], 83)])
    untis.hole_pruefungen(sitzung, MONTAG)
    untis.hole_pruefungen(sitzung, MONTAG + datetime.timedelta(days=1))

    assert sitzung.abrufe == 2


def test_ein_fehlgeschlagener_abruf_kostet_nur_die_etiketten():
    """
    An anderen Schulen fehlt dem Anzeige-Zugang womoeglich das Recht dazu. Das
    ist kein Grund, die Anzeige zu verlieren.
    """
    sitzung = Pruefungssitzung(fehler=RuntimeError("no right for getExams()"))
    assert untis.hole_pruefungen(sitzung, MONTAG) == []


# ==============================================================================
# Das Etikett im Bild: es ist nur ein Kasten da
# ==============================================================================
def stunde_mit(status=None, pruefung=""):
    return Lesson(fach="De", fach_lang="Deutsch", lehrer="Gk", klasse="5a",
                  zeit="08:00 - 08:45", stunde="1. Std.", status_code=status,
                  stunden_info="", pruefung=pruefung)


def test_der_ausfall_schlaegt_die_arbeit():
    """
    Eine ausgefallene Arbeit findet nicht statt. Stuende dort "KLASSENARBEIT",
    hielte das Schild jemanden vom Anklopfen ab, obwohl der Raum leer ist.
    """
    etikett = waehle_etikett(stunde_mit(status="cancelled",
                                        pruefung=PRUEFUNG_KLASSENARBEIT))
    assert etikett == STATUS_LABELS["cancelled"]


def test_die_arbeit_schlaegt_die_vertretung():
    """Dass drinnen geschrieben wird, ist die wichtigere Auskunft."""
    etikett = waehle_etikett(stunde_mit(status="irregular",
                                        pruefung=PRUEFUNG_KLAUSUR))
    assert etikett == PRUEFUNG_KLAUSUR


def test_ohne_arbeit_bleibt_es_beim_bisherigen_status():
    assert waehle_etikett(stunde_mit(status="irregular")) == \
        STATUS_LABELS["irregular"]


def test_gewoehnlicher_unterricht_traegt_kein_etikett():
    assert waehle_etikett(stunde_mit()) is None


def test_das_etikett_steht_auch_wirklich_im_bild(conf, zeichenflaeche):
    """
    Die Auswahl allein genuegt nicht - sie muss auch gezeichnet werden. Ein
    Etikett, das nur in der Funktion existiert, hilft an der Tuer niemandem.
    """
    from tuerschild import anzeige

    gezeichnet = []
    echt = anzeige.ImageDraw.ImageDraw.text

    def mitschnitt(self, xy, text, *args, **kwargs):
        gezeichnet.append(text)
        return echt(self, xy, text, *args, **kwargs)

    anzeige.ImageDraw.ImageDraw.text = mitschnitt
    try:
        anzeige.zeichne_anzeige(
            {"current": stunde_mit(pruefung=PRUEFUNG_KLASSENARBEIT),
             "next": None}, "", conf)
    finally:
        anzeige.ImageDraw.ImageDraw.text = echt

    assert PRUEFUNG_KLASSENARBEIT in gezeichnet


def test_das_etikett_passt_neben_die_ueblichen_faecher(zeichenflaeche):
    """
    "KLASSENARBEIT" ist das laengste Wort, das je in diesem Kasten stand. Es
    darf den Fachnamen nicht aus dem Bild draengen.
    """
    from tuerschild.anzeige import get_text_width
    from tuerschild.konstanten import (UI_BADGE_GAP, UI_BADGE_PADDING,
                                       UI_MARGIN, UI_WIDTH)

    R.init_fonts()
    f_small = R.app_state.global_fonts["small"]
    f_reg = R.app_state.global_fonts["reg"]

    kasten = get_text_width(zeichenflaeche, PRUEFUNG_KLASSENARBEIT, f_small)
    fach_x = UI_MARGIN + kasten + 2 * UI_BADGE_PADDING + UI_BADGE_GAP
    platz = UI_WIDTH - UI_MARGIN - fach_x

    for fach in ("Mathematik", "Deutsch", "Erdkunde", "Religion (kath.)",
                 "Darstellendes Spiel"):
        breite = get_text_width(zeichenflaeche, fach, f_reg)
        assert breite <= platz, f"{fach} passt nicht mehr neben das Etikett"


# ==============================================================================
# Die Bildbeschreibung im Web-Interface
# ==============================================================================
# Die Vorschau ist ein Bitmap - fuer eine Vorlesesoftware stumm. Die
# Beschreibung im alt-Attribut muss dasselbe sagen wie das Bild, sonst fehlt
# genau denen die Auskunft, die auf sie angewiesen sind.
def test_die_bildbeschreibung_nennt_die_arbeit():
    from tuerschild.web import vorschau_beschreibung

    text = vorschau_beschreibung(
        {"current": stunde_mit(pruefung=PRUEFUNG_KLASSENARBEIT), "next": None},
        "")
    assert "Klassenarbeit" in text


def test_die_bildbeschreibung_folgt_derselben_rangfolge():
    """Steht im Bild AUSFALL, darf die Beschreibung nicht 'Klausur' sagen."""
    from tuerschild.web import vorschau_beschreibung

    text = vorschau_beschreibung(
        {"current": stunde_mit(status="cancelled",
                               pruefung=PRUEFUNG_KLAUSUR), "next": None}, "")
    assert "fällt aus" in text
    assert "Klausur" not in text
