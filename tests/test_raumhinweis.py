"""
Tests fuer den Hinweis unter dem Feld "Anzeigeraum".

Das Formular nimmt ROOM_NAME_MAX_LEN (40) Zeichen an, in die Kopfzeile des
Displays passen aber nur rund ein Viertel davon. Bisher merkte man das erst
nach dem Speichern, an den drei Punkten in der Vorschau. Der Hinweis nennt die
Spanne vorher.

Eine Zahl in einem Hinweistext ist eine Behauptung ueber die Anzeige - und
genau die Sorte Behauptung, die nach der naechsten Layoutaenderung still falsch
dasteht. Die Tests hier pruefen deshalb nicht, dass ueberhaupt eine Zahl da
steht, sondern dass sie stimmt: Ein Raumname in der genannten Laenge wird
tatsaechlich ungekuerzt gezeichnet, einer mit einem Zeichen mehr nicht.
"""
import datetime
import re
import time

import pytest
from PIL import Image, ImageDraw

import tuerschild as R
from tuerschild.anzeige import (RAUM_MUSTER_BREIT, RAUM_MUSTER_SCHMAL,
                                get_text_width, sichtbare_raumzeichen,
                                zeichne_anzeige)
from tuerschild.konstanten import (ROOM_NAME_MAX_LEN, UI_ELLIPSIS,
                                   UI_HEADER_HEIGHT, UI_WIDTH, WOCHENTAGE_KURZ)

from conftest import MONTAG


def breitester_wochentag() -> int:
    """
    Der Wochentag, dessen Kuerzel am meisten Platz frisst - also der Tag, an dem
    fuer den Raumnamen am wenigsten uebrig bleibt.

    Die Schaetzung im Formular rechnet mit genau diesem Tag: Eine Zahl, die nur
    montags stimmt und dienstags nicht mehr, waere keine Hilfe, sondern eine
    Falle.
    """
    R.init_fonts()
    draw = ImageDraw.Draw(Image.new("1", (UI_WIDTH, UI_HEADER_HEIGHT), 255))
    schrift = R.app_state.global_fonts["small"]
    return max(range(len(WOCHENTAGE_KURZ)),
               key=lambda i: get_text_width(draw, WOCHENTAGE_KURZ[i], schrift))


def setze_wochentag(tag_index: int) -> None:
    """Stellt die Uhr des Programms auf den gewuenschten Wochentag."""
    # MONTAG aus conftest ist ein Montag, also ist +tag_index der gesuchte Tag.
    R.app_state.simulated_datetime = datetime.datetime.combine(
        MONTAG + datetime.timedelta(days=tag_index), datetime.time(22, 22))
    R.app_state.simulation_started_at = time.time()


def gezeichneter_raumname(monkeypatch, name, stale=False, tag_index=0):
    """
    Gibt zurueck, was von 'name' wirklich in der Kopfzeile landet.

    Dafuer wird ImageDraw.text mitgeschnitten. Der Umweg ist noetig, weil das
    Ergebnis ein 1-Bit-Bild ist: Ob dort ein Name gekuerzt wurde, laesst sich
    aus den Pixeln nur muehsam ablesen. Der Mitschnitt fragt stattdessen das,
    worum es geht - welcher Text gezeichnet wurde.
    """
    setze_wochentag(tag_index)
    protokoll = []
    echtes_text = ImageDraw.ImageDraw.text

    def mitschnitt(self, xy, text, *args, **kwargs):
        protokoll.append(text)
        return echtes_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", mitschnitt)
    zeichne_anzeige(None, "Kein Unterricht", {"ROOM_NAME": name}, stale=stale)

    # Der Raumname ist der einzige gezeichnete Text, der mit dem Anfang des
    # Namens beginnt - Uhrzeit und Meldung tun das nicht.
    treffer = [t for t in protokoll if t.startswith(name[:3])]
    assert treffer, f"Raumname nicht gezeichnet, protokolliert wurde: {protokoll}"
    return treffer[0]


@pytest.mark.parametrize("tag_index", range(len(WOCHENTAGE_KURZ)))
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("muster,stelle", [(RAUM_MUSTER_BREIT, 0),
                                           (RAUM_MUSTER_SCHMAL, 1)])
def test_die_genannte_laenge_wird_ungekuerzt_gezeichnet(monkeypatch, muster,
                                                        stelle, stale,
                                                        tag_index):
    """
    Die untere bzw. obere Grenze der Spanne passt wirklich noch - und zwar an
    jedem Wochentag, nicht nur an dem, an dem gerade getestet wird.
    """
    anzahl = sichtbare_raumzeichen(stale)[stelle]
    name = (muster * 10)[:anzahl]

    assert gezeichneter_raumname(monkeypatch, name, stale, tag_index) == name


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("muster,stelle", [(RAUM_MUSTER_BREIT, 0),
                                           (RAUM_MUSTER_SCHMAL, 1)])
def test_ein_zeichen_mehr_passt_nicht_mehr(monkeypatch, muster, stelle, stale):
    """
    Die Gegenprobe, am engsten Wochentag. Ohne sie waere jede zu kleine Zahl
    richtig - "1 Zeichen sichtbar" bestuende den Test darueber muehelos.
    """
    anzahl = sichtbare_raumzeichen(stale)[stelle]
    name = (muster * 10)[:anzahl + 1]

    gezeichnet = gezeichneter_raumname(monkeypatch, name, stale,
                                       breitester_wochentag())

    assert gezeichnet.endswith(UI_ELLIPSIS)


def test_das_offline_zeichen_kostet_platz():
    """
    Steht das Warndreieck in der Kopfzeile, bleibt fuer den Raumnamen weniger
    uebrig. Rechnete die Schaetzung das nicht mit, waere sie genau dann zu
    grosszuegig, wenn ohnehin schon etwas schiefgeht.
    """
    normal = sichtbare_raumzeichen(False)
    mit_zeichen = sichtbare_raumzeichen(True)

    assert mit_zeichen[0] < normal[0]
    assert mit_zeichen[1] < normal[1]


def test_die_spanne_ist_eine_spanne():
    """Breite Grossbuchstaben zuerst, schmale Kleinbuchstaben danach."""
    schmal_zuerst, breit_zuletzt = sichtbare_raumzeichen()

    assert 0 < schmal_zuerst < breit_zuletzt < ROOM_NAME_MAX_LEN


def test_der_hinweis_steht_unter_dem_feld(webclient):
    """Der Hinweis gehoert an das Feld, nicht irgendwohin auf die Seite."""
    client, kopf = webclient
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)

    feld = inhalt.index('name="ROOM_NAME"')
    hinweis = inhalt.index('class="feld-hinweis"')
    naechstes_feld = inhalt.index("</div>", feld)

    assert feld < hinweis < naechstes_feld


def test_der_hinweis_nennt_die_gerechneten_zahlen(webclient):
    """
    Die Zahlen im Text muessen die aus der Kopfzeilen-Geometrie sein. Ein
    Hinweis mit fest eingetippten Zahlen bestuende diesen Test nicht.
    """
    client, kopf = webclient
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)

    absatz = re.search(r'<p class="feld-hinweis">(.*?)</p>', inhalt, re.S)
    assert absatz, "Hinweisabsatz fehlt"
    zahlen = [int(z) for z in re.findall(r"\d+", absatz.group(1))]

    assert zahlen == list(sichtbare_raumzeichen())
