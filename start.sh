#!/bin/bash

# Ins Projektverzeichnis wechseln (Pfade anpassen, falls abweichend vom User 'pi')
cd /home/pi/webuntis-display

# Virtuelle Umgebung aktivieren
source webuntis/bin/activate

# Das Python-Skript ausführen
python3 raumanzeige.py