#!/bin/bash

# ==============================================================================
# Start-Skript für das WebUntis E-Paper Türschild
# ==============================================================================

# Ins Projektverzeichnis wechseln (Pfade anpassen, falls abweichend vom User 'pi')
PROJECT_DIR="/home/pi/webuntis-display"

if [ -d "$PROJECT_DIR" ]; then
    cd "$PROJECT_DIR" || exit 1
else
    echo "[FEHLER] Projektverzeichnis $PROJECT_DIR existiert nicht."
    exit 1
fi

# Vorab-Prüfungen (Pre-Flight Checks)
if [ ! -f "config.json" ]; then
    echo "[FEHLER] config.json fehlt! Bitte config.example.json kopieren und anpassen."
    exit 1
fi

if [ ! -f "webuntis/bin/activate" ]; then
    echo "[FEHLER] Virtuelle Python-Umgebung 'webuntis' nicht gefunden!"
    exit 1
fi

# Virtuelle Umgebung aktivieren
source webuntis/bin/activate

# Das Python-Skript ausführen (exec leitet Beenden-Signale sauber an Python weiter)
exec python3 raumanzeige.py
