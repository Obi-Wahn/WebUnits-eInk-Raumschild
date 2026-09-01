#!/bin/bash

# ==============================================================================
# Aktualisierungs-Skript für das WebUntis E-Paper Türschild
# ==============================================================================
# Holt den neuen Stand, spielt geänderte Abhängigkeiten ein, lässt die Tests
# laufen und startet den Dienst neu - in dieser Reihenfolge.
#
# WARUM DIE REIHENFOLGE WICHTIG IST:
# Der Neustart steht am Ende und findet nur statt, wenn die Tests durchlaufen.
# Ein Türschild, das nach einer Aktualisierung im Flur stehenbleibt, fällt
# niemandem auf - es zeigt ja weiter ein Bild, nur eben ein veraltetes. Lieber
# bricht dieses Skript vorher ab und sagt, was nicht stimmt.
#
# Aufruf (im Projektverzeichnis):   ./update.sh
# Ohne Tests (nicht empfohlen):     ./update.sh --ohne-tests
# Ohne Neustart des Dienstes:       ./update.sh --ohne-neustart

set -u

# ------------------------------------------------------------------------------
# ACHTUNG, DIESE GESCHWEIFTE KLAMMER IST KEIN SCHMUCK:
# Das Skript aktualisiert sich selbst mit - 'git pull' schreibt update.sh neu,
# waehrend die Bash sie noch liest. Die Bash liest ein Skript naemlich nicht auf
# einmal ein, sondern haeppchenweise und merkt sich dabei die Leseposition.
# Aendert sich die Datei zwischendurch in ihrer Laenge, liest sie danach mitten
# in einer Zeile weiter und fuehrt Unsinn aus.
# Der Block { ... } am Ende der Datei zwingt die Bash, alles vorher einzulesen
# und zu zerlegen. Danach ist die Datei auf der Platte gleichgueltig.
# ------------------------------------------------------------------------------
{

DIENST="raumanzeige.service"
VENV="webuntis"

TESTS_AUSFUEHREN=1
NEUSTART_AUSFUEHREN=1

for argument in "$@"; do
    case "$argument" in
        --ohne-tests)    TESTS_AUSFUEHREN=0 ;;
        --ohne-neustart) NEUSTART_AUSFUEHREN=0 ;;
        *)
            echo "[FEHLER] Unbekannte Option: $argument"
            echo "         Erlaubt sind --ohne-tests und --ohne-neustart."
            exit 1
            ;;
    esac
done

# Ins Projektverzeichnis wechseln. Es wird aus dem Ort dieses Skripts
# abgeleitet - so funktioniert der Aufruf auch aus einem anderen Verzeichnis
# heraus und ohne fest eingetragenen Pfad.
PROJEKT_VERZEICHNIS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJEKT_VERZEICHNIS" || exit 1
echo "[1/5] Projektverzeichnis: $PROJEKT_VERZEICHNIS"

if [ ! -d ".git" ]; then
    echo "[FEHLER] Das ist keine Git-Arbeitskopie. Wurde das Projekt als ZIP"
    echo "         entpackt statt mit 'git clone' geholt? Dann bitte die"
    echo "         Installationsanleitung, Schritt 3, nachvollziehen."
    exit 1
fi

# Eigene, nicht eingecheckte Änderungen würden von 'git pull' angefasst.
# Die config.json ist davon nicht betroffen: Sie steht in der .gitignore und
# taucht hier gar nicht erst auf.
if [ -n "$(git status --porcelain)" ]; then
    echo "[FEHLER] Im Projektverzeichnis liegen ungespeicherte Änderungen:"
    git status --short
    echo
    echo "         Diese Aktualisierung würde sie überschreiben. Bitte erst"
    echo "         sichern oder mit 'git checkout -- <datei>' verwerfen."
    exit 1
fi

STAND_VORHER="$(git rev-parse HEAD)"

echo "[2/5] Neuen Stand holen..."
# --ff-only: Es wird nur weitergespult, niemals zusammengeführt. Ein
# überraschender Merge-Commit auf dem Gerät wäre schwer zu durchschauen.
if ! git pull --ff-only; then
    echo
    echo "[FEHLER] Der neue Stand ließ sich nicht einspielen."
    echo "         Meist liegt das an eigenen Commits auf dem Gerät."
    exit 1
fi

STAND_NACHHER="$(git rev-parse HEAD)"

if [ "$STAND_VORHER" = "$STAND_NACHHER" ]; then
    echo
    echo "Bereits auf dem neuesten Stand. Es gibt nichts zu tun."
    exit 0
fi

if [ ! -f "$VENV/bin/activate" ]; then
    echo "[FEHLER] Virtuelle Python-Umgebung '$VENV' nicht gefunden."
    exit 1
fi
source "$VENV/bin/activate"

echo "[3/5] Abhängigkeiten prüfen..."
# Nur nachinstallieren, wenn sich wirklich etwas geändert hat. Ein pip-Lauf
# dauert auf einem Pi Zero mehrere Minuten und lohnt sich sonst nicht.
GEAENDERT="$(git diff --name-only "$STAND_VORHER" "$STAND_NACHHER")"
if echo "$GEAENDERT" | grep -qx "requirements.txt"; then
    echo "      requirements.txt hat sich geändert - installiere nach."
    if ! pip install -r requirements.txt; then
        echo "[FEHLER] Die Installation der Abhängigkeiten ist fehlgeschlagen."
        exit 1
    fi
else
    echo "      Unverändert, nichts zu tun."
fi

if echo "$GEAENDERT" | grep -qx "requirements-dev.txt"; then
    if [ "$TESTS_AUSFUEHREN" -eq 1 ] && command -v pytest > /dev/null; then
        echo "      requirements-dev.txt hat sich geändert - installiere nach."
        pip install -r requirements-dev.txt
    fi
fi

echo "[4/5] Tests..."
if [ "$TESTS_AUSFUEHREN" -eq 0 ]; then
    echo "      Übersprungen (--ohne-tests)."
elif ! command -v pytest > /dev/null; then
    echo "      pytest ist nicht installiert - übersprungen."
    echo "      Nachrüsten mit: pip install -r requirements-dev.txt"
else
    # Die Tests fassen die Hardware nicht an (siehe tests/conftest.py), laufen
    # also auch bei aktivem Dienst gefahrlos.
    if ! pytest -q; then
        echo
        echo "[FEHLER] Die Tests sind fehlgeschlagen. Der Dienst wurde NICHT"
        echo "         neu gestartet und läuft weiter mit dem alten Stand im"
        echo "         Speicher. Zum Zurückrollen:"
        echo "           git checkout $STAND_VORHER"
        exit 1
    fi
fi

echo "[5/5] Dienst..."
if [ "$NEUSTART_AUSFUEHREN" -eq 0 ]; then
    echo "      Übersprungen (--ohne-neustart)."
elif ! command -v systemctl > /dev/null; then
    echo "      Auf diesem System gibt es kein systemd - übersprungen."
elif ! systemctl is-active --quiet "$DIENST" 2> /dev/null; then
    # Im Testbetrieb wird das Programm oft von Hand gestartet. Dann gibt es
    # keinen Dienst, den dieses Skript neu starten könnte - und der laufende
    # Prozess arbeitet weiter mit dem alten Programmstand im Speicher.
    echo "      Der Dienst $DIENST läuft nicht."
    echo "      Wird das Programm von Hand gestartet, muss es zum Übernehmen"
    echo "      des neuen Standes beendet (Strg+C) und neu gestartet werden."
else
    echo "      Starte $DIENST neu..."
    if ! sudo systemctl restart "$DIENST"; then
        echo "[FEHLER] Der Neustart des Dienstes ist fehlgeschlagen."
        exit 1
    fi
    sleep 3
    if systemctl is-active --quiet "$DIENST"; then
        echo "      Läuft."
    else
        echo "[FEHLER] Der Dienst läuft nach dem Neustart nicht. Protokoll:"
        sudo journalctl -u "$DIENST" -n 20 --no-pager
        exit 1
    fi
fi

echo
echo "Fertig. Neuer Stand:"
git --no-pager log -1 --format='  %h  %s'

}
