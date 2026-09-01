"""
Tests fuer die Vorschau im Web-Interface (/vorschau.png).

WAS SICH GEAENDERT HAT:
Die Vorschau war eine Nachbildung in HTML - aehnlich, aber eine zweite Fassung
desselben Layouts. Zwei Fassungen laufen auseinander, und vor allem zeigte die
Nachbildung nicht, was auf 250x122 Pixeln wirklich Platz hat: Gerade die
Kuerzungen langer Fachnamen sah man dort nie, obwohl sie auf dem Schild der
haeufigste Grund fuer Rueckfragen sind.

Jetzt wird dasselbe Bild ausgeliefert, das auch auf das E-Paper geht - mit
derselben Funktion gezeichnet. Diese Tests sichern genau das ab.
"""
import io as bytes_io

from PIL import Image

import tuerschild as R
from tuerschild import anzeige, web
from tuerschild.konstanten import UI_HEIGHT, UI_WIDTH


def bild(webclient):
    client, kopf = webclient
    antwort = client.get("/vorschau.png", headers=kopf)
    assert antwort.status_code == 200
    return antwort, Image.open(bytes_io.BytesIO(antwort.get_data()))


# ==============================================================================
# Das Bild selbst
# ==============================================================================
def test_die_vorschau_hat_die_masse_des_displays(webclient):
    antwort, b = bild(webclient)
    assert antwort.mimetype == "image/png"
    assert b.size == (UI_WIDTH, UI_HEIGHT) == (250, 122)


def test_die_vorschau_ist_schwarzweiss(webclient):
    """
    Ein E-Paper kennt nur zwei Zustaende. Waere das Bild in Graustufen,
    zeigte die Vorschau Zwischentoene, die es auf dem Schild nicht gibt.
    """
    _, b = bild(webclient)
    assert b.mode == "1"


def test_die_vorschau_zeigt_wirklich_etwas(webclient):
    """Gegen ein leeres Bild, das jede andere Pruefung bestehen wuerde."""
    _, b = bild(webclient)
    schwarz = sum(1 for wert in b.convert("L").tobytes() if wert == 0)
    assert schwarz > 200, "Das Bild ist praktisch leer"


def test_die_vorschau_darf_nicht_zwischengespeichert_werden(webclient):
    """
    Ohne das zeigte der Browser nach einem Update weiter das alte Bild - und
    die Vorschau behauptete etwas, das laengst nicht mehr stimmt.
    """
    antwort, _ = bild(webclient)
    assert "no-store" in antwort.headers.get("Cache-Control", "")


def test_die_vorschau_braucht_eine_anmeldung(webclient):
    client, _ = webclient
    assert client.get("/vorschau.png").status_code == 401


# ==============================================================================
# Dasselbe Bild wie auf dem Schild
# ==============================================================================
def test_die_vorschau_ist_dasselbe_bild_wie_auf_dem_display(webclient, conf):
    """
    Der wichtigste Test dieser Datei: gleiches Bild, Pixel fuer Pixel.

    Wuerde die Vorschau eigenstaendig zeichnen, waere sie wieder eine zweite
    Fassung - und die naechste Layoutaenderung ginge an ihr vorbei, ohne dass
    es jemand bemerkt.
    """
    R.app_state.current_display_data = {
        "current": R.Lesson("INF", "Informatik", "Ab", "11B",
                            "09:55 - 10:40", "3. Std.", "irregular", "Theorie"),
        "next": None,
    }
    R.app_state.current_display_msg = ""

    _, aus_dem_web = bild(webclient)
    direkt = anzeige.zeichne_anzeige(R.app_state.current_display_data, "",
                                     R.get_cached_config(), stale=False)

    assert aus_dem_web.tobytes() == direkt.tobytes()


def test_die_vorschau_folgt_dem_zustand(webclient):
    """Andere Anzeige, anderes Bild - sonst zeigte sie irgendetwas Festes."""
    R.app_state.current_display_msg = "Raum ist frei"
    _, eins = bild(webclient)

    R.app_state.current_display_msg = "Unterrichtsende"
    _, zwei = bild(webclient)

    assert eins.tobytes() != zwei.tobytes()


def test_die_ruecklage_wird_im_bild_gekennzeichnet(webclient):
    """Das Ausrufezeichen in der Kopfzeile - dieselbe Marke wie auf dem Schild."""
    R.app_state.current_display_msg = "Raum ist frei"
    _, frisch = bild(webclient)

    R.app_state.data_is_stale = True
    _, alt = bild(webclient)

    assert frisch.tobytes() != alt.tobytes()


def test_die_vorschau_fasst_die_hardware_nicht_an(webclient, monkeypatch):
    """
    Sie wird aufgerufen, waehrend das Tuerschild arbeitet. Es wird gezeichnet,
    nicht gesendet - der Treiber darf dabei nicht angesprochen werden.
    """
    def verboten(*args, **kwargs):
        raise AssertionError("Die Vorschau hat den Displaytreiber angefasst")

    monkeypatch.setattr(anzeige.hardware, "epd2in13_V3",
                        type("Sperre", (), {"EPD": staticmethod(verboten)}))
    bild(webclient)


# ==============================================================================
# Die Beschreibung fuer Vorlesesoftware
# ==============================================================================
def test_die_beschreibung_nennt_die_stunden():
    beschreibung = web.vorschau_beschreibung({
        "current": R.Lesson("INF", "Informatik", "Ab", "11B",
                            "09:55 - 10:40", "3. Std.", "irregular", ""),
        "next": R.Lesson("GE", "Geschichte", "Cd", "9B",
                         "10:45 - 11:30", "4. Std.", "cancelled", ""),
    }, "")

    assert "Jetzt:" in beschreibung and "Informatik" in beschreibung
    assert "Danach:" in beschreibung and "Geschichte" in beschreibung
    assert "Vertretung" in beschreibung
    assert "fällt aus" in beschreibung


def test_die_beschreibung_nennt_die_meldung():
    assert web.vorschau_beschreibung(None, "Schönes Wochenende!") == "Schönes Wochenende!"


def test_die_beschreibung_macht_aus_dem_umbruch_ein_leerzeichen():
    """Ein Zeilenumbruch in einem Attribut hilft niemandem."""
    assert web.vorschau_beschreibung(None, "Unterrichtsfrei!\nFerien") \
        == "Unterrichtsfrei! Ferien"


def test_die_beschreibung_ist_nie_leer():
    """Ein leeres alt-Attribut gilt als 'Bild ohne Inhalt' und wird verschwiegen."""
    assert web.vorschau_beschreibung(None, "")
    assert web.vorschau_beschreibung({}, None)


def test_die_beschreibung_steht_auf_der_seite(webclient):
    R.app_state.current_display_msg = "Schönes Wochenende!"
    client, kopf = webclient
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)

    assert 'src="/vorschau.png' in inhalt
    assert "Schönes Wochenende!" in inhalt


def test_die_seite_bildet_die_anzeige_nicht_mehr_in_html_nach(webclient):
    """
    Gegenprobe zum Umbau: Die alte Nachbildung ist wirklich weg. Bliebe sie
    daneben stehen, gaebe es die zwei Fassungen wieder, die abzuschaffen der
    ganze Sinn der Uebung war.
    """
    client, kopf = webclient
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)

    assert 'class="lesson-block"' not in inhalt
    assert 'class="tag-red"' not in inhalt
    assert 'class="tag-yellow"' not in inhalt
