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
    assert 'class="btn btn-neutral"' in inhalt
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


# ==============================================================================
# Ein Farbsystem statt sieben Einzelfarben
# ==============================================================================
# Vorher hatte jeder Knopf seine eigene Farbe, zusammengesucht aus zwei
# Systemen: #007BFF, #6f42c1, #DC3545 und #28A745 stammen aus Bootstrap, der
# Rest aus der Slate-Palette der uebrigen Seite. Aus der Farbe eines Knopfes
# liess sich nichts ableiten - sie war Dekoration.
#
# Jetzt gibt es vier Rollen. Diese Tests halten sie fest, denn eine achte Farbe
# schleicht sich beim naechsten neuen Knopf muehelos wieder ein, und niemandem
# faellt es einzeln auf.
ROLLENFARBEN = {
    "0f172a",   # btn-haupt    Hauptaktion des Abschnitts
    "475569",   # btn-neutral  harmlos, jederzeit umkehrbar
    "f59e0b",   # btn-test     greift sichtbar ein, aber voruebergehend
    "dc2626",   # btn-gefahr   beendet den Betrieb
}


def test_die_knoepfe_benutzen_nur_die_vier_rollenfarben():
    gefunden = set(re.findall(r"\.btn-[a-z-]+ \{[^}]*background-color: #([0-9a-fA-F]{6})",
                              vorlage()))
    ueberzaehlig = {f.lower() for f in gefunden} - ROLLENFARBEN
    assert not ueberzaehlig, (
        f"Neue Knopffarben ausserhalb der vier Rollen: {sorted(ueberzaehlig)}"
    )


def test_jede_rolle_wird_auch_benutzt(webclient):
    """
    Gegenprobe. Ohne sie waere der Test darueber auch dann gruen, wenn die
    Rollen zwar definiert, im Markup aber gar nicht mehr vergeben sind.

    Geprueft wird auf das ATTRIBUT, nicht auf den blossen Klassennamen: Der
    steht ohnehin im Stylesheet der Seite, die Bedingung waere immer erfuellt.
    Genau daran ist eine erste Fassung dieses Tests gescheitert - sie blieb
    gruen, obwohl im Markup keine einzige Schaltflaeche die Rolle mehr trug.
    """
    inhalt = seite(webclient)
    for rolle in ("btn-haupt", "btn-neutral", "btn-test", "btn-gefahr"):
        assert f'class="btn {rolle}"' in inhalt, (
            f"Die Rolle {rolle} wird auf der Seite nicht mehr vergeben"
        )


def test_kein_knopf_traegt_seine_farbe_im_markup():
    """
    So sind die Sonderfarben ueberhaupt entstanden: ein style-Attribut am
    einzelnen Knopf, an der zentralen Palette vorbei.
    """
    treffer = re.findall(r'class="btn[^"]*"[^>]*style="[^"]*background', vorlage())
    assert not treffer, f"Farbe direkt am Knopf statt ueber eine Rolle: {treffer}"


# ==============================================================================
# Massstab am Rechner
# ==============================================================================
def desktop_block():
    """Der Inhalt der @media-Regel ab 800 Pixel Breite."""
    treffer = re.search(r"@media \(min-width: 800px\) \{(.*?)\n        \}",
                        vorlage(), re.DOTALL)
    assert treffer, "Die Regel fuer breite Bildschirme ist verschwunden"
    return treffer.group(1)


def test_die_knoepfe_sind_am_rechner_kleiner():
    """
    Die Grundmasse sind fuers Telefon gemacht - 15 Pixel Innenabstand treffen
    dort den Daumen. Am Rechner wirkt dieselbe Schaltflaeche wie eine
    vergroesserte App, und die Seite wird unnoetig lang.

    Faellt diese Regel weg, merkt es auf dem Telefon niemand.
    """
    grund = re.search(r"\n        \.btn \{[^}]*padding: (\d+)px", vorlage())
    schmal = re.search(r"\.btn \{[^}]*padding: (\d+)px", desktop_block())

    assert grund, "Der Grundabstand der Knoepfe ist nicht mehr auffindbar"
    assert schmal, "Am Rechner gilt kein eigener Abstand mehr"
    assert int(schmal.group(1)) < int(grund.group(1)), (
        f"Am Rechner sind die Knoepfe nicht kleiner "
        f"({schmal.group(1)}px gegen {grund.group(1)}px)"
    )
