"""
Test fuer die Reihenfolge im Einstiegspunkt raumanzeige.py.

WARUM DIESER TEST SO UNGEWOEHNLICH AUSSIEHT:
Geprueft wird hier der Quelltext selbst, nicht das Verhalten. Das ist normal
kein guter Stil - hier aber der einzige gangbare Weg, und der Fehler, den er
abfaengt, ist real aufgetreten.

Beim Aufteilen des Programms in ein Paket rutschte logging.basicConfig() hinter
die Importe. Das Laden von tuerschild.hardware gibt aber bereits Warnungen aus,
wenn die Hardware-Bibliotheken fehlen. Die erste Log-Ausgabe eines Programms
legt die Voreinstellung fest; jedes spaetere basicConfig() bleibt wirkungslos.

Folge: Alle Zeitstempel verschwanden, und weil die Voreinstellung erst ab
WARNING protokolliert, fehlten saemtliche INFO-Meldungen - darunter die
Startausgabe mit der Netzwerkadresse.

Warum kein Test des laufenden Programms? Ein Unterprozess wuerde die echte
Hardware ansprechen: Er kennt die Attrappe aus conftest.py nicht und wuerde auf
dem Raspberry Pi das Display tatsaechlich loeschen. Und im Testlauf selbst ist
die Reihenfolge nicht mehr beobachtbar, weil pytest das Paket laengst geladen
hat.
"""
import os
import re


def einstiegspunkt_quelltext():
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "raumanzeige.py")
    with open(pfad, encoding="utf-8") as datei:
        return datei.read().split("\n")


def test_logging_wird_vor_dem_paket_konfiguriert():
    zeilen = einstiegspunkt_quelltext()

    logging_zeile = next(i for i, z in enumerate(zeilen)
                         if z.startswith("logging.basicConfig("))
    erster_paketimport = next(i for i, z in enumerate(zeilen)
                              if re.match(r"^(from|import) tuerschild", z))

    assert logging_zeile < erster_paketimport, (
        "logging.basicConfig() steht hinter dem Import des Pakets. "
        "Dadurch gehen Zeitstempel und alle INFO-Meldungen verloren."
    )


def test_logging_schreibt_nach_stdout_mit_zeitstempel():
    """Der systemd-Dienst und der Direktstart erwarten dieses Format."""
    quelltext = "\n".join(einstiegspunkt_quelltext())

    assert "%(asctime)s" in quelltext, "Zeitstempel fehlt im Log-Format"
    assert "StreamHandler(sys.stdout)" in quelltext, "Log geht nicht nach stdout"
    assert "level=logging.INFO" in quelltext, "INFO-Meldungen wuerden gefiltert"
