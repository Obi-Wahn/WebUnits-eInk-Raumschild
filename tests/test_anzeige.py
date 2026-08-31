"""
Tests fuer die Darstellung auf dem E-Paper.

Das Display ist 250 x 122 Pixel gross - jeder Pixel zaehlt. Diese Tests
sichern ab, dass nichts ueber den Rand laeuft und dass bei Platzmangel die
richtige Information erhalten bleibt.

Gezeichnet wird mit echtem Pillow, aber in einen Speicherpuffer (siehe
conftest.py). Layoutfehler fallen dadurch auf, ohne das Panel zu beruehren.
"""
import raumanzeige as R
from conftest import uhrzeit


def stunde(fach="Mathematik", lehrer="Ab", klasse="9B", info="", code=None):
    """Baut ein fertiges Lesson-Objekt fuer die Layouttests."""
    return R.Lesson(fach, fach, lehrer, klasse, "08:00 - 08:45", "1. Std.", code, info)


# ==============================================================================
# truncate_to_width: Kuerzen an der Wortgrenze
# ==============================================================================
def test_passender_text_bleibt_unveraendert(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    assert R.truncate_to_width(zeichenflaeche, "kurz", schrift, 240) == "kurz"


def test_leerer_text_bleibt_leer(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    assert R.truncate_to_width(zeichenflaeche, "", schrift, 240) == ""


def test_gekuerzter_text_passt_und_endet_mit_auslassungszeichen(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    lang = "Achtung: Raumaenderung nach In2 und danach zurueck in den Stammraum"
    ergebnis = R.truncate_to_width(zeichenflaeche, lang, schrift, 240)

    assert R.get_text_width(zeichenflaeche, ergebnis, schrift) <= 240
    assert ergebnis.endswith(R.UI_ELLIPSIS)


def test_getrennt_wird_an_der_wortgrenze(zeichenflaeche):
    """'Raumaenderung nach…' ist lesbarer als 'Raumaenderung nac…'."""
    schrift = R.app_state.global_fonts["small"]
    lang = "Achtung: Raumaenderung nach In2 und danach zurueck in den Stammraum"
    ergebnis = R.truncate_to_width(zeichenflaeche, lang, schrift, 240)

    letztes_wort = ergebnis[:-len(R.UI_ELLIPSIS)].split()[-1]
    assert letztes_wort in lang.split()


def test_einzelnes_ueberlanges_wort_wird_zeichenweise_gekuerzt(zeichenflaeche):
    """Ohne Leerzeichen gibt es keine Wortgrenze - trotzdem darf nichts ueberlaufen."""
    schrift = R.app_state.global_fonts["small"]
    wort = "Donaudampfschifffahrtsgesellschaftskapitaenspatentpruefungsordnung"
    ergebnis = R.truncate_to_width(zeichenflaeche, wort, schrift, 240)

    assert R.get_text_width(zeichenflaeche, ergebnis, schrift) <= 240
    assert ergebnis.endswith(R.UI_ELLIPSIS)


def test_absurd_schmale_vorgabe_stuerzt_nicht_ab(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    assert isinstance(R.truncate_to_width(zeichenflaeche, "Text", schrift, 5), str)


def test_auslassungszeichen_ist_darstellbar(zeichenflaeche):
    """Waere '…' in der Schrift nicht enthalten, erschiene ein leeres Kaestchen."""
    schrift = R.app_state.global_fonts["small"]
    assert R.get_text_width(zeichenflaeche, R.UI_ELLIPSIS, schrift) > 0


# ==============================================================================
# build_detail_line: gestaffeltes Nachgeben statt stumpfem Abschneiden
# ==============================================================================
BREITE = R.UI_WIDTH - 2 * R.UI_MARGIN


def test_bei_genug_platz_steht_alles_ausgeschrieben(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    zeile = R.build_detail_line(zeichenflaeche, stunde(info="Buch S. 12"), schrift, BREITE)
    assert "Lehrkraft: Ab" in zeile
    assert "Buch S. 12" in zeile


def test_erste_stufe_streicht_nur_die_beschriftung(zeichenflaeche):
    """
    Das Kuerzel der Lehrkraft bleibt erhalten - innerhalb der
    Schulgemeinschaft ist es gelaeufig und braucht keine Beschriftung.
    """
    schrift = R.app_state.global_fonts["small"]
    eintrag = stunde(lehrer="Ef", klasse="7A", info="Aufgaben in IServ bearbeiten")
    zeile = R.build_detail_line(zeichenflaeche, eintrag, schrift, BREITE)

    assert "Ef" in zeile.split(" | ")
    assert "Lehrkraft" not in zeile
    assert "Aufgaben in IServ bearbeiten" in zeile
    assert R.UI_ELLIPSIS not in zeile


def test_wichtige_rauminformation_ueberlebt(zeichenflaeche):
    """
    Der Grund fuer die ganze Staffelung: Bei stumpfem Abschneiden ginge
    ausgerechnet die Raumangabe verloren - also das, wofuer jemand vor der
    Tuer steht.
    """
    schrift = R.app_state.global_fonts["small"]
    eintrag = stunde(lehrer="Gk", klasse="8C", info="Achtung: Raumaenderung nach In2")
    zeile = R.build_detail_line(zeichenflaeche, eintrag, schrift, BREITE)

    assert "In2" in zeile
    assert R.UI_ELLIPSIS not in zeile
    assert "8C" in zeile


def test_ergebnis_passt_immer_in_die_breite(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    faelle = [
        stunde(info=""),
        stunde(info="Buch S. 12"),
        stunde(klasse="7A", lehrer="Ef", info="Aufgaben in IServ bearbeiten"),
        stunde(klasse="8C", lehrer="Gk", info="Achtung: Raumaenderung nach In2"),
        stunde(klasse="11B", lehrer="Cd", info="Theorieunterricht - Netzwerktechnik"),
        stunde(klasse="A" * 80, lehrer="", info=""),
        stunde(info="Sehr langer Hinweis der die Zeile in jedem Fall deutlich sprengt"),
    ]
    for eintrag in faelle:
        zeile = R.build_detail_line(zeichenflaeche, eintrag, schrift, BREITE)
        breite = R.get_text_width(zeichenflaeche, zeile, schrift)
        assert breite <= BREITE, f"{breite} px zu breit: {zeile}"


def test_ohne_angaben_bleibt_die_zeile_leer(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    zeile = R.build_detail_line(zeichenflaeche, stunde(lehrer="", klasse=""), schrift, BREITE)
    assert zeile == ""


def test_fehlende_lehrkraft_erzeugt_keine_leeren_trenner(zeichenflaeche):
    schrift = R.app_state.global_fonts["small"]
    eintrag = stunde(lehrer="", klasse="7A", info="Aufgaben in IServ bearbeiten")
    zeile = R.build_detail_line(zeichenflaeche, eintrag, schrift, BREITE)

    assert " |  | " not in zeile
    assert not zeile.strip().endswith("|")


# ==============================================================================
# Status-Kaesten (AUSFALL / VERTRETUNG)
# ==============================================================================
#: Oberkante des Status-Kastens im JETZT-Block (y_offset 30 + 13)
KASTEN_OBEN = 43


def _kasten_rechts_im_bild(bild):
    """
    Sucht im gezeichneten Bild die rechte Kante des schwarzen Status-Kastens.

    Untersucht wird das tatsaechlich erzeugte Bild, nicht die Rechnung aus dem
    Programm - sonst wuerde der Test nur die eigene Arithmetik bestaetigen und
    eine fest verdrahtete Kastenbreite gar nicht bemerken.

    Ausgewertet wird die oberste Zeile des Kastens: Dort ist er durchgehend
    schwarz, weil die weisse Beschriftung erst eine Zeile tiefer beginnt. Wir
    laufen vom linken Rand nach rechts, bis es weiss wird. Der Fachname steht
    zwar in derselben Bildzeile, aber erst nach einer weissen Luecke - er kann
    also nicht mitgezaehlt werden.
    """
    pixel = bild.load()
    x = R.UI_MARGIN
    while x < R.UI_WIDTH and pixel[x, KASTEN_OBEN] == 0:   # 0 = schwarz
        x += 1
    return x - 1


def test_kastenbreite_folgt_der_beschriftung(conf, display_attrappe, monkeypatch):
    """
    Ein laengeres Etikett muss einen breiteren Kasten ergeben. Waere die Breite
    wie frueher fest im Code hinterlegt, bliebe sie hier gleich.
    """
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    daten = {"current": stunde(code="irregular"), "next": None}

    monkeypatch.setitem(R.STATUS_LABELS, "irregular", "KURZ")
    R.update_display_logic(daten, "", conf)
    schmal = _kasten_rechts_im_bild(display_attrappe.letztes_bild)

    monkeypatch.setitem(R.STATUS_LABELS, "irregular", "SEHR LANGES ETIKETT")
    R.update_display_logic(daten, "", conf)
    breit = _kasten_rechts_im_bild(display_attrappe.letztes_bild)

    assert breit > schmal + 20, f"Kasten waechst nicht mit: {schmal} -> {breit}"


def test_beschriftung_hat_rand_im_kasten(conf, display_attrappe):
    """
    Der frueher fest verdrahtete Wert 82 liess den Text genau auf der
    Kastenkante enden. Rechts muss ein schwarzer Rand ohne Schrift bleiben.
    """
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    R.update_display_logic({"current": stunde(code="irregular"), "next": None}, "", conf)

    bild = display_attrappe.letztes_bild
    rechts = _kasten_rechts_im_bild(bild)
    pixel = bild.load()

    # Die letzten Spalten vor der Kante duerfen keine weisse Schrift enthalten
    for x in range(rechts - R.UI_BADGE_PADDING + 1, rechts + 1):
        for y in range(KASTEN_OBEN + 1, KASTEN_OBEN + 14):
            assert pixel[x, y] == 0, f"Schrift beruehrt die Kastenkante bei x={x}"


# ==============================================================================
# Vollstaendiges Zeichnen
# ==============================================================================
def test_alle_zustaende_zeichnen_fehlerfrei(conf, display_attrappe):
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    for code in (None, "cancelled", "irregular"):
        daten = {"current": stunde(code=code, info="Ein Hinweis"),
                 "next": stunde(fach="Deutsch")}
        R.update_display_logic(daten, "", conf)
    assert display_attrappe.anzahl_anzeigen == 3


def test_offline_markierung_veraendert_das_bild(conf, display_attrappe):
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    daten = {"current": stunde(), "next": None}

    R.update_display_logic(daten, "", conf, stale=True)
    mit_markierung = display_attrappe.letztes_bild.tobytes()

    R.update_display_logic(daten, "", conf, stale=False)
    ohne_markierung = display_attrappe.letztes_bild.tobytes()

    assert mit_markierung != ohne_markierung


def test_offline_markierung_ueberschreibt_die_uhrzeit_nicht(conf, display_attrappe):
    """Der Hinweis sitzt rechts aussen und darf die Kopfzeile nicht stoeren."""
    R.app_state.simulated_datetime = uhrzeit(8, 20)
    daten = {"current": stunde(), "next": None}

    R.update_display_logic(daten, "", conf, stale=True)
    mit = display_attrappe.letztes_bild.crop((120, 0, 215, R.UI_HEADER_HEIGHT))
    mit_daten = mit.tobytes()

    R.update_display_logic(daten, "", conf, stale=False)
    ohne = display_attrappe.letztes_bild.crop((120, 0, 215, R.UI_HEADER_HEIGHT))

    assert mit_daten == ohne.tobytes()


def test_mehrzeilige_meldung_wird_gezeichnet(conf, display_attrappe):
    """Etwa 'Unterrichtsfrei!\\n(Ferienzeit)' - beide Zeilen mittig."""
    R.app_state.simulated_datetime = uhrzeit(10, 0)
    R.update_display_logic(None, "Unterrichtsfrei!\n(Ferienzeit)", conf)
    assert display_attrappe.anzahl_anzeigen == 1
