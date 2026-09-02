"""
Tests fuer das Selbstneuladen der Bedienseite.

Die Seite wird serverseitig gebaut und hat kein JavaScript. Ohne Zutun stand
sie deshalb still: Was im Browser zu sehen war, war der Zustand des
Augenblicks, in dem die Seite geladen wurde - und zwar beliebig lange. Beim
Vorschaubild faellt das noch auf; bei der Statusliste nicht. Sie behauptete
weiter "Datenstand: aktuell", waehrend das Schild laengst aus der Ruecklage
lief - also ausgerechnet dann, wenn die Seite gebraucht wird.

Ein <meta http-equiv="refresh"> loest das ohne jede neue Abhaengigkeit. Der
Preis ist, dass die Seite VOLLSTAENDIG neu laedt: Eine angefangene Eingabe im
Formular ist dann weg. Deshalb pruefen die Tests hier nicht nur, DASS neu
geladen wird, sondern auch, wann ausdruecklich nicht.
"""
import re

import pytest

import tuerschild as R
from tuerschild.konstanten import UI_REFRESH_SECONDS


def seite(webclient):
    client, kopf = webclient
    return client.get("/", headers=kopf).get_data(as_text=True)


def refresh_wert(inhalt):
    """Die Sekundenzahl aus dem Tag - oder None, wenn keins da ist."""
    treffer = re.search(r'<meta http-equiv="refresh" content="(\d+)"', inhalt)
    return int(treffer.group(1)) if treffer else None


def test_die_seite_laedt_sich_selbst_nach(webclient):
    assert refresh_wert(seite(webclient)) == UI_REFRESH_SECONDS


def test_der_takt_ist_nicht_haeufiger_als_der_des_displays():
    """
    MIN_UPDATE_SECONDS ist der kuerzeste Takt, in dem sich das Schild ueberhaupt
    aendern kann. Haeufiger nachzuladen koennte gar nichts Neues zeigen - es
    erhoehte nur die Wahrscheinlichkeit, jemandem das Formular unter den Haenden
    zu leeren.
    """
    from tuerschild.konstanten import MIN_UPDATE_SECONDS

    assert UI_REFRESH_SECONDS >= MIN_UPDATE_SECONDS


def test_nach_dem_speichern_wird_nicht_nachgeladen(webclient):
    """
    Die Bestaetigung darf nicht weggezogen werden, bevor sie gelesen ist - und
    wer gerade gespeichert hat, sitzt oft noch am Formular.
    """
    R.app_state.save_ok = True
    assert refresh_wert(seite(webclient)) is None


def test_bei_einem_fehler_wird_erst_recht_nicht_nachgeladen(webclient):
    """Eine Fehlermeldung, die nach fuenf Minuten verschwindet, ist keine."""
    R.app_state.save_error = "Raumname zu lang"
    assert refresh_wert(seite(webclient)) is None


def test_die_meldung_steht_auch_wirklich_auf_der_seite(webclient):
    """
    Gegenprobe zu den beiden Tests darueber: Sie wuerden auch dann bestehen,
    wenn die Seite die Meldung gar nicht erst anzeigte.
    """
    R.app_state.save_error = "Raumname zu lang"
    inhalt = seite(webclient)

    assert 'class="error-msg"' in inhalt
    assert "Raumname zu lang" in inhalt


def test_nach_der_meldung_laedt_die_seite_wieder_nach(webclient):
    """
    Die Meldung wird beim Anzeigen verbraucht. Beim naechsten Aufruf muss das
    Nachladen also von selbst zurueckkehren - sonst haette ein einziges
    Speichern die Seite fuer immer stillgelegt.
    """
    R.app_state.save_ok = True
    seite(webclient)

    assert refresh_wert(seite(webclient)) == UI_REFRESH_SECONDS
