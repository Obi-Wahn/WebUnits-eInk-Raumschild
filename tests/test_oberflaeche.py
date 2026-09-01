"""
Tests fuer Entscheidungen an der Oberflaeche, die sich still zuruecknehmen lassen.

Aussehen laesst sich nicht sinnvoll automatisch pruefen - Abstaende und
Schriftgroessen gehoeren vors Auge. Drei Dinge sind aber keine Geschmacksfragen
und wuerden bei einer spaeteren Aenderung unbemerkt verlorengehen:

  * WELCHE FARBE AUF WELCHEM KNOPF SITZT. Rot war auf "Display aus" - einer
    Aktion, die ein Klick rueckgaengig macht - und "System Herunterfahren" war
    hellgrau, obwohl danach jemand zum Geraet laufen und es vom Strom trennen
    muss. Die Warnfarbe sass auf der harmlosen Schaltflaeche.
  * OB DIE STATUSZEILE LESBAR IST. Sie stand als Fusszeile auf #cbd5e1: rund
    1,5:1 Kontrast gegen Weiss, also faktisch unsichtbar - und dort steht auch
    der Hinweis auf eine laufende Zeitsimulation. Inzwischen sitzt sie in der
    dunklen Kopfzeile, wo die Rechnung eine andere ist; der Test holt sich
    beide Farben deshalb aus dem Stylesheet, statt eine davon anzunehmen.
  * WELCHES ABRUFINTERVALL ANGEZEIGT WIRD. Das Formular zeigt den rohen Wert
    aus der Datei, die Uebersicht muss den zeigen, mit dem wirklich gearbeitet
    wird - sonst behauptet die Seite etwas Falsches.
"""
import base64
import json
import os
import re

import pytest

import tuerschild as R
from tuerschild import konfiguration


def seite(webclient):
    client, kopf = webclient
    return client.get("/", headers=kopf).get_data(as_text=True)


def vorlage():
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tuerschild", "templates", "dashboard.html")
    with open(pfad, encoding="utf-8") as datei:
        return datei.read()


# ==============================================================================
# Farben nach Bedeutung
# ==============================================================================
def test_die_schalter_sind_nicht_rot(webclient):
    """
    Display und Touch abzuschalten ist harmlos und mit einem Klick rueckgaengig.
    Rot gehoert dort nicht hin - es stumpft ab, wo es wirklich gebraucht wird.
    """
    inhalt = seite(webclient)
    assert 'class="btn btn-schalter"' in inhalt
    assert 'class="btn btn-off"' not in inhalt
    assert 'class="btn btn-on"' not in inhalt


def test_der_schalter_zeigt_den_jetzigen_zustand(webclient):
    """
    Die Beschriftung nennt die Aktion ("Display aus"), nicht den Zustand. Ohne
    den Punkt muesste man aus dem Verb rueckwaerts schliessen, was gerade gilt.
    """
    inhalt = seite(webclient)
    assert "punkt-an" in inhalt
    assert "eingeschaltet" in inhalt

    R.app_state.cached_config = {**R.get_cached_config(), "DISPLAY_ACTIVE": False}
    R.app_state.last_config_mtime = float("inf")     # Neueinlesen unterbinden
    inhalt = seite(webclient)
    assert "punkt-aus" in inhalt


def test_das_herunterfahren_traegt_die_warnfarbe(webclient):
    """Danach muss jemand zum Geraet und es vom Strom trennen."""
    inhalt = seite(webclient)
    stelle = inhalt.index('action="/sys_shutdown"')
    assert "btn-gefahr" in inhalt[stelle:stelle + 800]


def test_der_neustart_ist_die_leisere_warnung(webclient):
    """Ebenfalls rot, aber nur umrandet - er ist die harmlosere der beiden."""
    inhalt = seite(webclient)
    stelle = inhalt.index('action="/sys_reboot"')
    assert "btn-gefahr-leise" in inhalt[stelle:stelle + 800]


# ==============================================================================
# Lesbarkeit
# ==============================================================================
def leuchtdichte(farbe):
    """Relative Helligkeit nach WCAG."""
    werte = []
    for anteil in (farbe[0:2], farbe[2:4], farbe[4:6]):
        c = int(anteil, 16) / 255
        werte.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * werte[0] + 0.7152 * werte[1] + 0.0722 * werte[2]


def kontrast(vordergrund, hintergrund):
    hell, dunkel = sorted((leuchtdichte(vordergrund), leuchtdichte(hintergrund)),
                          reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


def farbe(regel, eigenschaft="color"):
    treffer = re.search(re.escape(regel) + r" \{[^}]*" + eigenschaft
                        + r": #([0-9a-fA-F]{6})", vorlage())
    assert treffer, f"'{regel}' hat keine Angabe fuer {eigenschaft} mehr"
    return treffer.group(1)


def test_die_statuszeile_ist_lesbar():
    """
    Sie sitzt in der dunklen Kopfzeile und traegt neben dem Zeitstempel den
    Hinweis auf eine laufende Zeitsimulation. Gefordert sind 4,5:1.

    Beide Farben kommen aus dem Stylesheet. Wuerde der Test den Hintergrund
    annehmen, ginge er beim naechsten Umbau der Kopfzeile stillschweigend von
    einer falschen Rechnung aus.
    """
    grund = farbe(".header", "background-color")

    for regel in (".kopf-status", ".kopf-status .simuliert"):
        wert = kontrast(farbe(regel), grund)
        assert wert >= 4.5, (
            f"Kontrast von {regel} nur {wert:.1f}:1 - gefordert sind 4,5:1"
        )


def test_der_zeitstempel_steht_in_der_kopfzeile(webclient):
    """
    Er sagt, wie aktuell das Gezeigte ist. Unter dem letzten Knopf las ihn
    niemand.
    """
    inhalt = seite(webclient)
    kopf = inhalt.index('class="header"')
    ende = inhalt.index('class="content"')
    assert "Stand:" in inhalt[kopf:ende], "Der Zeitstempel steht nicht mehr im Kopf"
    assert 'class="footer"' not in inhalt, "Die alte Fusszeile ist noch da"


# ==============================================================================
# Kurzuebersicht
# ==============================================================================
def test_die_uebersicht_zeigt_das_wirksame_intervall(conf):
    """
    In der Datei darf 30 stehen - gearbeitet wird trotzdem mit dem Mindestwert.
    Zeigte die Seite die 30, behauptete sie etwas, das nicht stimmt.
    """
    import tempfile
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
    json.dump({**conf, "ADMIN_USER": "a", "ADMIN_PASS": "b",
               "AUTO_UPDATE_SECONDS": 30}, datei)
    datei.close()
    konfiguration.CONFIG_FILE = datei.name
    R.app_state.last_config_mtime = 0

    kopf = {"Authorization": "Basic " + base64.b64encode(b"a:b").decode()}
    inhalt = R.app.test_client().get("/", headers=kopf).get_data(as_text=True)
    os.unlink(datei.name)

    assert f"{R.MIN_UPDATE_SECONDS // 60} Min." in inhalt
    assert "30 Min." not in inhalt


def test_die_uebersicht_kennzeichnet_veraltete_daten(webclient):
    """
    Ohne Kennzeichnung sieht ein Plan aus der Ruecklage genauso verbindlich aus
    wie ein frisch abgerufener.
    """
    R.app_state.data_is_stale = True
    inhalt = seite(webclient)

    assert "aus der Rücklage" in inhalt
    assert "status-warn" in inhalt


def test_die_uebersicht_ist_bei_gutem_stand_unauffaellig(webclient):
    inhalt = seite(webclient)
    assert "aktuell" in inhalt
    assert "Echtzeit" in inhalt
