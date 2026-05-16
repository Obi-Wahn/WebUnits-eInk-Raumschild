# **Installationsanleitung: WebUntis Türschild (Raspberry Pi Zero 2 W)**

*Hinweis: Dieses Projekt und der zugehörige Programmcode wurden mit Unterstützung von Künstlicher Intelligenz (KI) erstellt.*

Die vorliegende Anleitung dokumentiert die vollständige Einrichtung eines Raspberry Pi OS bis hin zum vollautomatischen, wartungsfreien E-Paper-Türschild.

**Features:**

* Live-Synchronisation der "Jetzt" und "Danach" Unterrichtsstunden  
* Automatische visuelle Markierung von Ausfällen und Vertretungen  
* Konfigurierbarer, dynamischer Stundenplan  
* Smarte Anzeige von Pausenzeiten und Feiertagen  
* Lokales Web-Interface zur Konfiguration (inkl. Demo-Modus)  
* Kapazitiver Touch-Support (via I2C-Polling für fehlerfreie Eingaben)  
* Offline-Resilienz bei WLAN-Abbrüchen

**Voraussetzungen:** \* Raspberry Pi Zero 2 W mit installiertem **Raspberry Pi OS Lite**.

* Konfigurierte WLAN-Verbindung und aktivierter SSH-Zugriff.  
* Standard-Benutzername: pi (Bei abweichendem Benutzernamen sind die Pfade entsprechend anzupassen).

## **Phase 1: Systemschnittstellen & Sprache aktivieren**

Das E-Paper-Display benötigt die SPI-Schnittstelle, der kapazitive Touch-Chip die I2C-Schnittstelle. Zudem ist die korrekte Konfiguration der deutschen Zeitzone erforderlich.

1. SSH-Verbindung zum Raspberry Pi herstellen.  
2. Konfigurationsmenü aufrufen:  
   sudo raspi-config

3. **Schnittstellen:** Zu 3 Interface Options navigieren und **I4 SPI** sowie **I5 I2C** aktivieren.  
4. **Zeitzone & Sprache:** Unter 5 Localisation Options folgende Einstellungen vornehmen:  
   * Unter L1 Locale den Eintrag de\_DE.UTF-8 aktivieren und als Standard festlegen.  
   * Unter L2 Timezone den Pfad Europe \-\> Berlin auswählen.  
5. Das Menü beenden und den erforderlichen **Neustart** (Reboot) bestätigen.

## **Phase 2: System-Updates & Abhängigkeiten**

Aktualisierung des Betriebssystems und Installation essenzieller System-Bibliotheken (für Bildverarbeitung, I2C-Kommunikation und Schriftarten für Umlaute).

1. Paketquellen aktualisieren:  
   sudo apt update && sudo apt upgrade \-y

2. Benötigte Systempakete installieren:  
   sudo apt install \-y python3-pip python3-venv git libopenjp2-7 libtiff5 libxcb1 i2c-tools fonts-dejavu

## **Phase 3: Projektordner & Waveshare-Treiber**

Anlegen der Verzeichnisstruktur und Herunterladen der offiziellen Hardware-Treiber für das Display.

1. Projektordner erstellen und in das Verzeichnis wechseln:  
   cd \~  
   mkdir webuntis-display  
   cd webuntis-display

2. Waveshare e-Paper Treiber über Git klonen:  
   git clone \[https://github.com/waveshareteam/e-Paper.git\](https://github.com/waveshareteam/e-Paper.git)

## **Phase 4: Python Virtuelle Umgebung (venv)**

Bereitstellung einer isolierten Umgebung für die Python-Pakete zur Vermeidung von Systemkonflikten.

1. Virtuelle Umgebung mit dem Namen webuntis erstellen:  
   python3 \-m venv webuntis

2. Umgebung aktivieren:  
   source webuntis/bin/activate

   *(Indikator: Der Eingabeaufforderung wird ein (webuntis) vorangestellt).*  
3. Erforderliche Python-Bibliotheken installieren:  
   pip install RPi.GPIO spidev Pillow webuntis flask waitress smbus2

4. Virtuelle Umgebung deaktivieren:  
   deactivate

## **Phase 5: Dateien anlegen**

Bereitstellung der Skripte im Projektverzeichnis /home/pi/webuntis-display. Folgende Dateien müssen hochgeladen oder über die Kommandozeile (z. B. via nano) erstellt werden:

1. **raumanzeige.py**: Das Hauptskript.  
2. **start.sh**: Das Start-Skript (muss mit chmod \+x start.sh ausführbar gemacht werden).  
3. **config.json**: Die Konfigurationsdatei.

Als Vorlage für die config.json dient folgender Codeblock. Hier müssen die schulspezifischen Zugangsdaten sowie die genauen Stunden- und Pausenzeiten eingetragen werden:

{  
    "UNTIS\_SERVER": "demo.webuntis.com",  
    "UNTIS\_SCHOOL": "muster\_schule",  
    "UNTIS\_USER": "benutzername",  
    "UNTIS\_PASS": "passwort",  
    "ROOM\_NAME": "Raum101",  
    "AUTO\_UPDATE\_SECONDS": 900,  
    "DISPLAY\_ACTIVE": true,  
    "TOUCH\_ACTIVE": true,  
    "SCHEDULE": {  
        "DAY\_START": "07:55",  
        "DAY\_END": "15:30",  
        "LESSONS": \[  
            {"start": "08:00", "end": "08:45", "name": "1. Std."},  
            {"start": "08:50", "end": "09:35", "name": "2. Std."}  
        \],  
        "BREAKS": \[  
            {"start": "09:35", "end": "09:50", "name": "1. Pause"}  
        \]  
    }  
}

## **Phase 6: Autostart-Service (systemd) einrichten**

Einrichtung eines systemd-Dienstes, damit das Skript beim Systemstart automatisch im Hintergrund ausgeführt und bei Fehlern neu gestartet wird.

1. Service-Datei anlegen:  
   sudo nano /etc/systemd/system/raumanzeige.service

2. Folgende Konfiguration einfügen (Pfade bei abweichendem Benutzernamen anpassen):  
   \[Unit\]  
   Description=WebUntis Raumanzeige Tuerschild  
   After=network-online.target  
   Wants=network-online.target

   \[Service\]  
   User=pi  
   Group=pi  
   WorkingDirectory=/home/pi/webuntis-display  
   ExecStart=/home/pi/webuntis-display/webuntis/bin/python3 /home/pi/webuntis-display/raumanzeige.py  
   Restart=always  
   RestartSec=10  
   KillSignal=SIGINT

   \[Install\]  
   WantedBy=multi-user.target

   *Speichern mit Strg+O, Enter bestätigen und den Editor mit Strg+X beenden.*  
3. Dienst aktivieren und starten:  
   sudo systemctl daemon-reload  
   sudo systemctl enable raumanzeige.service  
   sudo systemctl start raumanzeige.service

Nach erfolgreichem Abschluss dieser Schritte arbeitet das System vollautomatisch.

Das lokale Administrations-Panel ist im Schulnetzwerk unter folgender Adresse erreichbar:

http://\[IP-ADRESSE-DES-PI\]:5000