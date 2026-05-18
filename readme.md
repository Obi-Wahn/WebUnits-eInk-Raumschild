# **WebUntis E-Paper-Raumanzeige**

Dieses Projekt stellt ein automatisiertes, digitales Türschild für den Einsatz im schulischen Umfeld bereit. Das System synchronisiert sich selbstständig mit der WebUntis-API und visualisiert den aktuellen sowie den folgenden Unterricht auf einem energieeffizienten E-Paper-Display.

*Hinweis: Dieses Projekt und die zugehörige Dokumentation wurden mit Unterstützung von KI-Modellen entwickelt und strukturiert.*

## **✨ Funktionsumfang**

* **Automatisierte Synchronisation:** Abruf der aktuellen Plandaten über die WebUntis-API. Das Display zeigt übersichtlich die aktuell laufende Stunde ("JETZT") sowie die darauf folgende Belegung ("DANACH") an.  
* **Ausfall- und Vertretungserkennung:** Planänderungen wie Ausfälle oder Vertretungen werden durch spezifische WebUntis-Statuscodes erkannt und visuell hervorgehoben (z. B. durch invertierte Darstellung).  
* **Ressourcenschonender Ruhemodus:** Außerhalb der regulären Unterrichtszeiten (sowie an Wochenenden und Feiertagen) pausiert das System die regelmäßigen API-Abfragen und versetzt das Display in einen schonenden Standby-Modus.  
* **Hardware-Interaktion:** Über einen kapazitiven Touch-Sensor (via I2C) kann jederzeit ein sofortiges manuelles Update des Displays erzwungen werden.  
* **Sicheres Administrations-Interface:** Die Verwaltung erfolgt über ein lokales Web-Interface. Dieses ist durch Nginx als Reverse Proxy (HTTPS/SSL) sowie HTTP Basic Authentication abgesichert.  
* **Sicherheitsarchitektur:** Zustandsändernde Aktionen im Web-Interface sind durch POST-Requests (CSRF-Schutz) gesichert. Schreibvorgänge in die Konfigurationsdatei erfolgen atomar, um Datenkorruption bei plötzlichem Stromausfall zu vermeiden.  
* **Integrierte Diagnose:** Ein implementierter Testlauf ermöglicht die Überprüfung aller Display-Zustände und Fehlermeldungen direkt über das Web-Interface.

## **🛠️ Hardware-Voraussetzungen**

* **Raspberry Pi Zero 2 W** (oder ein vergleichbares, aarch64-fähiges Modell)  
* **Waveshare e-Paper Display** (z. B. 2.13" kapazitiv Touch, V3)  
* **MicroSD-Karte** (mit Raspberry Pi OS Lite, 64-bit empfohlen)

## **📦 Verwendete Komponenten & Abhängigkeiten**

Das Projekt baut auf einer Reihe von Systempaketen und Python-Bibliotheken auf:

**System-Pakete (Raspberry Pi OS / Debian):**

* python3-pip, python3-venv, git: Grundlegende Werkzeuge für die Python-Umgebung und Versionskontrolle.  
* libopenjp2-7, libtiff5, libxcb1: Systembibliotheken, die für die Bildverarbeitung auf dem E-Paper-Display zwingend erforderlich sind.  
* i2c-tools: Werkzeuge zur Diagnose und Kommunikation mit dem Touch-Controller.  
* fonts-dejavu: Lokale Schriftarten für eine saubere, skalierbare Textdarstellung.  
* nginx, openssl: Bereitstellung der sicheren HTTPS-Verbindung (Reverse Proxy).

**Python-Bibliotheken:**

* python-webuntis: Schnittstelle zur WebUntis-API.  
* Pillow (PIL): Generierung des Bildmaterials und des Layouts für das Display.  
* Flask & Waitress: Bereitstellung des lokalen Web-Interfaces.  
* smbus2: Direkte I2C-Kommunikation mit dem kapazitiven Touch-Controller.

## **🚀 Installation & Einrichtung**

Eine vollständige, detaillierte Schritt-für-Schritt-Anleitung zur Einrichtung des Raspberry Pi, der Treiber und der Software finden Sie in der Datei [**Installationsanleitung.md**](http://docs.google.com/Installationsanleitung.md).

## **⚙️ Konfiguration**

Das Programm erfordert eine Konfigurationsdatei namens config.json im Hauptverzeichnis. Nutzen Sie die bereitgestellte Datei config.example.json als Vorlage.

**Beispielkonfiguration:**

{  
    "UNTIS\_SERVER": "demo.webuntis.com",  
    "UNTIS\_SCHOOL": "demo\_schule",  
    "UNTIS\_USER": "webuntis\_benutzername",  
    "UNTIS\_PASS": "webuntis\_passwort",  
    "ADMIN\_USER": "admin",  
    "ADMIN\_PASS": "passwort",  
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
            {"start": "09:55", "end": "10:40", "name": "3. Std."},  
            {"start": "10:45", "end": "11:30", "name": "4. Std."},  
            {"start": "11:45", "end": "12:30", "name": "5. Std."},  
            {"start": "12:35", "end": "13:20", "name": "6. Std."},  
            {"start": "13:55", "end": "14:40", "name": "7. Std."},  
            {"start": "14:45", "end": "15:30", "name": "8. Std."}  
        \],  
        "BREAKS": \[  
            {"start": "09:35", "end": "09:50", "name": "1. Pause"},  
            {"start": "11:30", "end": "11:45", "name": "2. Pause"},  
            {"start": "13:20", "end": "13:55", "name": "Mittagspause"}  
        \]  
    }  
}

### **🔒 Wichtige Hinweise zu Datenschutz und Sicherheit**

1. **Dateirechte anpassen:** Stellen Sie sicher, dass die Zugangsdaten in der config.json vor dem unbefugten Auslesen durch andere lokale Benutzer geschützt sind. Führen Sie dazu auf dem System den Befehl chmod 600 config.json aus.  
2. **Standard-Passwörter ändern:** Ändern Sie zwingend die voreingestellten Werte für ADMIN\_USER und ADMIN\_PASS in der config.json vor der ersten produktiven Inbetriebnahme im Netzwerk.  
3. **Versionskontrolle (.gitignore):** Sollten Sie eigene Anpassungen an diesem Code-Repository vornehmen und dieses veröffentlichen wollen, stellen Sie sicher, dass die Datei config.json sowie etwaige Log-Dateien durch die .gitignore vom Upload ausgeschlossen sind. Reale Schul-, Nutzer- oder Zugangsdaten dürfen nicht in öffentliche Repositories gelangen.

## **📝 Lizenz & Nutzung**

Dieses Projekt kann für den schulischen und edukativen Bereich frei genutzt, modifiziert und weiterentwickelt werden.
