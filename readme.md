# **WebUntis E-Paper Türschild (Raumanzeige)**

Automatisches, wartungsfreies E-Paper-Türschild für Schulen. Synchronisiert sich selbstständig mit der WebUntis-API und zeigt den aktuellen Stundenplan, Vertretungen, Ausfälle sowie die Folge-Stunden ("Jetzt / Danach") an. Inklusive Touch-Support und blitzschnellem Admin-Webinterface.

Dieses Projekt wurde speziell für den Schulalltag entwickelt und läuft ressourcenschonend auf einem Raspberry Pi Zero 2 W.

*🤖 Hinweis: Dieses Projekt, der zugehörige Programmcode und die Dokumentation wurden mit Unterstützung von Künstlicher Intelligenz (KI) erstellt.*

## **✨ Funktionen**

* **Live-Synchronisation (Jetzt & Danach):** Holt den aktuellen Tagesplan für den konfigurierten Raum über die WebUntis-API. Das Display wird intelligent aufgeteilt und zeigt neben der laufenden Stunde auch direkt an, wer als Nächstes den Raum belegt.  
* **Vertretungs- und Ausfallautomatik:** Fällt eine Stunde aus, wird dies mit einem markanten, invertierten Block ("FÄLLT AUS") signalisiert. Gleiches gilt für Vertretungsstunden.  
* **Dynamischer Stundenplan:** Die genauen Unterrichts- und Pausenzeiten (inkl. 5-Minuten-Vorlauf) berechnet das Skript vollautomatisch anhand einer zentralen Konfigurationsdatei.  
* **Smarte Zeiterkennung:** Erkennt Wochenenden und unterrichtsfreie Tage (z. B. Feiertage/Ferien) und schaltet das Display in einen schonenden Ruhemodus mit zentrierter Großschrift.  
* **Offline-Resilienz:** Fängt WLAN-Verbindungsabbrüche sauber ab und versucht es nach einem festgelegten Intervall erneut.  
* **Kapazitiver Touch-Support:** Ein Tippen auf das Display erzwingt ein sofortiges Update. Ghost-Touches durch das E-Paper werden durch gezieltes I2C-Polling zuverlässig unterdrückt.  
* **Admin-Webinterface mit Caching:** Eine lokal im Schulnetzwerk erreichbare Webseite spiegelt das Display live wider. Sie lädt dank internem Zwischenspeicher (Cache) in Millisekunden und bietet einen "Demo-Modus" für Präsentationen.

## **🛠️ Hardware-Voraussetzungen**

* **Raspberry Pi Zero 2 W** (oder vergleichbares Modell)  
* **Waveshare e-Paper Display** (z. B. 2.13" kapazitiv Touch, V3)  
* **MicroSD-Karte** (mit Raspberry Pi OS Lite)

## **📦 Verwendete Projekte & Abhängigkeiten**

Dieses Projekt baut auf mehreren Open-Source-Bibliotheken auf. Um das Skript auszuführen, müssen folgende Abhängigkeiten installiert werden:

### **System-Pakete**

* python3-venv, git, i2c-tools  
* libopenjp2-7, libtiff5, libxcb1 (für die Bildverarbeitung)  
* fonts-dejavu (Wichtig für die Darstellung von Umlauten und dynamischen Schriftgrößen)

### **Python-Bibliotheken (via pip)**

* [**python-webuntis**](https://github.com/python-webuntis/python-webuntis) (webuntis): Die essenzielle Schnittstelle, um sich in WebUntis einzuloggen.  
* [**Pillow**](https://python-pillow.github.io/): Die Python Imaging Library zum Zeichnen der Layouts.  
* [**Flask**](https://flask.palletsprojects.com/) & [**Waitress**](https://docs.pylonsproject.org/projects/waitress/): Stellen den lokalen Webserver bereit.  
* [**Waveshare e-Paper**](https://github.com/waveshareteam/e-Paper): Die offiziellen Hardware-Treiber (SPI).  
* [**smbus2**](https://pypi.org/project/smbus2/): Für die direkte I2C-Kommunikation mit dem Touch-Controller.  
* **RPi.GPIO** & **spidev**: Für die Bus-Steuerung des Raspberry Pi.

## **🚀 Installation & Einrichtung**

Eine vollständige, Schritt-für-Schritt-Installationsanleitung findest du in der separaten Datei [**anleitung.md**](http://docs.google.com/anleitung.md).

## **⚙️ Konfiguration**

Das Skript benötigt eine config.json im Hauptverzeichnis. Eine Vorlage liegt als config.example.json bei. In dieser Datei kannst du auch die exakten Unterrichts- und Pausenzeiten deiner Schule definieren:

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
            {"start": "08:50", "end": "09:35", "name": "2. Std."},  
            {"start": "09:55", "end": "10:40", "name": "3. Std."}  
        \],  
        "BREAKS": \[  
            {"start": "09:35", "end": "09:50", "name": "1. Pause"}  
        \]  
    }  
}

## **📝 Lizenz & Nutzung**

Dieses Projekt kann frei für den schulischen und edukativen Bereich genutzt, kopiert und modifiziert werden.