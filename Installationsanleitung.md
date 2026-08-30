# **Installationsanleitung: WebUntis E-Paper-Raumanzeige**

Diese Dokumentation beschreibt die vollständige Einrichtung der WebUntis-Raumanzeige auf einem Raspberry Pi. Die Architektur umfasst die Ansteuerung des E-Paper-Displays, die Synchronisation mit der WebUntis-API sowie die Bereitstellung eines lokalen, per HTTPS abgesicherten Administrations-Interfaces.

## **Systemvoraussetzungen**

* Hardware: Raspberry Pi Zero 2 W (oder vergleichbares Modell).  
* Betriebssystem: Raspberry Pi OS Lite (64-bit wird empfohlen).  
* Netzwerk: Konfigurierte WLAN-Verbindung und aktivierter SSH-Zugriff.  
* Benutzer: Die Anleitung geht vom Standard-Benutzer pi aus. Bei abweichenden Benutzernamen sind die absoluten Pfade entsprechend anzupassen.

## **1. Systemkonfiguration**

Stellen Sie eine SSH-Verbindung zum Raspberry Pi her und öffnen Sie das Konfigurationsmenü:

sudo raspi-config

1. **Schnittstellen aktivieren:** Navigieren Sie zu 3 Interface Options und aktivieren Sie **I4 SPI** sowie **I5 I2C**.  
2. **Lokalisierung:** Navigieren Sie zu 5 Localisation Options.  
   * Setzen Sie unter L1 Locale den Wert de_DE.UTF-8 als Standard.  
   * Konfigurieren Sie unter L2 Timezone die Zeitzone (Europe -> Berlin).  
3. Beenden Sie das Menü und bestätigen Sie den anschließenden Neustart.

## **2. Paketquellen und Abhängigkeiten**

Aktualisieren Sie die Paketquellen und installieren Sie die benötigten Systempakete:

sudo apt update && sudo apt upgrade -y  
sudo apt install -y python3-pip python3-venv git libopenjp2-7 libtiff-dev libxcb1 i2c-tools fonts-dejavu nginx openssl

## **3. Projektverzeichnis und Treiber**

Erstellen Sie das Arbeitsverzeichnis und laden Sie die benötigten Hardware-Treiber für das Waveshare-Display herunter:

cd ~  
mkdir webuntis-display  
cd webuntis-display  
git clone https://github.com/waveshareteam/e-Paper.git

## **4. Python-Umgebung einrichten**

Um Konflikte mit systemweiten Paketen zu vermeiden, wird eine virtuelle Python-Umgebung (venv) verwendet. Die benötigten Bibliotheken sind mit exakten Versionsnummern in der Datei `requirements.txt` hinterlegt. Dadurch entsteht bei jeder Neuinstallation dieselbe, getestete Umgebung, und ein späteres Update einer Bibliothek kann das Programm nicht unbemerkt brechen.

python3 -m venv webuntis  
source webuntis/bin/activate  
pip install -r requirements.txt  
deactivate

*Hinweis:* Legen Sie die Datei `requirements.txt` aus diesem Repository zuvor im Projektverzeichnis ab. Auf einem Raspberry Pi 5 ersetzen Sie darin `RPi.GPIO` durch das API-kompatible Paket `rpi-lgpio`; der Programmcode bleibt unverändert.

## **5. Programmdateien und Konfiguration**

Erstellen Sie im Verzeichnis /home/pi/webuntis-display die folgenden Dateien:

1. **raumanzeige.py**: Fügen Sie den vollständigen Python-Code des Hauptprogramms ein.  
2. **config.json**: Erstellen Sie die Konfigurationsdatei. Nutzen Sie folgendes Schema und passen Sie die Parameter an Ihre Gegebenheiten an:

{  
    "UNTIS_SERVER": "demo.webuntis.com",  
    "UNTIS_SCHOOL": "muster_schule",  
    "UNTIS_USER": "benutzername",  
    "UNTIS_PASS": "passwort",  
    "ADMIN_USER": "admin",  
    "ADMIN_PASS": "tuerschild",  
    "ROOM_NAME": "Raum 101",  
    "AUTO_UPDATE_SECONDS": 900,  
    "DISPLAY_ACTIVE": true,  
    "TOUCH_ACTIVE": true,  
    "SCHEDULE": {  
        "DAY_START": "07:55",  
        "DAY_END": "15:30",  
        "LESSONS": [  
            {"start": "08:00", "end": "08:45", "name": "1. Std."},  
            {"start": "08:50", "end": "09:35", "name": "2. Std."}  
        ],  
        "BREAKS": [  
            {"start": "09:35", "end": "09:50", "name": "1. Pause"}  
        ]  
    }  
}

*Wichtiger Hinweis zu den Zugangsdaten:* Die Parameter ADMIN_USER und ADMIN_PASS definieren den Zugang für das Web-Interface. Ändern Sie diese zwingend vor der Inbetriebnahme.

## **6. Datenschutz und Sicherheit**

Um die sensiblen Zugangsdaten (WebUntis-Login und Admin-Passwort) vor unbefugtem Auslesen durch andere lokale Benutzer oder kompromittierte Prozesse zu schützen, müssen die Dateirechte der Konfiguration strikt limitiert werden.

Führen Sie folgenden Befehl aus, damit nur der Besitzer der Datei Lese- und Schreibrechte besitzt:

chmod 600 /home/pi/webuntis-display/config.json

*Versionskontrolle:* Sollten Sie den Code über ein öffentliches Repository (z. B. GitHub) verwalten, stellen Sie zwingend sicher, dass die Datei config.json in der .gitignore-Datei aufgeführt ist, um einen versehentlichen Upload von Zugangsdaten und schulbezogenen Informationen zu verhindern.

## **7. Nginx Reverse Proxy und HTTPS**

Der in Python integrierte Webserver (Waitress) wird ausschließlich an den Localhost (127.0.0.1) gebunden. Nginx übernimmt die Rolle des Reverse Proxys und sichert die Verbindung nach außen über HTTPS ab.

1. **SSL-Zertifikat generieren:**  
   Erstellen Sie ein selbstsigniertes Zertifikat. Die Zertifikatsdetails (-subj) sind neutrale Platzhalter und können nach Ermessen angepasst werden.  
   sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
     -keyout /etc/ssl/private/tuerschild.key \
     -out /etc/ssl/certs/tuerschild.crt \
     -subj "/C=DE/ST=Bundesland/L=Musterstadt/O=Musterschule/CN=tuerschild.local"

2. **Nginx Konfiguration erstellen:**  
   sudo nano /etc/nginx/sites-available/tuerschild

   Fügen Sie folgenden Inhalt ein:  
   server {  
       listen 443 ssl;  
       server_name _;

       ssl_certificate /etc/ssl/certs/tuerschild.crt;  
       ssl_certificate_key /etc/ssl/private/tuerschild.key;

       location / {  
           proxy_pass http://127.0.0.1:5000;  
           proxy_set_header Host $host;  
           proxy_set_header X-Real-IP $remote_addr;  
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  
           proxy_set_header X-Forwarded-Proto $scheme;  
       }  
   }

   server {  
       listen 80;  
       server_name _;  
       return 301 https://$host$request_uri;  
   }

3. **Konfiguration aktivieren:**  
   sudo ln -s /etc/nginx/sites-available/tuerschild /etc/nginx/sites-enabled/  
   sudo rm /etc/nginx/sites-enabled/default  
   sudo systemctl restart nginx

## **8. Systemdienst (Autostart) einrichten**

Damit das Programm bei einem Neustart des Raspberry Pi automatisch ausgeführt wird, wird ein systemd-Service angelegt. Aus Sicherheitsgründen (Principle of Least Privilege) läuft dieser Dienst **nicht** als Root, sondern als normaler Benutzer pi.

1. **Service-Datei erstellen:**  
   sudo nano /etc/systemd/system/raumanzeige.service

2. **Konfiguration einfügen:**  
   [Unit]  
   Description=WebUntis Raumanzeige Service  
   After=network-online.target  
   Wants=network-online.target

   [Service]  
   User=pi  
   Group=pi  
   WorkingDirectory=/home/pi/webuntis-display  
   ExecStart=/home/pi/webuntis-display/webuntis/bin/python3 /home/pi/webuntis-display/raumanzeige.py  
   Restart=always  
   RestartSec=10  
   KillSignal=SIGINT

   [Install]  
   WantedBy=multi-user.target

3. **Dienst aktivieren und starten:**  
   sudo systemctl daemon-reload  
   sudo systemctl enable raumanzeige.service  
   sudo systemctl start raumanzeige.service

## **9. Rechteverwaltung für das Webinterface (Sudoers)**

Da der Webserver aus Sicherheitsgründen als unprivilegierter Benutzer (pi) läuft, kann er das System normalerweise nicht eigenständig über die Web-Buttons herunterfahren oder neustarten. Wir erteilen dem Nutzer pi daher gezielt eine "Ausnahmegenehmigung" für exakt diese beiden Befehle, ohne dass eine Passworteingabe (die das Skript blockieren würde) erforderlich ist.

1. **Sudoers-Datei sicher bearbeiten:**  
   sudo visudo /etc/sudoers.d/010_pi-nopasswd

2. **Folgende Zeile einfügen und speichern:**  
   *(Wenn Sie einen anderen Benutzernamen als pi verwenden, passen Sie das erste Wort entsprechend an)*  
   pi ALL=(ALL) NOPASSWD: /sbin/reboot, /sbin/poweroff

Die Installation ist damit abgeschlossen. Das Administrations-Interface ist netzwerkintern unter der IP-Adresse des Raspberry Pi über HTTPS erreichbar (z. B. https://10.x.x.x). Browser-Warnungen bezüglich des selbstsignierten Zertifikats müssen für den Zugriff bestätigt werden.
