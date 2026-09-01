"""
Tests fuer die Pfade zu config.json und zu den Waveshare-Treibern.

WARUM DIESE TESTS EXISTIEREN:
Beim Aufteilen des Programms in ein Paket sind diese Pfade die gefaehrlichste
Stelle. Frueher lag der gesamte Code in einer Datei im Projektverzeichnis, und
beide Pfade wurden aus deren __file__ abgeleitet. Die Moduldateien liegen nun
eine Ebene tiefer - ohne Anpassung zeigten die Pfade in das Paketverzeichnis.

Besonders tueckisch ist der Treiberpfad: Ein falscher Wert fuehrt nicht zu
einer Fehlermeldung, sondern dazu, dass das Programm scheinbar normal
weiterlaeuft und lediglich das Display dunkel bleibt. Genau diese Art Fehler
faellt im Betrieb erst auf, wenn jemand vor der Tuer steht.

Die uebrige Testsuite kann das nicht abdecken: Sie laeuft ohne echten Treiber
und wuerde einen falschen Pfad daher nie bemerken.
"""
import os
import sys

from tuerschild import hardware, konfiguration, konstanten


def projektverzeichnis():
    """Das Verzeichnis, in dem raumanzeige.py liegt."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_projektverzeichnis_ist_nicht_das_paketverzeichnis():
    assert konstanten.PROJEKT_VERZEICHNIS == projektverzeichnis()
    assert not konstanten.PROJEKT_VERZEICHNIS.endswith("tuerschild")


def test_konfigurationsdatei_liegt_im_projektverzeichnis():
    assert konfiguration.CONFIG_FILE == os.path.join(projektverzeichnis(), "config.json")


def test_treiberpfad_liegt_im_projektverzeichnis():
    erwartet = os.path.join(projektverzeichnis(),
                            "e-Paper", "RaspberryPi_JetsonNano", "python", "lib")
    assert konstanten.WAVESHARE_LIB == erwartet


def test_hardware_benutzt_den_pfad_aus_den_konstanten():
    """
    Prueft die VERDRAHTUNG, nicht nur den Wert.

    Ein Test, der allein konstanten.WAVESHARE_LIB prueft, wuerde nicht bemerken,
    wenn hardware.py den Pfad wieder selbst aus __file__ bildet und die
    Konstante gar nicht benutzt - der Wert waere dann richtig und der Treiber
    trotzdem unauffindbar.
    """
    assert hardware.WAVESHARE_LIB is konstanten.WAVESHARE_LIB


def test_treiberpfad_landet_im_suchpfad():
    """
    Auf dem Raspberry Pi existiert das Treiberverzeichnis und muss dann auch im
    Suchpfad von Python stehen - sonst bleibt das Display dunkel. Wo es nicht
    existiert (etwa auf den Rechnern von GitHub), ist nichts zu pruefen.
    """
    if os.path.isdir(konstanten.WAVESHARE_LIB):
        assert konstanten.WAVESHARE_LIB in sys.path


def test_die_html_vorlage_liegt_dort_wo_flask_sie_sucht():
    """
    Die Vorlage stand frueher als Zeichenkette in web.py. Als eigene Datei
    haengt sie nun daran, dass Flask sie findet: Das Vorlagenverzeichnis
    richtet sich nach dem Paket, in dem Flask() aufgerufen wurde. Ein
    verschobenes oder umbenanntes Verzeichnis faellt sonst erst beim ersten
    Aufruf der Seite auf - mit einem Serverfehler statt des Dashboards.
    """
    from tuerschild import web
    erwartet = os.path.join(os.path.dirname(os.path.abspath(web.__file__)),
                            "templates", web.DASHBOARD_VORLAGE)
    assert os.path.isfile(erwartet)
    assert web.app.jinja_env.get_or_select_template(web.DASHBOARD_VORLAGE)


def test_einstiegspunkt_liegt_neben_dem_paket():
    """raumanzeige.py wird von start.sh und vom systemd-Dienst aufgerufen."""
    assert os.path.isfile(os.path.join(projektverzeichnis(), "raumanzeige.py"))


def test_beispielkonfiguration_liegt_neben_dem_einstiegspunkt():
    """Die Installationsanleitung kopiert sie mit einem relativen Pfad."""
    assert os.path.isfile(os.path.join(projektverzeichnis(), "config.example.json"))
