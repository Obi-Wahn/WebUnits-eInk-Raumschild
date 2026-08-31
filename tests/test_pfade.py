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


def test_einstiegspunkt_liegt_neben_dem_paket():
    """raumanzeige.py wird von start.sh und vom systemd-Dienst aufgerufen."""
    assert os.path.isfile(os.path.join(projektverzeichnis(), "raumanzeige.py"))


def test_beispielkonfiguration_liegt_neben_dem_einstiegspunkt():
    """Die Installationsanleitung kopiert sie mit einem relativen Pfad."""
    assert os.path.isfile(os.path.join(projektverzeichnis(), "config.example.json"))
