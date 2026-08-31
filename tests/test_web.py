"""
Tests fuer das Web-Interface: Anmeldung, CSRF-Schutz und Anzeige.

Genutzt wird der Testclient von Flask. Er stellt echte Anfragen an die
Anwendung, ohne dass ein Server laufen muss - die Routen werden also wirklich
ausgefuehrt, samt Decorators fuer Anmeldung und CSRF-Pruefung.
"""
import base64
import statistics
import time

import tuerschild as R
from tuerschild import web
from conftest import uhrzeit


# ==============================================================================
# Anmeldung
# ==============================================================================
def test_ohne_zugangsdaten_kein_zutritt(webclient):
    client, _ = webclient
    antwort = client.get("/")
    assert antwort.status_code == 401


def test_mit_richtigen_zugangsdaten_erreichbar(webclient):
    client, kopf = webclient
    assert client.get("/", headers=kopf).status_code == 200


def test_falsches_passwort_wird_abgelehnt(webclient):
    client, _ = webclient
    falsch = {"Authorization": "Basic " + base64.b64encode(b"admin:falsch").decode()}
    assert client.get("/", headers=falsch).status_code == 401


def test_falscher_benutzername_wird_abgelehnt(webclient):
    client, _ = webclient
    falsch = {"Authorization": "Basic " + base64.b64encode(b"root:geheim").decode()}
    assert client.get("/", headers=falsch).status_code == 401


def test_klartextpasswort_wird_beim_ersten_login_gehasht(webclient):
    """
    Auto-Migration: In der config.json darf das Passwort im Klartext stehen,
    danach liegt dort nur noch ein Hash - selbst wenn die SD-Karte in falsche
    Haende geraet.
    """
    client, kopf = webclient
    client.get("/", headers=kopf)

    gespeichert = R.get_cached_config()["ADMIN_PASS"]
    assert gespeichert.startswith("scrypt:") or gespeichert.startswith("pbkdf2:")
    assert "geheim" not in gespeichert


def test_fehlende_angaben_werfen_keine_ausnahme():
    """check_password_hash(hash, None) wuerde ohne Absicherung abstuerzen."""
    assert R.check_auth(None, None) is False
    assert R.check_auth("", "") is False


def test_falscher_name_wird_nicht_schneller_abgelehnt(webclient):
    """
    Schutz gegen Timing-Angriffe: Wuerde die Passwortpruefung bei falschem
    Namen uebersprungen, waere an der Antwortzeit ablesbar, ob ein
    Benutzername existiert. Die Pruefung ist bewusst rechenintensiv, der
    Unterschied waere also deutlich messbar.
    """
    client, kopf = webclient
    client.get("/", headers=kopf)          # loest die Hash-Migration einmalig aus

    def mittlere_dauer(benutzer, passwort, laeufe=5):
        zeiten = []
        for _ in range(laeufe):
            beginn = time.perf_counter()
            R.check_auth(benutzer, passwort)
            zeiten.append(time.perf_counter() - beginn)
        return statistics.median(zeiten)

    falscher_name = mittlere_dauer("voelligAndererName", "geheim")
    falsches_passwort = mittlere_dauer("admin", "falschesPasswort")

    verhaeltnis = falscher_name / falsches_passwort
    assert 0.4 < verhaeltnis < 2.5, f"Verhaeltnis {verhaeltnis:.2f}"


# ==============================================================================
# CSRF-Schutz
# ==============================================================================
def test_post_ohne_token_wird_abgewiesen(webclient):
    client, kopf = webclient
    assert client.post("/update", headers=kopf).status_code == 403


def test_post_mit_falschem_token_wird_abgewiesen(webclient):
    client, kopf = webclient
    antwort = client.post("/update", headers=kopf, data={"csrf_token": "erfunden"})
    assert antwort.status_code == 403


def test_post_mit_richtigem_token_wird_angenommen(webclient):
    client, kopf = webclient
    antwort = client.post("/update", headers=kopf,
                          data={"csrf_token": R.app_state.csrf_token})
    assert antwort.status_code == 302          # Weiterleitung zurueck zur Startseite
    assert R.app_state.force_update_flag is True


def test_csrf_token_wird_zeitkonstant_verglichen(webclient, monkeypatch):
    """
    Der Unterschied zwischen '==' und secrets.compare_digest() ist reines
    Zeitverhalten und liesse sich bei einem 64-Zeichen-Token nicht zuverlaessig
    messen - der Vergleich ist dafuer viel zu schnell.

    Statt der Zeit pruefen wir deshalb die Zusammenarbeit: Wird beim Vergleich
    des Tokens ueberhaupt compare_digest aufgerufen? Ein Rueckfall auf '=='
    faellt damit auf.
    """
    aufrufe = []
    echt = web.secrets.compare_digest

    def mitschreiben(a, b):
        aufrufe.append((a, b))
        return echt(a, b)

    monkeypatch.setattr(web.secrets, "compare_digest", mitschreiben)

    client, kopf = webclient
    client.post("/update", headers=kopf, data={"csrf_token": R.app_state.csrf_token})

    # check_auth vergleicht ebenfalls zeitkonstant - uns interessiert der
    # Aufruf, an dem der CSRF-Token beteiligt war.
    assert any(R.app_state.csrf_token in paar for paar in aufrufe), \
        "Der CSRF-Token wurde nicht mit compare_digest verglichen"


def test_systembefehle_sind_ebenfalls_geschuetzt(webclient):
    """Ueber diese Routen laesst sich der Pi neu starten - ohne Token niemals."""
    client, kopf = webclient
    for route in ["/sys_reboot", "/sys_shutdown"]:
        assert client.post(route, headers=kopf).status_code == 403


# ==============================================================================
# Einstellungen speichern
# ==============================================================================
def test_intervall_wird_beim_speichern_begrenzt(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Raum101",
        "AUTO_UPDATE_SECONDS": "5",
    })
    assert R.get_cached_config()["AUTO_UPDATE_SECONDS"] == R.MIN_UPDATE_SECONDS


def test_raumname_wird_uebernommen(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Chemie 2",
        "AUTO_UPDATE_SECONDS": "900",
    })
    assert R.get_cached_config()["ROOM_NAME"] == "Chemie 2"


# ==============================================================================
# Darstellung des Dashboards
# ==============================================================================
def test_ohne_stoerung_kein_warnbanner(webclient):
    client, kopf = webclient
    R.app_state.data_is_stale = False
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "nicht erreichbar" not in inhalt


def test_bei_stoerung_erscheint_das_warnbanner(webclient):
    client, kopf = webclient
    R.app_state.data_is_stale = True
    R.app_state.last_successful_sync = uhrzeit(8, 15)

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "WebUntis ist derzeit nicht erreichbar" in inhalt
    assert "31.08.2026 08:15" in inhalt


def test_api_texte_werden_maskiert(webclient):
    """
    Schutz gegen Cross-Site Scripting: Texte aus der WebUntis-API landen
    ungeprueft im Dashboard. Enthielten sie HTML, duerfte es nicht ausgefuehrt
    werden.
    """
    client, kopf = webclient
    R.app_state.current_display_msg = "<script>alert('xss')</script>"

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "<script>alert" not in inhalt
    assert "&lt;script&gt;" in inhalt


def test_zeilenumbrueche_in_meldungen_werden_dargestellt(webclient):
    """'Schöne Ferien!\\n(Sommerferien)' soll im Browser zweizeilig erscheinen."""
    client, kopf = webclient
    R.app_state.current_display_msg = "Schöne Ferien!\n(Sommerferien)"

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Schöne Ferien!<br>(Sommerferien)" in inhalt


def test_simulierte_zeit_wird_im_dashboard_ausgewiesen(webclient):
    client, kopf = webclient
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "ZEIT WIRD SIMULIERT" in inhalt
