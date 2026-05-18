# **Installationsanleitung: WebUntis E-Paper-Raumanzeige**

Diese Dokumentation beschreibt die vollständige Einrichtung der WebUntis-Raumanzeige auf einem Raspberry Pi. Die Architektur umfasst die Ansteuerung des E-Paper-Displays, die Synchronisation mit der WebUntis-API sowie die Bereitstellung eines lokalen, per HTTPS abgesicherten Administrations-Interfaces.

## **Systemvoraussetzungen**

* Hardware: Raspberry Pi Zero 2 W (oder vergleichbares Modell).  
* Betriebssystem: Raspberry Pi OS Lite (64-bit wird empfohlen).  
* Netzwerk: Konfigurierte WLAN-Verbindung und aktivierter SSH-Zugriff.  
* Benutzer: Die Anleitung geht vom Standard-Benutzer pi aus. Bei abweichenden Benutzernamen sind die absoluten Pfade entsprechend anzupassen.

## **1\. Systemkonfiguration**

Stellen Sie eine SSH-Verbindung zum Raspberry Pi her und öffnen Sie das Konfigurationsmenü:

sudo raspi-config

1. **Schnittstellen aktivieren:** Navigieren Sie zu 3 Interface Options und aktivieren Sie **I4 SPI** sowie **I5 I2C**.  
2. **Lokalisierung:** Navigieren Sie zu 5 Localisation Options.  
   * Setzen Sie unter L1 Locale den Wert de\_DE.UTF-8 als Standard.  
   * Konfigurieren Sie unter L2 Timezone die Zeitzone (Europe \-\> Berlin).  
3. Beenden Sie das Menü und bestätigen Sie den anschließenden Neustart.

## **2\. Paketquellen und Abhängigkeiten**

Aktualisieren Sie die Paketquellen und installieren Sie die benötigten Systempakete:

sudo apt update && sudo apt upgrade \-y  
sudo apt install \-y python3-pip python3-venv git libopenjp2-7 libtiff5 libxcb1 i2c-tools fonts-dejavu nginx openssl

## **3\. Projektverzeichnis und Treiber**

Erstellen Sie das Arbeitsverzeichnis und laden Sie die benötigten Hardware-Treiber für das Waveshare-Display herunter:

cd \~  
mkdir webuntis-display  
cd webuntis-display  
git clone \[https://github.com/waveshareteam/e-Paper.git\](https://github.com/waveshareteam/e-Paper.git)

## **4\. Python-Umgebung einrichten**

Um Konflikte mit systemweiten Paketen zu vermeiden, wird eine virtuelle Python-Umgebung (venv) verwendet:

python3 \-m venv webuntis  
source webuntis/bin/activate  
pip install RPi.GPIO spidev Pillow webuntis flask waitress smbus2  
deactivate

## **5\. Programmdateien und Konfiguration**

Erstellen Sie im Verzeichnis /home/pi/webuntis-display die folgenden Dateien:

1. **raumanzeige.py**: Fügen Sie den vollständigen Python-Code des Hauptprogramms ein.  
2. **config.json**: Erstellen Sie die Konfigurationsdatei. Nutzen Sie folgendes Schema und passen Sie die Parameter an Ihre Gegebenheiten an:

{  
    "UNTIS\_SERVER": "demo.webuntis.com",  
    "UNTIS\_SCHOOL": "muster\_schule",  
    "UNTIS\_USER": "benutzername",  
    "UNTIS\_PASS": "passwort",  
    "ADMIN\_USER": "admin",  
    "ADMIN\_PASS": "tuerschild",  
    "ROOM\_NAME": "Raum 101",  
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

*Wichtiger Hinweis zu den Zugangsdaten:* Die Parameter ADMIN\_USER und ADMIN\_PASS definieren den Zugang für das Web-Interface. Ändern Sie diese zwingend vor der Inbetriebnahme.

## **6\. Datenschutz und Sicherheit**

Um die sensiblen Zugangsdaten (WebUntis-Login und Admin-Passwort) vor unbefugtem Auslesen durch andere lokale Benutzer oder kompromittierte Prozesse zu schützen, müssen die Dateirechte der Konfiguration strikt limitiert werden.

Führen Sie folgenden Befehl aus, damit nur der Besitzer der Datei Lese- und Schreibrechte besitzt:

chmod 600 /home/pi/webuntis-display/config.json

*Versionskontrolle:* Sollten Sie den Code über ein öffentliches Repository (z. B. GitHub) verwalten, stellen Sie zwingend sicher, dass die Datei config.json in der .gitignore-Datei aufgeführt ist, um einen versehentlichen Upload von Zugangsdaten und schulbezogenen Informationen zu verhindern.

## **7\. Nginx Reverse Proxy und HTTPS**

Der in Python integrierte Webserver (Waitress) wird ausschließlich an den Localhost (127.0.0.1) gebunden. Nginx übernimmt die Rolle des Reverse Proxys und sichert die Verbindung nach außen über HTTPS ab.

1. **SSL-Zertifikat generieren:**  
   Erstellen Sie ein selbstsigniertes Zertifikat. Die Zertifikatsdetails (-subj) sind neutrale Platzhalter und können nach Ermessen angepasst werden.  
   sudo openssl req \-x509 \-nodes \-days 3650 \-newkey rsa:2048 \\  
     \-keyout /etc/ssl/private/tuerschild.key \\  
     \-out /etc/ssl/certs/tuerschild.crt \\  
     \-subj "/C=DE/ST=Bundesland/L=Musterstadt/O=Musterschule/CN=tuerschild.local"

2. **Nginx Konfiguration erstellen:**  
   sudo nano /etc/nginx/sites-available/tuerschild

   Fügen Sie folgenden Inhalt ein:  
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

3. **Konfiguration aktivieren:**  
   sudo ln \-s /etc/nginx/sites-available/tuerschild /etc/nginx/sites-enabled/  
   sudo rm /etc/nginx/sites-enabled/default  
   sudo systemctl restart nginx

## **8\. Systemdienst (Autostart) einrichten**

Damit das Programm bei einem Neustart des Raspberry Pi automatisch ausgeführt wird, wird ein systemd-Service angelegt.

1. **Service-Datei erstellen:**  
   sudo nano /etc/systemd/system/raumanzeige.service

2. **Konfiguration einfügen:**  
   \[Unit\]  
   Description=WebUntis Raumanzeige Service  
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

3. **Dienst aktivieren und starten:**  
   sudo systemctl daemon-reload  
   sudo systemctl enable raumanzeige.service  
   sudo systemctl start raumanzeige.service

Die Installation ist damit abgeschlossen. Das Administrations-Interface ist netzwerkintern unter der IP-Adresse des Raspberry Pi über HTTPS erreichbar (z. B. https://10.x.x.x). Browser-Warnungen bezüglich des selbstsignierten Zertifikats müssen für den Zugriff bestätigt werden.
