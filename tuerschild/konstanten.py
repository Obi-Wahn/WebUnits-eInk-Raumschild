"""
==============================================================================
Konstanten und feste Werte
==============================================================================
Alle Zahlen und Texte, die das Verhalten des Tuerschilds festlegen, an einer
Stelle. Wer eine Zeitspanne oder ein Layoutmass aendern will, muss dafuer nicht
den Programmcode durchsuchen.
"""
import os

# Das Projektverzeichnis - eine Ebene ueber diesem Paket.
#
# ACHTUNG, HAEUFIGE FEHLERQUELLE: Frueher lag der gesamte Code in einer Datei
# im Projektverzeichnis, und die Pfade wurden aus deren __file__ abgeleitet.
# Diese Datei liegt nun eine Ebene tiefer, im Paket. Ohne das zusaetzliche
# dirname() zeigten die Pfade zur config.json und zu den Waveshare-Treibern
# ins Leere - und ein fehlender Treiber aeussert sich nicht als klare
# Fehlermeldung, sondern als stiller Simulationsmodus mit dunklem Display.
PROJEKT_VERZEICHNIS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# Pfad zu den E-Paper-Treibern des Herstellers (Waveshare)
WAVESHARE_LIB = os.path.join(PROJEKT_VERZEICHNIS,
                             'e-Paper/RaspberryPi_JetsonNano/python/lib')

# Hardware-Pins & Konstanten
TOUCH_RST_PIN = 22
TOUCH_I2C_ADDR = 0x14           # Hexadezimale I2C-Adresse des Touch-Chips
TOUCH_COOLDOWN = 5.0            # Entprell-Zeit in Sekunden (verhindert Touch-Spam)
BACKGROUND_ERROR_PAUSE = 30     # Wartezeit nach einem unerwarteten Schleifenfehler

# Adressen, hinter denen unser eigener Reverse Proxy (Nginx) sitzt. Nur wenn
# eine Anfrage von hier kommt, werten wir die Kopfzeilen aus, in denen der
# Proxy die echte Adresse des Aufrufers vermerkt hat.
TRUSTED_PROXIES = {"127.0.0.1", "::1"}

# Rate-Limiting gegen automatisiertes Passwortraten
MAX_LOGIN_ATTEMPTS = 5          # Fehlversuche bis zur Sperre
LOGIN_LOCKOUT_SECONDS = 60      # Dauer der Sperre
FAILED_LOGIN_TTL = 3600         # Einträge nach einer Stunde ohne Aktivität verwerfen
FAILED_LOGIN_MAX = 1000         # harte Obergrenze gegen unbegrenztes Wachstum

# Zeitsimulation: Nach dieser Zeit kehrt das Programm von selbst zur echten Uhr
# zurueck. Ohne Grenze wuerde ein vergessener Testlauf dazu fuehren, dass das
# Schild dauerhaft einen falschen Tag anzeigt - fuer die Person davor nicht
# erkennbar, weil ein plausibles Datum in der Kopfzeile steht.
SIMULATION_MAX_SECONDS = 7200   # 2 Stunden
HOLIDAYS_CACHE_SECONDS = 86400  # API-Schonung: Ferien für 24 Stunden im RAM cachen

# Grenzen für das automatische Abrufintervall.
# Der Mindestwert schützt den WebUntis-Server: Wenn an einer Schule dutzende
# Türschilder hängen, summieren sich zu kurze Intervalle zu erheblicher Last,
# und die Schule riskiert eine Drosselung oder Sperre.
# Kurze Intervalle bringen ohnehin kaum etwas, denn das Display aktualisiert
# sich zusätzlich zu jedem Stunden- und Pausenbeginn sowie bei Berührung.
MIN_UPDATE_SECONDS = 300        # 5 Minuten
MAX_UPDATE_SECONDS = 86400      # 24 Stunden
DEFAULT_UPDATE_SECONDS = 900    # 15 Minuten

# Grenzen fuer den Raumnamen aus dem Web-Formular.
# Der Name geht als Suchbegriff an WebUntis und steht in der Kopfzeile des
# Displays. Ein leerer Name laesst das Schild ohne erkennbaren Grund
# "Raum None fehlt." melden; ein sehr langer schiebt die Uhrzeit aus der
# Kopfzeile. Beides faellt erst am Geraet auf - deshalb wird schon beim
# Speichern geprueft.
ROOM_NAME_MAX_LEN = 40

# Grenzen fuer den Stundenplan aus dem Web-Formular. Sie halten Tippfehler
# ("800" statt "8:00") und versehentlich eingefuegte Datenmengen ab, bevor sie
# in der config.json landen.
DEFAULT_DAY_START = "07:55"
DEFAULT_DAY_END = "15:30"
SCHEDULE_MAX_LESSONS = 20       # mehr Stunden hat kein Schultag
SCHEDULE_MAX_BREAKS = 12
SCHEDULE_NAME_MAX_LEN = 30      # "Mittagspause" braucht 12 Zeichen

# Ab dieser Dauer gilt eine Stoerung als laenger andauernd. Bis dahin ist ein
# Ausfall Alltag (WLAN-Aussetzer, Wartungsfenster bei WebUntis) und nur eine
# Randnotiz. Danach wird es ein Fall fuer die Betreuung: Der angezeigte Plan
# ist zwar noch der von heute, kurzfristige Aenderungen fehlen aber seit
# Stunden - und das sieht man dem Schild nicht an.
STALE_ALERT_SECONDS = 3 * 3600  # 3 Stunden

# Fehlermeldungen, die auf eine *vorübergehende* Störung hindeuten (Netz/Server).
# Nur bei diesen greifen wir auf den zuletzt abgerufenen Tagesplan zurück.
# Konfigurationsfehler (falsches Passwort, fehlender Raum) sind dagegen dauerhaft
# und müssen sichtbar bleiben, damit sie überhaupt jemand behebt.
ERR_NO_NETWORK = "Kein WLAN/Internet"
ERR_UNTIS_OFFLINE = "WebUntis offline"
TRANSIENT_ERRORS = {ERR_NO_NETWORK, ERR_UNTIS_OFFLINE}

# Magic Numbers (Festgelegte Layout-Werte für das E-Paper-Display)
UI_WIDTH = 250
UI_HEIGHT = 122
UI_HEADER_HEIGHT = 24
UI_LINE_Y = 68
UI_MARGIN = 5
UI_BADGE_PADDING = 3            # Luft links und rechts im Status-Kasten
UI_BADGE_GAP = 5                # Abstand zwischen Status-Kasten und Fachname
UI_ELLIPSIS = "…"               # Zeichen für gekürzte Texte (U+2026)

# Beschriftung der invertierten Status-Kästen. Die Kastenbreite wird zur
# Laufzeit aus der Textbreite berechnet, diese Texte sind also frei änderbar.
STATUS_LABELS = {
    'cancelled': "AUSFALL",
    'irregular': "VERTRETUNG",
}
