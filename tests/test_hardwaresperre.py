"""
Tests fuer die Hardware-Sperre TUERSCHILD_OHNE_HARDWARE.

WARUM ES DIESE SPERRE GIBT:
Der Waveshare-Treiber belegt die GPIO-Pins bereits beim Import - epdconfig.py
legt beim Laden eine Instanz an, die sich ueber gpiozero die Pins sichert.
Auf dem Raspberry Pi hatte das zwei Folgen:

  * Lief das Tuerschild gerade, brach schon das Einlesen von conftest.py ab:
    "lgpio.error: 'GPIO busy'". Die gesamte Testsuite war unbenutzbar.
  * Lief es nicht, griffen die Tests selbst nach den Pins - die Attrappe aus
    conftest.py kommt erst nach dem Import zum Zug und damit zu spaet.

Die Attrappe allein reicht also nicht. Erst die Sperre macht die Trennung
zwischen Testlauf und Geraet dicht.

WARUM MIT UNTERPROZESSEN GEARBEITET WIRD:
Ein Modul fuehrt Python nur ein einziges Mal aus. Innerhalb des laufenden
Testlaufs laesst sich der Schalter daher nicht mehr umlegen - er hat seine
Wirkung laengst entfaltet. Nur ein frischer Prozess kann zeigen, was der
Import mit und ohne Sperre tut.
"""
import json
import os
import subprocess
import sys
import textwrap

from tuerschild import hardware


def projektverzeichnis():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------------------
# Der Zustand im laufenden Testprozess
# ------------------------------------------------------------------------------
def test_sperre_ist_im_testlauf_aktiv():
    assert hardware.OHNE_HARDWARE is True, (
        "conftest.py setzt TUERSCHILD_OHNE_HARDWARE offenbar nicht mehr, "
        "oder erst nach dem Import des Pakets."
    )


def test_gpio_und_i2c_wurden_nicht_geladen():
    """
    Diese drei Namen ersetzt keine Vorrichtung - anders als den Displaytreiber.
    Sind sie belegt, hat der Testlauf echte Hardware angefasst.
    """
    assert hardware.GPIO is None
    assert hardware.smbus is None
    assert hardware.i2c_bus is None


def test_conftest_setzt_die_sperre_vor_dem_paketimport():
    """
    Reihenfolgen-Pruefung am Quelltext, wie in test_startausgabe.py.

    Rutscht die Zeile hinter den Import, ist sie wirkungslos - und das faellt
    sonst nirgends auf, weil auf Rechnern ohne Raspberry-Hardware ohnehin
    nichts zu laden ist. Bemerkt wuerde es erst auf dem Geraet.
    """
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
    with open(pfad, encoding="utf-8") as datei:
        zeilen = datei.read().split("\n")

    sperre = next(i for i, z in enumerate(zeilen)
                  if z.startswith('os.environ["TUERSCHILD_OHNE_HARDWARE"]'))
    erster_import = next(i for i, z in enumerate(zeilen)
                         if z.startswith("import tuerschild"))

    assert sperre < erster_import, (
        "Die Hardware-Sperre steht hinter dem Import des Pakets und ist damit "
        "wirkungslos: Der Treiber hat die GPIO-Pins dann bereits belegt."
    )


# ------------------------------------------------------------------------------
# Auswertung der Umgebungsvariable
# ------------------------------------------------------------------------------
def test_schalter_gilt_als_gesetzt():
    for wert in ("1", "ja", "true", "2"):
        assert hardware._ohne_hardware({"TUERSCHILD_OHNE_HARDWARE": wert}) is True


def test_schalter_gilt_als_nicht_gesetzt():
    assert hardware._ohne_hardware({}) is False
    assert hardware._ohne_hardware({"TUERSCHILD_OHNE_HARDWARE": ""}) is False
    assert hardware._ohne_hardware({"TUERSCHILD_OHNE_HARDWARE": "0"}) is False


# ------------------------------------------------------------------------------
# Der Import in einem frischen Prozess - mit nachgebauten Bibliotheken
# ------------------------------------------------------------------------------
def baue_attrappen_bibliotheken(verzeichnis, treiber_defekt):
    """
    Legt Ersatzmodule fuer RPi.GPIO, smbus2 und den Waveshare-Treiber an.

    Sie liegen im Suchpfad vor allem anderen und verdecken damit auf dem
    Raspberry Pi auch die echten Bibliotheken. Ein Unterprozess dieser Tests
    fasst also selbst dann keine Hardware an, wenn die Sperre aus ist - genau
    das muss ja geprueft werden.

    'treiber_defekt' entscheidet, ob der nachgebaute Treiber sich laden laesst
    oder beim Ausfuehren scheitert wie auf dem Geraet mit belegten Pins. Beide
    Faelle werden gebraucht: Ein Treiber, der ohnehin nie zustande kommt,
    koennte gar nicht zeigen, dass die Sperre ihn verhindert.
    """
    os.makedirs(os.path.join(verzeichnis, "RPi"))
    open(os.path.join(verzeichnis, "RPi", "__init__.py"), "w").close()
    open(os.path.join(verzeichnis, "RPi", "GPIO.py"), "w").close()

    with open(os.path.join(verzeichnis, "smbus2.py"), "w", encoding="utf-8") as datei:
        datei.write("class SMBus:\n    def __init__(self, nummer):\n        pass\n")

    os.makedirs(os.path.join(verzeichnis, "waveshare_epd"))
    open(os.path.join(verzeichnis, "waveshare_epd", "__init__.py"), "w").close()
    with open(os.path.join(verzeichnis, "waveshare_epd", "epd2in13_V3.py"),
              "w", encoding="utf-8") as datei:
        if treiber_defekt:
            datei.write("raise RuntimeError(\"'GPIO busy'\")\n")
        else:
            datei.write("class EPD:\n    pass\n")


def importiere_in_frischem_prozess(verzeichnis, sperre, treiber_defekt=False):
    baue_attrappen_bibliotheken(verzeichnis, treiber_defekt)

    umgebung = dict(os.environ)
    umgebung["PYTHONPATH"] = os.pathsep.join([verzeichnis, projektverzeichnis()])
    if sperre:
        umgebung["TUERSCHILD_OHNE_HARDWARE"] = "1"
    else:
        umgebung.pop("TUERSCHILD_OHNE_HARDWARE", None)

    skript = textwrap.dedent("""
        import json
        from tuerschild import hardware
        print(json.dumps({
            "sperre": hardware.OHNE_HARDWARE,
            "gpio": hardware.GPIO is not None,
            "i2c": hardware.i2c_bus is not None,
            "treiber": hardware.epd2in13_V3 is not None,
        }))
    """)
    ergebnis = subprocess.run([sys.executable, "-c", skript], env=umgebung,
                              capture_output=True, text=True, timeout=120)
    assert ergebnis.returncode == 0, (
        "Der Import von tuerschild.hardware ist abgestuerzt:\n" + ergebnis.stderr
    )
    return json.loads(ergebnis.stdout.strip().split("\n")[-1])


def test_sperre_verhindert_den_import_auch_wenn_bibliotheken_da_sind(tmp_path):
    """
    Der eigentliche Beweis: Nicht das Fehlen der Bibliotheken haelt die Tests
    von der Hardware fern, sondern die Sperre. Ohne diesen Nachweis waere der
    Test auf Rechnern ohne Raspberry-Hardware bloss zufaellig gruen.
    """
    ergebnis = importiere_in_frischem_prozess(str(tmp_path), sperre=True)

    assert ergebnis["sperre"] is True
    assert ergebnis["gpio"] is False
    assert ergebnis["i2c"] is False
    assert ergebnis["treiber"] is False


def test_ohne_sperre_werden_die_bibliotheken_geladen(tmp_path):
    """
    Gegenprobe zum vorigen Test: Zeigt, dass die nachgebauten Bibliotheken
    ueberhaupt auffindbar sind. Ohne sie waere der Nachweis oben wertlos - er
    wuerde auch dann gelingen, wenn die Sperre gar nichts bewirkt.

    Im Betrieb bleibt die Sperre selbstverstaendlich aus; raumanzeige.py setzt
    sie nirgends.
    """
    ergebnis = importiere_in_frischem_prozess(str(tmp_path), sperre=False)

    assert ergebnis["sperre"] is False
    assert ergebnis["gpio"] is True
    assert ergebnis["i2c"] is True
    assert ergebnis["treiber"] is True


def test_defekter_treiber_beendet_das_programm_nicht(tmp_path):
    """
    Der Fall "GPIO busy" aus dem Betrieb: Der Treiber ist vorhanden, sein
    Import scheitert aber. Frueher fing hier nur ImportError - das Programm
    waere mit einem Absturz stehengeblieben, statt ohne Display weiterzulaufen
    und wenigstens die Weboberflaeche anzubieten.
    """
    ergebnis = importiere_in_frischem_prozess(str(tmp_path), sperre=False,
                                              treiber_defekt=True)

    assert ergebnis["treiber"] is False, (
        "Der defekte Treiber wurde uebernommen, statt auf None zu fallen."
    )
