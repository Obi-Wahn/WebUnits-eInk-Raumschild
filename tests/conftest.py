"""
==============================================================================
Gemeinsame Vorrichtungen ("Fixtures") fuer die Testsuite
==============================================================================
Diese Datei wird von pytest automatisch geladen. Was hier als @pytest.fixture
steht, koennen alle Testdateien einfach als Parameter anfordern.

WICHTIGSTE AUFGABE DIESER DATEI:
Die Tests laufen auch auf dem Raspberry Pi selbst - also auf einem Geraet, an
dem das echte E-Paper haengt und der Dienst womoeglich gerade laeuft. Ohne
Vorkehrung wuerde ein Test das Display tatsaechlich ansteuern und mitten im
Schulbetrieb einen vollen Loeschzyklus ausloesen.
Dafuer greifen zwei Vorkehrungen ineinander:

1. TUERSCHILD_OHNE_HARDWARE (ganz unten in dieser Beschreibung, im Code aber
   ganz oben): Sie muss VOR dem Import des Pakets gesetzt sein und verhindert,
   dass die Hardware-Bibliotheken ueberhaupt geladen werden.
2. Die Vorrichtung 'display_attrappe': Sie ersetzt den Treiber vor JEDEM Test
   durch eine Attrappe (autouse=True). Gezeichnet wird mit echtem Pillow in
   einen Speicherpuffer - Layoutfehler fallen also auf, das Panel wird aber nie
   beruehrt.

Warum beides? Punkt 2 allein genuegt nicht. Der Waveshare-Treiber belegt die
GPIO-Pins schon beim Import, also lange bevor eine Vorrichtung eingreifen kann.
Auf dem Raspberry Pi hatte das zwei Folgen: Lief das Tuerschild gerade, brach
schon das Einlesen dieser Datei mit "GPIO busy" ab; lief es nicht, griffen die
Tests selbst nach den Pins. Punkt 1 schneidet das an der Wurzel ab.
"""
import datetime
import json
import os
import sys
import tempfile
import types

import pytest

# HARDWARE-SPERRE - DIESE ZEILE MUSS VOR DEM IMPORT DES PAKETS STEHEN.
# Sie sorgt dafuer, dass tuerschild.hardware weder GPIO noch I2C noch den
# Displaytreiber laedt. Rutscht sie hinter den Import, ist sie wirkungslos:
# Python fuehrt ein Modul nur ein einziges Mal aus, und der Treiber hat die
# Pins dann bereits angefasst. tests/test_hardwaresperre.py haelt das fest.
os.environ["TUERSCHILD_OHNE_HARDWARE"] = "1"

# Das Hauptprogramm liegt eine Ebene ueber diesem Verzeichnis
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tuerschild as R  # noqa: E402
from tuerschild import hardware, konfiguration  # noqa: E402

# Ein Montag - als fester Bezugspunkt fuer alle Zeitrechnungen.
# Feste Daten statt "heute" halten die Tests reproduzierbar: Ein Test, der nur
# dienstags faellt, ist schlimmer als gar kein Test.
MONTAG = datetime.date(2026, 8, 31)


def uhrzeit(stunde: int, minute: int) -> datetime.datetime:
    """Erzeugt einen Zeitpunkt am Bezugstag MONTAG."""
    return datetime.datetime.combine(MONTAG, datetime.time(stunde, minute))


# ------------------------------------------------------------------------------
# Test-Doubles fuer die rohen WebUntis-Objekte
# ------------------------------------------------------------------------------
class Referenz:
    """Bildet ein Fach-, Lehrkraft- oder Klassen-Objekt der Bibliothek nach."""

    def __init__(self, name, long_name=""):
        self.name = name
        self.long_name = long_name or name


class RohStunde:
    """
    Bildet eine Unterrichtsstunde nach, wie die WebUntis-Bibliothek sie liefert.

    Entscheidend ist das Verhalten von 'sitzung_lebt': Die echte Bibliothek
    loest Fach, Lehrkraft und Klasse erst beim Zugriff auf und fragt dafuer ihre
    Sitzung. Ist die Sitzung beendet und das Netz weg, schlaegt dieser Zugriff
    fehl. Genau das bildet diese Klasse nach - nur so laesst sich pruefen, dass
    die Offline-Ruecklage wirklich ohne Sitzung auskommt.
    """

    def __init__(self, h1, m1, h2, m2, fach, sitzung_lebt=None, code=None,
                 lehrer="Ab", klasse="9B", info=""):
        self.start = datetime.datetime.combine(MONTAG, datetime.time(h1, m1))
        self.end = datetime.datetime.combine(MONTAG, datetime.time(h2, m2))
        self.code = code
        self.info = info
        self.lstext = ""
        self.substText = ""
        self._fach = fach
        self._lehrer = lehrer
        self._klasse = klasse
        # Liste statt bool, damit der Test den Wert nachtraeglich umschalten kann
        self._lebt = sitzung_lebt if sitzung_lebt is not None else [True]

    def _pruefe_sitzung(self):
        if not self._lebt[0]:
            raise ConnectionError("Sitzung beendet, kein Netz - Nachladen unmoeglich")

    @property
    def subjects(self):
        self._pruefe_sitzung()
        return [Referenz(self._fach, self._fach)]

    @property
    def teachers(self):
        self._pruefe_sitzung()
        return [Referenz(self._lehrer)] if self._lehrer else []

    @property
    def klassen(self):
        self._pruefe_sitzung()
        return [Referenz(self._klasse)] if self._klasse else []


class DisplayAttrappe:
    """
    Ersatz fuer den Waveshare-Treiber. Zeichnet nicht auf das Panel, sondern
    merkt sich das erzeugte Bild, damit Tests es untersuchen koennen.
    """

    # Massangaben wie beim echten 2.13"-Display (Hoehe und Breite sind dort
    # vertauscht, weil das Panel quer betrieben wird)
    height = 250
    width = 122

    def __init__(self):
        self.letztes_bild = None
        self.anzahl_anzeigen = 0
        self.anzahl_loeschen = 0

    def init(self):
        pass

    def getbuffer(self, image):
        self.letztes_bild = image
        return image

    def display(self, buffer):
        self.anzahl_anzeigen += 1

    def sleep(self):
        pass

    def Clear(self, farbe):
        self.anzahl_loeschen += 1


# ------------------------------------------------------------------------------
# Vorrichtungen
# ------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def display_attrappe(monkeypatch):
    """
    SICHERHEITSNETZ: Ersetzt den Displaytreiber vor jedem einzelnen Test.

    autouse=True bedeutet, dass diese Vorrichtung automatisch fuer jeden Test
    gilt - auch fuer solche, die gar nichts mit dem Display zu tun haben. Das
    ist Absicht: Ein vergessenes Ersetzen wuerde auf dem Raspberry Pi das echte
    E-Paper ansteuern.
    """
    attrappe = DisplayAttrappe()
    monkeypatch.setattr(hardware, "epd2in13_V3", types.SimpleNamespace(EPD=lambda: attrappe))
    return attrappe


@pytest.fixture(autouse=True)
def sauberer_zustand():
    """
    Setzt den globalen Programmzustand vor und nach jedem Test zurueck.

    AppState ist bewusst ein einziges gemeinsames Objekt. Ohne diese Vorrichtung
    wuerde ein Test dem naechsten seine simulierte Uhrzeit oder seine
    Offline-Ruecklage hinterlassen - und Fehler haengen dann von der
    Reihenfolge der Tests ab, was die Suche zur Qual macht.
    """
    felder = [
        "simulated_datetime", "cached_lessons", "cached_lessons_date",
        "last_successful_sync", "data_is_stale", "current_display_data",
        "current_display_msg", "force_update_flag", "show_demo_once",
        "test_mode_active", "cached_config", "last_config_mtime",
        "cached_holidays", "last_holidays_fetch",
        "stoerung_seit", "stoerung_gemeldet",
        "save_error", "save_ok",
    ]
    vorher = {name: getattr(R.app_state, name) for name in felder}
    vorher["failed_logins"] = dict(R.app_state.failed_logins)
    konfigpfad = konfiguration.CONFIG_FILE

    yield

    for name, wert in vorher.items():
        setattr(R.app_state, name, wert)
    konfiguration.CONFIG_FILE = konfigpfad


@pytest.fixture
def conf():
    """Die mitgelieferte Beispielkonfiguration als Woerterbuch."""
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.example.json")
    with open(pfad, encoding="utf-8") as datei:
        return json.load(datei)


@pytest.fixture
def rohplan():
    """Ein Tagesplan aus rohen WebUntis-Objekten, Sitzung noch aktiv."""
    lebt = [True]
    return lebt, [
        RohStunde(8, 0, 8, 45, "Mathematik", lebt),
        RohStunde(8, 50, 9, 35, "Deutsch", lebt, code="cancelled"),
        RohStunde(9, 55, 10, 40, "Englisch", lebt),
    ]


@pytest.fixture
def stundenplan(rohplan, conf):
    """Derselbe Tagesplan, bereits ausgelesen (Liste von TimedLesson)."""
    _, roh = rohplan
    return R.resolve_timetable(roh, conf)


@pytest.fixture
def zeichenflaeche():
    """Eine leere Zeichenflaeche in Displaygroesse samt geladener Schriften."""
    from PIL import Image, ImageDraw
    R.init_fonts()
    return ImageDraw.Draw(Image.new("1", (R.UI_WIDTH, R.UI_HEIGHT), 255))


@pytest.fixture
def webclient(conf):
    """
    Ein Flask-Testclient samt gueltiger Zugangsdaten.

    Die Konfiguration landet in einer temporaeren Datei, damit die echte
    config.json des Geraets unberuehrt bleibt - auf dem Pi liegen dort die
    Zugangsdaten der Schule.
    """
    datei = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
    json.dump({**conf, "ADMIN_USER": "admin", "ADMIN_PASS": "geheim"},
              datei, ensure_ascii=False)
    datei.close()

    konfiguration.CONFIG_FILE = datei.name
    R.app_state.last_config_mtime = 0

    import base64
    kopf = {"Authorization": "Basic " + base64.b64encode(b"admin:geheim").decode()}

    yield R.app.test_client(), kopf

    os.unlink(datei.name)
