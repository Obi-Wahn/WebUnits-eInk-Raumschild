# **WebUntis E-Paper-Raumanzeige**

Dieses Projekt stellt ein automatisiertes, digitales Türschild für den Einsatz im schulischen Umfeld bereit. Das System synchronisiert sich selbstständig mit der WebUntis-API und visualisiert den aktuellen sowie den folgenden Unterricht auf einem energieeffizienten E-Paper-Display.

*Hinweis: Dieses Projekt und die zugehörige Dokumentation wurden für den schulischen Einsatz konzipiert und mit Unterstützung von KI-Modellen entwickelt und strukturiert.*

## **✨ Funktionsumfang**

* **Automatisierte Synchronisation:** Abruf der aktuellen Plandaten über die WebUntis-API. Das Display zeigt übersichtlich die aktuell laufende Stunde ("JETZT") sowie die darauf folgende Belegung ("DANACH") an.
* **Ausfall- und Vertretungserkennung:** Planänderungen wie Ausfälle oder Vertretungen werden durch spezifische WebUntis-Statuscodes erkannt und visuell hervorgehoben (z. B. durch invertierte Darstellung).
* **Ressourcenschonender Ruhemodus:** Außerhalb der regulären Unterrichtszeiten (sowie an Wochenenden und Feiertagen) pausiert das System die regelmäßigen API-Abfragen und versetzt das Display in einen schonenden Standby-Modus.
* **Offline-Rücklage bei Netzausfall:** Fällt das WLAN oder WebUntis aus, ersetzt das Display den Stundenplan nicht durch eine Fehlermeldung, sondern zeigt den zuletzt abgerufenen Tagesplan weiter. Dieser wird dabei lokal neu ausgewertet, sodass auch während der Störung zur richtigen Zeit auf die nächste Stunde gewechselt wird. Ein kleines Ausrufezeichen in der Kopfzeile (und ein Hinweis im Web-Interface) kennzeichnet die Daten als möglicherweise nicht mehr taggenau. Dauerhafte Fehler wie ein falsches Passwort bleiben dagegen sichtbar, damit sie behoben werden.
* **Hardware-Interaktion:** Über einen kapazitiven Touch-Sensor (via I2C) kann jederzeit ein sofortiges manuelles Update des Displays erzwungen werden.
* **Responsives & Sicheres Administrations-Interface:** Die Verwaltung erfolgt über ein lokales Web-Interface. Dank modernem **CSS-Grid** und **Mobile-First-Ansatz** passt sich das Layout perfekt an: Auf dem Smartphone fließen die Bedienelemente logisch untereinander, auf einem Desktop-Monitor entfaltet sich ein Zwei-Spalten-Cockpit. Abgesichert ist das Ganze durch Nginx als Reverse Proxy (HTTPS/SSL) sowie HTTP Basic Authentication.
* **Sichere Systemsteuerung & Architektur:** Über das Web-Interface lässt sich der Raspberry Pi per Knopfdruck sicher neu starten oder herunterfahren. Zustandsändernde Aktionen sind durch POST-Requests (CSRF-Schutz) gesichert. Schreibvorgänge in die Konfigurationsdatei erfolgen atomar, um Datenkorruption bei plötzlichem Stromausfall zu vermeiden.
* **Integrierte Diagnose:** Ein implementierter Testlauf ermöglicht die Überprüfung aller Display-Zustände und Fehlermeldungen direkt über das Web-Interface.
* **Geprüfte Konfiguration:** Beim Einlesen der `config.json` wird gemeldet, was am Raumnamen und am Stundenplan nicht stimmt — mit Angabe des betroffenen Eintrags. Diese Fehler äußern sich sonst nicht als Fehlermeldung, sondern als stille Unauffälligkeit: `"8:00"` statt `"08:00"` lässt den Stundennamen einfach leer, ein leerer Raumname erzeugt ein „Raum None fehlt." ohne erkennbaren Grund. Gewarnt wird nur, abgelehnt nichts — ein Türschild, das wegen eines Kommafehlers gar nicht erst startet, wäre die schlechtere Lösung.
* **Meldung bei anhaltender Störung:** Ein kurzer Ausfall bleibt eine Randnotiz — die Offline-Rücklage trägt ihn. Dauert er länger als drei Stunden, wird daraus eine Fehlermeldung im Systemprotokoll und ein roter Hinweis samt Dauer im Web-Interface. Ohne diese Eskalation sähe das Schild weiterhin völlig gesund aus: Es zeigt ja einen plausiblen Plan, nur eben einen, in dem seit Stunden keine Vertretung mehr nachgetragen wurde.

## **🛠️ Hardware-Voraussetzungen**

* **Raspberry Pi Zero 2 W** (oder ein vergleichbares, aarch64-fähiges Modell)
* **Waveshare e-Paper Display** (z. B. 2.13" kapazitiv Touch, V3)
* **MicroSD-Karte** (mit Raspberry Pi OS Lite, 64-bit empfohlen)

## **📦 Verwendete Komponenten & Abhängigkeiten**

Das Projekt baut auf einer Reihe von Systempaketen und Python-Bibliotheken auf:

**System-Pakete (Raspberry Pi OS / Debian):**

* `python3-pip`, `python3-venv`, `git`: Grundlegende Werkzeuge für die Python-Umgebung und Versionskontrolle.
* `libopenjp2-7`, `libtiff-dev`, `libxcb1`: Systembibliotheken, die für die Bildverarbeitung auf dem E-Paper-Display zwingend erforderlich sind.
* `i2c-tools`: Werkzeuge zur Diagnose und Kommunikation mit dem Touch-Controller.
* `fonts-dejavu`: Lokale Schriftarten für eine saubere, skalierbare Textdarstellung.
* `nginx`, `openssl`: Bereitstellung der sicheren HTTPS-Verbindung (Reverse Proxy).

**Python-Bibliotheken:**

Die exakten, getesteten Versionen sind in der Datei [`requirements.txt`](./requirements.txt) hinterlegt und werden mit `pip install -r requirements.txt` installiert.


* [**python-webuntis**](https://github.com/python-webuntis/python-webuntis): Schnittstelle zur WebUntis-API.
* [**Pillow (PIL)**](https://python-pillow.github.io/): Generierung des Bildmaterials und des Layouts für das Display.
* [**Flask**](https://flask.palletsprojects.com/) & [**Waitress**](https://docs.pylonsproject.org/projects/waitress/): Bereitstellung des lokalen Web-Interfaces.
* [**Waveshare e-Paper**](https://github.com/waveshareteam/e-Paper): Die offiziellen Hardware-Treiber (SPI). Diese werden separat geklont und dabei auf einen geprüften Commit festgelegt.
* [**gpiozero**](https://gpiozero.readthedocs.io/) & [**lgpio**](https://pypi.org/project/lgpio/): Werden von aktuellen Fassungen des Waveshare-Treibers zur Ansteuerung der GPIO-Pins vorausgesetzt.
* [**smbus2**](https://pypi.org/project/smbus2/): Direkte I2C-Kommunikation mit dem kapazitiven Touch-Controller.

## **🚀 Installation & Einrichtung**

Eine vollständige, detaillierte Schritt-für-Schritt-Anleitung zur Einrichtung des Raspberry Pi, der Treiber und der Software finden Sie in der Datei [**Installationsanleitung.md**](./Installationsanleitung.md).

## **⚙️ Konfiguration**

Das Programm erfordert eine Konfigurationsdatei namens `config.json` im Hauptverzeichnis. Nutzen Sie die bereitgestellte Datei `config.example.json` als Vorlage.

### **Beispielkonfiguration:**

```json
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
            {"start": "08:50", "end": "09:35", "name": "2. Std."},
            {"start": "09:55", "end": "10:40", "name": "3. Std."},
            {"start": "10:45", "end": "11:30", "name": "4. Std."},
            {"start": "11:45", "end": "12:30", "name": "5. Std."},
            {"start": "12:35", "end": "13:20", "name": "6. Std."},
            {"start": "13:55", "end": "14:40", "name": "7. Std."},
            {"start": "14:45", "end": "15:30", "name": "8. Std."}
        ],
        "BREAKS": [
            {"start": "09:35", "end": "09:50", "name": "1. Pause"},
            {"start": "11:30", "end": "11:45", "name": "2. Pause"},
            {"start": "13:20", "end": "13:55", "name": "Mittagspause"}
        ]
    }
}
```

`SCHEDULE` enthält **keine Plandaten.** Der Unterricht kommt ausschließlich aus WebUntis, und dieses Gerät schreibt dorthin nie zurück. Hier steht nur, wie das Schild die Zeiten des Hauses benennt: dass `"08:00"` die *1. Std.* ist und dass zwischen 13:20 und 13:55 „Mittagspause" statt „Raum ist frei" angezeigt wird. Diese Werte werden bei der Einrichtung einmal eingetragen und danach kaum wieder angefasst; das Web-Interface bearbeitet sie deshalb bewusst nicht — es bleibt Steuerung und Anzeige.

Damit ein Tippfehler in dieser Datei nicht unbemerkt bleibt, wird sie beim Einlesen geprüft. Ein Fehler landet als Warnung im Protokoll (`journalctl -u raumanzeige`), etwa:

```
WARNING - config.json: SCHEDULE: LESSONS, Eintrag 3 (start): '9:55' muss
zweistellig geschrieben werden ('09:55') - sonst bleibt der Stundenname leer.
```

Das ist der häufigste Fehler und zugleich der am schwersten zu findende: Die Startzeit aus WebUntis ist immer zweistellig, und da das Programm Uhrzeiten als Zeichenketten vergleicht, trifft `"9:55"` schlicht auf nichts. Das Display zeigt dann alles Übrige normal an, nur der Stundenname bleibt leer.

### **🔒 Wichtige Hinweise zu Datenschutz und Sicherheit**

1. **Principle of Least Privilege (PoLP):** Der Webserver läuft aus Sicherheitsgründen als eingeschränkter Standardnutzer (pi) und nicht als root. Für systemkritische Befehle (Reboot/Shutdown) wird dem Nutzer über die /etc/sudoers punktuell eine isolierte Ausnahmegenehmigung erteilt.  
2. **Dateirechte anpassen:** Stellen Sie sicher, dass die Zugangsdaten in der config.json vor dem unbefugten Auslesen durch andere lokale Benutzer geschützt sind. Führen Sie dazu auf dem System den Befehl chmod 600 config.json aus.  
3. **Standard-Passwörter ändern:** Ändern Sie zwingend die voreingestellten Werte für ADMIN\_USER und ADMIN\_PASS in der config.json vor der ersten produktiven Inbetriebnahme im Netzwerk.  
4. **Versionskontrolle (.gitignore):** Sollten Sie eigene Anpassungen an diesem Code-Repository vornehmen und dieses veröffentlichen wollen, stellen Sie sicher, dass die Datei config.json sowie etwaige Log-Dateien durch die .gitignore vom Upload ausgeschlossen sind. Reale Schul-, Nutzer- oder Zugangsdaten dürfen nicht in öffentliche Repositories gelangen.

## **📂 Aufbau des Programms**

Der Programmcode liegt im Paket `tuerschild/`, aufgeteilt in Ebenen, die aufeinander aufbauen und nur nach unten greifen:

| Modul | Aufgabe |
|---|---|
| `konstanten.py` | Feste Werte und Pfade, ohne Abhängigkeiten |
| `zustand.py` | Datenstrukturen (`Lesson`, `TimedLesson`) und gemeinsamer Zustand |
| `konfiguration.py` | `config.json` lesen und schreiben, Uhrzeit samt Simulation |
| `hardware.py` | GPIO, I2C-Touch, Displaytreiber, Schriftarten |
| `anzeige.py` | Layout und Zeichnen auf dem E-Paper |
| `untis.py` | Abruf der Plandaten und Offline-Rücklage |
| `web.py` | Flask-Oberfläche zur Administration |
| `steuerung.py` | Hintergrundschleife, die alles zusammenführt |
| `templates/dashboard.html` | Die HTML-Vorlage des Web-Interfaces |

Die Vorlage lag früher als 232-zeilige Zeichenkette in `web.py` — ein Zugeständnis an die Installation per Copy&Paste einer einzigen Datei. Seit der Aufteilung in ein Paket gilt das nicht mehr; als eigene Datei bekommt sie im Editor wieder Syntaxhervorhebung, und `web.py` enthält nur noch Programmcode.

Gestartet wird weiterhin über `raumanzeige.py` im Projektverzeichnis — dort steht nur noch, was zum Starten und sauberen Beenden gehört.

*Hinweis für Änderungen:* Soll in Tests eine Funktion ersetzt werden, muss das im **definierenden** Modul geschehen (etwa `tuerschild.hardware.epd2in13_V3`). Die Sammel-Importe in `tuerschild/__init__.py` sind Kopien der Verweise; ein Ersetzen dort träfe nur diese Kopie.

## **🔄 Aktualisieren**

Ein neuer Programmstand wird mit `update.sh` eingespielt:

```bash
cd ~/webuntis-display
./update.sh
```

Das Skript holt den neuen Stand, installiert geänderte Abhängigkeiten nach (nur wenn sich `requirements.txt` wirklich geändert hat — ein `pip`-Lauf dauert auf einem Pi Zero mehrere Minuten), lässt die Tests laufen und startet erst danach den Dienst neu. **Schlagen die Tests fehl, unterbleibt der Neustart** und das Skript nennt den Commit zum Zurückrollen. Der laufende Dienst arbeitet dann unverändert weiter, denn er hat sein Programm längst im Speicher.

Eigene, nicht eingecheckte Änderungen im Projektverzeichnis führen zum Abbruch, bevor irgendetwas überschrieben wird. Die `config.json` ist davon nicht betroffen — sie steht in der `.gitignore`.

Zwei Schalter für Sonderfälle: `--ohne-tests` und `--ohne-neustart`.

*Wird das Programm von Hand gestartet statt als Dienst* (etwa im Testbetrieb), sagt das Skript das und weist darauf hin, dass der laufende Prozess von Hand beendet und neu gestartet werden muss.

## **🧪 Tests**

Das Projekt bringt eine automatisierte Testsuite mit. Sie prüft die Logik des Programms — Stundenauswahl, Offline-Rücklage, Textlayout, Konfigurationsgrenzen und deren Prüfung, die Meldung bei anhaltenden Störungen sowie Anmeldung und CSRF-Schutz des Web-Interfaces.

**Ausführen auf dem Raspberry Pi:**

```bash
cd ~/webuntis-display
source webuntis/bin/activate
pip install -r requirements-dev.txt   # einmalig
pytest -q
```

Der sinnvolle Zeitpunkt dafür ist **nach einem `git pull` und vor dem Neustart des Dienstes**. Der Durchlauf dauert wenige Sekunden.

**Automatisch bei GitHub:** Bei jedem Push und jedem Pull Request läuft die Suite unter Python 3.11 und 3.13 (siehe `.github/workflows/tests.yml`). Das Ergebnis erscheint direkt im Pull Request.

*Sicherheitshinweis:* Die Tests fassen die Hardware nicht an. Dafür sorgen zwei Vorkehrungen: `tests/conftest.py` setzt die Umgebungsvariable `TUERSCHILD_OHNE_HARDWARE=1`, bevor das Paket geladen wird — damit werden GPIO, I2C und der Displaytreiber gar nicht erst eingebunden. Zusätzlich ersetzt eine Vorrichtung den Treiber vor jedem einzelnen Test durch eine Attrappe; gezeichnet wird mit echtem Pillow in einen Speicherpuffer, Layoutfehler fallen also auf. Ein Testlauf während des laufenden Betriebs stört die Anzeige nicht.

> Die Sperre ist nötig, weil der Waveshare-Treiber die GPIO-Pins **schon beim Import** belegt. Ohne sie brach die Testsuite bei laufendem Programm mit `lgpio.error: 'GPIO busy'` ab — die Attrappe allein greift erst danach und damit zu spät. Im Betrieb wird die Variable nirgends gesetzt; `raumanzeige.py` lädt die Hardware wie gewohnt.

*Was die Tests nicht abdecken:* Die Hardware selbst — SPI-Übertragung, I2C-Touch und das tatsächliche Erscheinungsbild auf dem Panel. Dafür bleiben der Testlauf-Knopf im Web-Interface und der Blick auf das Schild.

## **📝 Lizenz & Nutzung**

Dieses Projekt ist Open Source und steht unter der [MIT-Lizenz](./LICENSE). Dieses Projekt kann für den schulischen und edukativen Bereich frei genutzt, modifiziert und weiterentwickelt werden. Ideal geeignet als Praxisprojekt für den Informatikunterricht\! 
