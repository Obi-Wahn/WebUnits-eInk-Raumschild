"""
==============================================================================
Datenstrukturen und zentraler Programmzustand
==============================================================================
Hier liegen die Bausteine, mit denen alle uebrigen Ebenen arbeiten: eine
Unterrichtsstunde als Datensatz und der gemeinsame Zustand des Programms.
"""
import datetime
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PIL import ImageFont


@dataclass
class Lesson:
    """
    Datenstruktur für einen einzelnen Unterrichtsblock.
    Verhindert Schreibfehler, die bei der Nutzung von Standard-Dictionaries 
    (z.B. lesson['Fach'] statt lesson['fach']) oft passieren.
    """
    fach: str
    fach_lang: str  # Für den ausgeschriebenen Namen, z.B. "Mathematik-Förderkurs"
    lehrer: str
    klasse: str
    zeit: str
    stunde: str
    status_code: Optional[str]
    stunden_info: str

@dataclass
class TimedLesson:
    """
    Eine bereits vollständig ausgelesene Stunde samt ihrer Zeiten.

    TECHNISCHER HINTERGRUND (Warum eine eigene Struktur?):
    Die Objekte, die die WebUntis-Bibliothek liefert, lesen Fach, Lehrkraft und
    Klasse erst beim Zugriff nach - und fragen dafür ihre Sitzung. Ist die
    Sitzung beendet und das Netz weg, kann dieser Zugriff fehlschlagen oder
    hängen. Für die Offline-Rücklage speichern wir deshalb nicht die
    Original-Objekte, sondern diese eigene Struktur: Sie enthält nur noch
    einfache Werte und ist vollständig unabhängig von Netz und Sitzung.
    """
    start: datetime.datetime
    end: datetime.datetime
    lesson: Lesson

class AppState:
    """
    Zentrale Zustandsverwaltung (State Management).
    Kapselt alle globalen Variablen an einem Ort. Dies verhindert das unkontrollierte
    Überschreiben von Werten über verschiedene Dateien/Funktionen hinweg.
    """
    def __init__(self):
        # Steuerungs-Flags für den Ablauf der Hintergrund-Schleife
        self.force_update_flag: bool = True     # Erzwingt ein sofortiges Display-Update
        self.show_demo_once: bool = False       # Zeigt einmalig simulierte Demo-Daten
        self.test_mode_active: bool = False     # Pausiert das System für den Testlauf
        self.shutdown_event = threading.Event() # Signalisiert allen Threads, dass das System beendet wird
        
        # TECHNISCHER HINTERGRUND: Thread-Locks (Sperren)
        # Hier laufen Threads parallel (Flask-Webserver vs. Hintergrund-Loop). 
        # Ein "Lock" (Mutex) wirkt wie ein Schlüssel: Wer den Schlüssel hat, darf 
        # die Hardware/Datei nutzen. Der andere Thread wartet. Das verhindert Datenkorruption.
        self.display_lock = threading.Lock()    # Schützt das SPI-Display vor simultanen Schreibzugriffen
        self.state_lock = threading.Lock()      # Schützt Zugriffe auf diesen AppState
        self.config_lock = threading.Lock()     # Schützt das Dateisystem (config.json)
        
        # Caches & Simulation (Zwischenspeicher im RAM für mehr Performance)
        self.simulated_datetime: Optional[datetime.datetime] = None
        # Zeitpunkt (Systemuhr), zu dem die Simulation gesetzt wurde - fuer den
        # automatischen Rueckfall nach SIMULATION_MAX_SECONDS
        self.simulation_started_at: Optional[float] = None
        self.current_display_data: Optional[Dict[str, Optional[Lesson]]] = None
        self.current_display_msg: str = "Warte auf erstes Update..."
        self.cached_config: Dict[str, Any] = {}
        self.last_config_mtime: float = 0
        self.cached_holidays = None
        self.last_holidays_fetch: float = 0
        self.global_fonts: Dict[str, ImageFont.FreeTypeFont] = {}

        # Offline-Rücklage: der zuletzt erfolgreich abgerufene Tagesplan,
        # bereits vollständig ausgelesen (siehe TimedLesson).
        # Fällt WLAN oder WebUntis aus, zeigen wir weiter diesen Plan an,
        # statt eine Fehlermeldung über gültige Unterrichtsdaten zu legen.
        # Achtung: None bedeutet "keine Rücklage vorhanden", eine leere Liste
        # dagegen "heute findet nachweislich kein Unterricht statt".
        self.cached_lessons: Optional[List[TimedLesson]] = None
        self.cached_lessons_date: Optional[datetime.date] = None
        self.last_successful_sync: Optional[datetime.datetime] = None
        self.data_is_stale: bool = False
        
        # Security: Rate-Limiting gegen Brute-Force-Angriffe (IP -> {count, lockout_until})
        self.failed_logins: Dict[str, Dict[str, float]] = {}
        
        # Security: Generiert beim Start einen einmaligen, kryptografisch sicheren Token.
        # Schützt gegen CSRF (Cross-Site Request Forgery) Angriffe über das Web-Interface.
        self.csrf_token: str = secrets.token_hex(32)

# Instanziierung des globalen Zustands
app_state = AppState()
