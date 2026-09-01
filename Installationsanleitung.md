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

Laden Sie zunächst das Projekt selbst herunter. Über `git clone` erhalten Sie alle Programmdateien auf einmal und können spätere Aktualisierungen bequem mit `git pull` einspielen:

cd ~  
git clone https://github.com/Obi-Wahn/WebUnits-eInk-Raumschild.git webuntis-display  
cd webuntis-display

Laden Sie anschließend die Hardware-Treiber für das Waveshare-Display herunter. Diese liegen in einem eigenen Repository des Herstellers und sind bewusst nicht Teil dieses Projekts:

git clone https://github.com/waveshareteam/e-Paper.git  
cd e-Paper  
git checkout a794fbc39656b0f93938d1ffb3fdc77eaed9e9fc  
cd ..

*Warum ein fester Commit?* Der Befehl `git checkout` legt die Treiber auf einen exakt geprüften Stand fest — aus demselben Grund, aus dem auch die Python-Pakete auf feste Versionen gepinnt sind. Der Hersteller ändert dieses Repository laufend; eine neuere Fassung kann andere Abhängigkeiten voraussetzen und das Display ohne erkennbare Fehlermeldung dunkel lassen. Der Download umfasst rund 90 MB und dauert auf einem Pi Zero 2 W einige Minuten.

## **4. Python-Umgebung einrichten**

Um Konflikte mit systemweiten Paketen zu vermeiden, wird eine virtuelle Python-Umgebung (venv) verwendet. Die benötigten Bibliotheken sind mit exakten Versionsnummern in der Datei `requirements.txt` hinterlegt. Dadurch entsteht bei jeder Neuinstallation dieselbe, getestete Umgebung, und ein späteres Update einer Bibliothek kann das Programm nicht unbemerkt brechen.

python3 -m venv webuntis  
source webuntis/bin/activate  
pip install -r requirements.txt  
deactivate

Die Datei `requirements.txt` liegt bereits im Projektverzeichnis, da sie mit dem `git clone` aus Schritt 3 heruntergeladen wurde. Auf Raspberry Pi OS muss dabei nichts kompiliert werden: Die vorkonfigurierte Paketquelle „piwheels" liefert fertig gebaute ARM-Pakete.

Für die Testsuite werden zusätzlich die Werkzeuge aus `requirements-dev.txt` benötigt. Sie sind für den reinen Betrieb nicht erforderlich, aber empfehlenswert: Das Aktualisierungs-Skript aus Schritt 10 lässt die Tests vor jedem Neustart des Dienstes laufen und überspringt sie, wenn `pytest` fehlt.

source webuntis/bin/activate  
pip install -r requirements-dev.txt  
deactivate

*Hinweis:* Auf einem Raspberry Pi 5 ersetzen Sie in der `requirements.txt` das Paket `RPi.GPIO` durch das API-kompatible `rpi-lgpio`; der Programmcode bleibt unverändert. Installieren Sie niemals beide gleichzeitig — sie stellen dasselbe Modul bereit und überschreiben sich gegenseitig.

## **5. Konfiguration**

Das Hauptprogramm `raumanzeige.py` wurde bereits mit dem `git clone` aus Schritt 3 heruntergeladen. Zu erstellen bleibt nur die Konfigurationsdatei — sie enthält Ihre Zugangsdaten und ist deshalb bewusst nicht Teil des Repositories.

Legen Sie sie auf Basis der mitgelieferten Vorlage an und passen Sie die Parameter an Ihre Gegebenheiten an:

cp config.example.json config.json  
nano config.json

Die Vorlage enthält folgende Parameter (hier zur besseren Übersicht auf zwei Stunden und eine Pause gekürzt — die vollständige Datei bringt einen kompletten Schultag mit):

{  
    "UNTIS_SERVER": "demo.webuntis.com",  
    "UNTIS_SCHOOL": "demo_schule",  
    "UNTIS_USER": "webuntis_benutzername",  
    "UNTIS_PASS": "webuntis_passwort",  
    "ADMIN_USER": "admin",  
    "ADMIN_PASS": "passwort",  
    "ROOM_NAME": "Raum101",  
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

Die Parameter WEB_HOST und WEB_PUBLIC_URL steuern, worauf der Webserver lauscht und welche Adresse beim Start ausgegeben wird. Die Voreinstellungen passen zu der in Schritt 7 beschriebenen Einrichtung und müssen dort nicht angepasst werden; Abweichungen sind ebenfalls in Schritt 7 beschrieben.

*Wichtiger Hinweis zu den Zugangsdaten:* Die Parameter ADMIN_USER und ADMIN_PASS definieren den Zugang für das Web-Interface. Ändern Sie diese zwingend vor der Inbetriebnahme.

*Die Zeiten müssen Sie nicht abtippen:* WebUntis kennt den Zeitraster Ihrer Schule. Nachdem die Zugangsdaten oben eingetragen sind, liest ihn das beiliegende Hilfsmittel aus und schlägt einen fertigen SCHEDULE-Block vor:

source webuntis/bin/activate  
python3 stundenraster_auslesen.py  
deactivate

Es speichert nichts, sondern gibt den Block zur Ansicht aus. Die Namen der Pausen kennt WebUntis nicht — dort steht überall "Pause"; daraus lässt sich von Hand "1. Pause" oder "Mittagspause" machen. Ist der Raster an Ihrer Schule nicht gepflegt, sagt das Skript das, und die Zeiten bleiben von Hand einzutragen.

*Wichtiger Hinweis zu den Uhrzeiten:* Alle Zeiten unter SCHEDULE müssen zweistellig geschrieben werden — also "08:00" und nicht "8:00". WebUntis liefert die Startzeiten immer zweistellig, und das Programm vergleicht sie als Zeichenketten; eine einstellige Angabe trifft deshalb auf nichts. Das Display zeigt dann alles Übrige normal an, nur der Name der Stunde bleibt leer — ein Fehler, der ohne Hinweis lange unentdeckt bleibt.

Das Programm prüft die Datei bei jedem Einlesen und schreibt Beanstandungen ins Protokoll (siehe Schritt 8, `journalctl -u raumanzeige`), zum Beispiel:

    WARNING - config.json: SCHEDULE: LESSONS, Eintrag 3 (start): '9:55' muss
    zweistellig geschrieben werden ('09:55') - sonst bleibt der Stundenname leer.

Abgelehnt wird dabei nichts; das Türschild läuft auch mit einer fehlerhaften Konfiguration weiter und zeigt an, was es anzeigen kann.

## **6. Datenschutz und Sicherheit**

Um die sensiblen Zugangsdaten (WebUntis-Login und Admin-Passwort) vor unbefugtem Auslesen durch andere lokale Benutzer oder kompromittierte Prozesse zu schützen, müssen die Dateirechte der Konfiguration strikt limitiert werden.

Führen Sie folgenden Befehl aus, damit nur der Besitzer der Datei Lese- und Schreibrechte besitzt:

chmod 600 /home/pi/webuntis-display/config.json

*Versionskontrolle:* Die mitgelieferte `.gitignore` schließt `config.json` bereits vom Upload aus. Sollten Sie den Code in einem eigenen öffentlichen Repository verwalten, prüfen Sie diesen Ausschluss vor der ersten Veröffentlichung — er verhindert, dass Zugangsdaten und schulbezogene Informationen versehentlich hochgeladen werden.

## **7. Nginx Reverse Proxy und HTTPS**

Der in Python integrierte Webserver (Waitress) wird ausschließlich an den Localhost (127.0.0.1) gebunden. Nginx übernimmt die Rolle des Reverse Proxys und sichert die Verbindung nach außen über HTTPS ab.

Beim Programmstart wird die Adresse, unter der das Interface im Netz erreichbar ist, als vollständige URL ins Log geschrieben. In den meisten Terminals lässt sie sich per Strg+Klick direkt öffnen. Zugrunde gelegt wird dabei die hier beschriebene Einrichtung, also HTTPS auf Port 443. Weicht Ihr Aufbau davon ab (eigener Hostname, anderer Port, nur HTTP), tragen Sie die gewünschte Adresse in der `config.json` unter `"WEB_PUBLIC_URL"` ein, zum Beispiel `"WEB_PUBLIC_URL": "https://tuerschild.local"`. Dieser Wert wird dann unverändert ausgegeben.

*Betrieb ohne Reverse Proxy:* Wenn Sie diesen Schritt überspringen, ist das Web-Interface ausschließlich auf dem Raspberry Pi selbst erreichbar — nicht von anderen Rechnern im Netz. Soll es ohne Nginx dennoch im lokalen Netz erreichbar sein, setzen Sie in der `config.json` den Wert `"WEB_HOST": "0.0.0.0"`. Das Interface ist dann unter `http://<IP-des-Pi>:5000` erreichbar; auch diese Adresse wird beim Start ausgegeben.

**Bedenken Sie dabei:** Ohne Nginx entfällt die Verschlüsselung. Die HTTP Basic Authentication überträgt Benutzername und Passwort dann bei jedem Aufruf lediglich Base64-kodiert, also praktisch im Klartext — jeder im selben Netz kann sie mitlesen. Da sich über das Interface auch Neustart und Herunterfahren auslösen lassen, ist diese Variante nur für abgeschottete Netze vertretbar. In einem Schul-WLAN ist der Reverse Proxy aus Schritt 7 die richtige Wahl.

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

## **10. Aktualisierungen einspielen**

Spätere Programmstände werden mit dem beiliegenden Skript eingespielt:

cd ~/webuntis-display  
./update.sh

Es holt den neuen Stand, installiert geänderte Abhängigkeiten nach, lässt die Testsuite laufen und startet den Dienst raumanzeige.service erst dann neu. Schlagen die Tests fehl, unterbleibt der Neustart, und das Skript nennt den Commit zum Zurückrollen — das Türschild läuft in diesem Fall unverändert weiter.

Eigene, noch nicht eingecheckte Änderungen im Projektverzeichnis führen zum Abbruch, bevor etwas überschrieben wird. Die config.json mit den Zugangsdaten ist davon nicht betroffen, sie ist von der Versionskontrolle ausgenommen.

Die Installation ist damit abgeschlossen. Das Administrations-Interface ist netzwerkintern unter der IP-Adresse des Raspberry Pi über HTTPS erreichbar (z. B. https://10.x.x.x). Browser-Warnungen bezüglich des selbstsignierten Zertifikats müssen für den Zugriff bestätigt werden.
