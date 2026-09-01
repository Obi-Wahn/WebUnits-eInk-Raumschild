"""
Tests fuer update.sh.

Ein Shell-Skript laesst sich hier nicht sinnvoll ausfuehren - es wuerde am
Testrechner git pull, pip und systemctl aufrufen. Geprueft werden deshalb die
wenigen Eigenschaften, die still kaputtgehen und dann teuer sind:

  * Der Name des Dienstes muss zu dem in der Installationsanleitung passen.
    Stimmt er nicht, meldet das Skript unauffaellig "Der Dienst laeuft nicht",
    und das Tuerschild arbeitet nach der Aktualisierung mit dem alten Stand
    weiter - ohne dass jemand etwas bemerkt.
  * Der Schutz gegen die Selbstaktualisierung muss stehen bleiben (siehe unten).
"""
import os
import re

import pytest


def projektverzeichnis():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def skript():
    with open(os.path.join(projektverzeichnis(), "update.sh"), encoding="utf-8") as datei:
        return datei.read()


def test_das_skript_existiert_und_ist_ausfuehrbar():
    pfad = os.path.join(projektverzeichnis(), "update.sh")
    assert os.path.isfile(pfad)
    assert os.access(pfad, os.X_OK), "Ohne Ausführungsrecht scheitert ./update.sh"


def test_der_dienstname_passt_zur_installationsanleitung(skript):
    treffer = re.search(r'^DIENST="([^"]+)"', skript, re.MULTILINE)
    assert treffer, "Der Dienstname steht nicht mehr in einer eigenen Variable"
    dienst = treffer.group(1)

    with open(os.path.join(projektverzeichnis(), "Installationsanleitung.md"),
              encoding="utf-8") as datei:
        anleitung = datei.read()

    assert dienst in anleitung, (
        f"update.sh startet '{dienst}' neu, die Installationsanleitung legt "
        "aber einen anders benannten Dienst an."
    )


def test_das_venv_verzeichnis_passt_zur_installationsanleitung(skript):
    treffer = re.search(r'^VENV="([^"]+)"', skript, re.MULTILINE)
    assert treffer
    with open(os.path.join(projektverzeichnis(), "start.sh"), encoding="utf-8") as datei:
        assert treffer.group(1) in datei.read()


def test_das_skript_ist_gegen_selbstaktualisierung_geschuetzt(skript):
    """
    update.sh aktualisiert sich selbst mit: 'git pull' schreibt die Datei neu,
    waehrend die Bash sie noch liest. Weil die Bash haeppchenweise liest und
    sich dabei ihre Leseposition merkt, laeuft sie danach mitten in einer Zeile
    weiter - und fuehrt Unsinn aus.

    Der Block { ... } um das gesamte Skript zwingt sie, alles vorher einzulesen.
    Dieser Test haelt fest, dass die Klammer nicht als vermeintlich unnoetig
    entfernt wird: Der Fehler taucht erst auf, wenn sich das Skript selbst
    aendert, und sieht dann nach allem Moeglichen aus, nur nicht nach seiner
    Ursache.
    """
    zeilen = [z for z in skript.split("\n")
              if z.strip() and not z.strip().startswith("#")]
    # Erste echte Anweisungen: der Shebang faellt oben schon als Kommentar weg
    assert "{" in zeilen[:3], (
        "Die schützende geschweifte Klammer fehlt am Anfang von update.sh"
    )
    assert zeilen[-1].strip() == "}", "Die schließende Klammer fehlt am Ende"


def test_tests_laufen_vor_dem_neustart(skript):
    """
    Die Reihenfolge ist der Sinn des Skripts: Erst pruefen, dann neu starten.
    Andersherum stuende ein defekter Stand im Flur, bevor jemand es merkt.
    """
    assert skript.index("pytest -q") < skript.index("systemctl restart")
