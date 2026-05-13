# **WebUntis E-Paper Türschild (Raumanzeige)**

Automatisches, wartungsfreies E-Paper-Türschild für Schulen. Synchronisiert sich selbstständig mit der WebUntis-API und zeigt den aktuellen Stundenplan, Pausenzeiten sowie Raumbelegungen an. Inklusive Touch-Support und Admin-Webinterface.

Dieses Projekt wurde speziell für den Schulalltag entwickelt und läuft ressourcenschonend auf einem Raspberry Pi Zero 2 W.

*🤖 Hinweis: Dieses Projekt, der zugehörige Programmcode und die Dokumentation wurden mit Unterstützung von Künstlicher Intelligenz (KI) erstellt.*

## **✨ Funktionen**

* **Live-Synchronisation:** Holt den aktuellen Tagesplan für den konfigurierten Raum über die WebUntis-API.  
* **Smarte Zeiterkennung:** Erkennt automatisch Doppelstunden (5-Minuten-Vorlauf), Pausenzeiten, Wochenenden und unterrichtsfreie Tage (z. B. Feiertage/Ferien).  
* **Offline-Resilienz:** Fängt WLAN-Verbindungsabbrüche sauber ab und versucht es nach einem festgelegten Intervall erneut, anstatt abzustürzen.  
* **Kapazitiver Touch-Support:** Ein Tippen auf das Display erzwingt ein sofortiges Update. Ghost-Touches durch elektromagnetische Felder des E-Papers werden durch gezieltes I2C-Polling (Speicherleerung) zuverlässig unterdrückt.  
* **Admin-Webinterface:** Eine lokal gehostete Webseite (im Schulnetzwerk erreichbar) ermöglicht das Ändern des angezeigten Raumes, das Anpassen der Update-Intervalle sowie das Deaktivieren von Display oder Touch-Funktion.

## **🛠️ Hardware-Voraussetzungen**

* **Raspberry Pi Zero 2 W** (oder vergleichbares Modell)  
* **Waveshare e-Paper Display** (z. B. 2.13" kapazitiv Touch, V3)  
* **MicroSD-Karte** (mit Raspberry Pi OS Lite)

## **📦 Verwendete Projekte & Abhängigkeiten**

Dieses Projekt baut auf mehreren Open-Source-Bibliotheken auf. Um das Skript auszuführen, müssen folgende Abhängigkeiten installiert werden:

### **System-Pakete**

* python3-venv, git, i2c-tools  
* libopenjp2-7, libtiff5, libxcb1 (für die Bildverarbeitung)

### **Python-Bibliotheken (via pip)**

* [**python-webuntis**](https://github.com/python-webuntis/python-webuntis) (webuntis): Die essenzielle Schnittstelle, um sich in WebUntis einzuloggen und die Stundenpläne abzurufen.  
* [**Pillow**](https://python-pillow.github.io/): Die Python Imaging Library. Wird genutzt, um die Layouts (Text, Linien) in den Arbeitsspeicher zu zeichnen, bevor sie an das Display gesendet werden.  
* [**Flask**](https://flask.palletsprojects.com/) & [**Waitress**](https://docs.pylonsproject.org/projects/waitress/): Stellen den lokalen Webserver für das Admin-Dashboard im Netzwerk bereit.  
* [**Waveshare e-Paper**](https://github.com/waveshareteam/e-Paper): Die offiziellen Hardware-Treiber zur Ansteuerung des Displays über die SPI-Schnittstelle.  
* [**smbus2**](https://pypi.org/project/smbus2/): Ermöglicht die direkte I2C-Kommunikation mit dem GT1151 Touch-Controller, um Hardware-Interrupts kontrolliert zu löschen.  
* **RPi.GPIO** & **spidev**: Für die generelle Pin- und Bus-Steuerung des Raspberry Pi.

## **🚀 Installation & Einrichtung**

Eine vollständige, Schritt-für-Schritt-Installationsanleitung (vom flashen des Betriebssystems bis zur Einrichtung des Autostart-Dienstes) findest du in der separaten Datei [**anleitung.md**](http://docs.google.com/anleitung.md).

## **⚙️ Konfiguration**

Das Skript benötigt eine config.json im Hauptverzeichnis. Eine Vorlage liegt als config.example.json bei.

Benenne diese einfach um und trage die Zugangsdaten deiner Schule ein:

{  
    "UNTIS\_SERVER": "demo.webuntis.com",  
    "UNTIS\_SCHOOL": "muster\_schule",  
    "UNTIS\_USER": "benutzername",  
    "UNTIS\_PASS": "passwort",  
    "ROOM\_NAME": "Raum101",  
    "AUTO\_UPDATE\_SECONDS": 900,  
    "DISPLAY\_ACTIVE": true,  
    "TOUCH\_ACTIVE": true  
}

## **📝 Lizenz & Nutzung**

Dieses Projekt kann frei für den schulischen und edukativen Bereich genutzt, kopiert und modifiziert werden.