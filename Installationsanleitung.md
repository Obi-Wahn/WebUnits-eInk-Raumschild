# **Installationsanleitung: WebUntis Türschild (Raspberry Pi Zero 2 W)**

*Hinweis: Dieses Projekt und der zugehörige Programmcode wurden mit Unterstützung von Künstlicher Intelligenz (KI) erstellt.*

Die vorliegende Anleitung dokumentiert die vollständige Einrichtung eines Raspberry Pi OS bis hin zum vollautomatischen, HTTPS-abgesicherten E-Paper-Türschild.

**Voraussetzungen:** \* Raspberry Pi Zero 2 W mit installiertem **Raspberry Pi OS Lite** (64-bit empfohlen).

* Konfigurierte WLAN-Verbindung und aktivierter SSH-Zugriff.  
* Standard-Benutzername: pi (Bei abweichendem Benutzernamen sind die Pfade entsprechend anzupassen).

## **Phase 1: Systemschnittstellen & Sprache aktivieren**

1. SSH-Verbindung zum Raspberry Pi herstellen.  
2. Konfigurationsmenü aufrufen: sudo raspi-config  
3. **Schnittstellen:** Zu 3 Interface Options navigieren und **I4 SPI** sowie **I5 I2C** aktivieren.  
4. **Zeitzone & Sprache:** Unter 5 Localisation Options:  
   * L1 Locale: de\_DE.UTF-8 aktivieren und als Standard festlegen.  
   * L2 Timezone: Europe \-\> Berlin auswählen.  
5. Das Menü beenden und den **Neustart** bestätigen.

## **Phase 2: System-Updates & Abhängigkeiten**

1. Paketquellen aktualisieren:  
   sudo apt update && sudo apt upgrade \-y

2. Benötigte Systempakete installieren (inkl. Nginx für HTTPS und Schriftarten für Umlaute):  
   sudo apt install \-y python3-pip python3-venv git libopenjp2-7 libtiff5 libxcb1 i2c-tools fonts-dejavu nginx openssl

## **Phase 3: Projektordner & Hardware-Treiber**

1. Projektordner erstellen und wechseln:  
   cd \~  
   mkdir webuntis-display  
   cd webuntis-display

2. Waveshare e-Paper Treiber über Git klonen:  
   git clone \[https://github.com/waveshareteam/e-Paper.git\](https://github.com/waveshareteam/e-Paper.git)

## **Phase 4: Python Virtuelle Umgebung (venv)**

1. Virtuelle Umgebung erstellen und aktivieren:  
   python3 \-m venv webuntis  
   source webuntis/bin/activate

2. Erforderliche Python-Bibliotheken installieren:  
   pip install RPi.GPIO spidev Pillow webuntis flask waitress smbus2

3. Umgebung deaktivieren:  
   deactivate

## **Phase 5: Dateien anlegen**

Erstelle im Projektverzeichnis /home/pi/webuntis-display folgende Dateien (z. B. via nano):

1. **raumanzeige.py**: Das Python-Hauptskript.  
2. **config.json**: Konfiguration inkl. SCHEDULE-Block (siehe README).  
3. **start.sh**: Start-Skript. Anschließend ausführbar machen:  
   chmod \+x start.sh

## **Phase 6: HTTPS & Nginx Reverse Proxy einrichten**

Um das Admin-Interface und die WebUntis-Passwörter im Schulnetzwerk zu schützen, leiten wir den internen Server (Port 5000\) durch einen Nginx-Server mit SSL-Zertifikat.

1. **Selbstsigniertes Zertifikat erstellen** (10 Jahre Gültigkeit):  
   sudo openssl req \-x509 \-nodes \-days 3650 \-newkey rsa:2048 \\  
     \-keyout /etc/ssl/private/tuerschild.key \\  
     \-out /etc/ssl/certs/tuerschild.crt \\  
     \-subj "/C=DE/ST=Bundesland/L=Musterstadt/O=Musterschule/CN=tuerschild.local"

   *(Passe die Daten im \-subj Parameter optional an deine Schule an).*  
2. **Nginx konfigurieren**:  
   sudo nano /etc/nginx/sites-available/tuerschild

   Folgenden Inhalt einfügen:  
   server {  
       listen 443 ssl;  
       server\_name \_;

       ssl\_certificate /etc/ssl/certs/tuerschild.crt;  
       ssl\_certificate\_key /etc/ssl/private/tuerschild.key;

       location / {  
           proxy\_pass \[http://127.0.0.1:5000\](http://127.0.0.1:5000);  
           proxy\_set\_header Host $host;  
           proxy\_set\_header X-Real-IP $remote\_addr;  
           proxy\_set\_header X-Forwarded-For $proxy\_add\_x\_forwarded\_for;  
           proxy\_set\_header X-Forwarded-Proto $scheme;  
       }  
   }

   server {  
       listen 80;  
       server\_name \_;  
       return 301 https://$host$request\_uri;  
   }

3. **Nginx aktivieren und neu starten**:  
   sudo ln \-s /etc/nginx/sites-available/tuerschild /etc/nginx/sites-enabled/  
   sudo rm /etc/nginx/sites-enabled/default  
   sudo systemctl restart nginx

## **Phase 7: Autostart-Service (systemd) einrichten**

1. Service-Datei anlegen:  
   sudo nano /etc/systemd/system/raumanzeige.service

2. Folgende Konfiguration einfügen:  
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

3. Dienst aktivieren und starten:  
   sudo systemctl daemon-reload  
   sudo systemctl enable raumanzeige.service  
   sudo systemctl start raumanzeige.service

🎉 **Fertig\!** Das Türschild läuft nun vollautomatisch. Das Administrations-Panel ist sicher verschlüsselt erreichbar unter:

https://\[IP-ADRESSE-DES-PI\] *(Browser-Warnung bezüglich des selbstsignierten Zertifikats einfach ignorieren/akzeptieren).*
