"""
Tests fuer die Ermittlung der Aufrufer-Adresse und den Rate-Limiter.

Hinter dem Reverse Proxy sieht Flask immer 127.0.0.1. Ohne Auswertung der
Proxy-Kopfzeile zaehlte der Rate-Limiter alle Zugriffe in einen gemeinsamen
Topf - fuenf Fehlversuche eines Fremden haetten alle anderen mit ausgesperrt.

Die Kopfzeile darf aber nur vom eigenen Proxy stammen duerfen. Sonst koennte
sich ein Angreifer bei jedem Versuch eine neue Adresse geben und die Sperre
vollstaendig umgehen. Beides wird hier geprueft.
"""
import base64
import time

import raumanzeige as R


def _falsche_daten():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:falsch").decode()}


# ==============================================================================
# get_client_ip
# ==============================================================================
def test_ohne_proxy_gilt_die_direkte_adresse(webclient):
    client, _ = webclient
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "192.168.1.50"}):
        assert R.get_client_ip() == "192.168.1.50"


def test_hinter_dem_proxy_zaehlt_die_kopfzeile(webclient):
    """Der eigentliche Zweck: Nicht mehr alle Aufrufer als 127.0.0.1 fuehren."""
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                    headers={"X-Real-IP": "192.168.178.55"}):
        assert R.get_client_ip() == "192.168.178.55"


def test_gefaelschte_kopfzeile_von_aussen_wird_ignoriert(webclient):
    """
    SICHERHEITSKERN: Kommt die Anfrage NICHT vom Proxy, darf die Kopfzeile
    keine Rolle spielen - sie ist dann vom Aufrufer selbst gesetzt.
    """
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "192.168.1.99"},
                                    headers={"X-Real-IP": "1.2.3.4"}):
        assert R.get_client_ip() == "192.168.1.99"


def test_bei_x_forwarded_for_zaehlt_der_letzte_eintrag(webclient):
    """
    Der Proxy haengt die von ihm gesehene Adresse hinten an. Die vorderen
    Eintraege koennen vom Aufrufer stammen und sind faelschbar.
    """
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                    headers={"X-Forwarded-For": "1.2.3.4, 192.168.178.55"}):
        assert R.get_client_ip() == "192.168.178.55"


def test_x_real_ip_hat_vorrang_vor_x_forwarded_for(webclient):
    with R.app.test_request_context(
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Real-IP": "10.0.0.7", "X-Forwarded-For": "1.2.3.4"}):
        assert R.get_client_ip() == "10.0.0.7"


def test_unbrauchbare_kopfzeile_wird_verworfen(webclient):
    """Beliebiger Text als Schluessel waere ein Weg, den Speicher zu fluten."""
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                    headers={"X-Real-IP": "kein" * 500}):
        assert R.get_client_ip() == "127.0.0.1"


def test_ohne_kopfzeile_bleibt_es_beim_localhost(webclient):
    with R.app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert R.get_client_ip() == "127.0.0.1"


# ==============================================================================
# Sperre nach zu vielen Fehlversuchen
# ==============================================================================
def test_sperre_greift_nach_fuenf_fehlversuchen(webclient):
    client, _ = webclient
    kopf = _falsche_daten()
    kopf["X-Real-IP"] = "192.168.178.55"

    for _ in range(R.MAX_LOGIN_ATTEMPTS):
        assert client.get("/", headers=kopf).status_code == 401

    assert client.get("/", headers=kopf).status_code == 429


def test_sperre_trifft_nur_die_betroffene_adresse(webclient):
    """
    Der Kern des Problems: Frueher sperrte ein Fremder alle anderen mit aus,
    weil hinter dem Proxy jede Anfrage als 127.0.0.1 gezaehlt wurde.
    """
    client, gute_daten = webclient

    angreifer = _falsche_daten()
    angreifer["X-Real-IP"] = "192.168.178.99"
    for _ in range(R.MAX_LOGIN_ATTEMPTS):
        client.get("/", headers=angreifer)
    assert client.get("/", headers=angreifer).status_code == 429

    # Eine andere Adresse mit gueltigen Daten muss weiterhin durchkommen
    lehrkraft = dict(gute_daten)
    lehrkraft["X-Real-IP"] = "192.168.178.20"
    assert client.get("/", headers=lehrkraft).status_code == 200


def test_erfolgreicher_login_loescht_den_zaehler(webclient):
    client, gute_daten = webclient

    kopf = _falsche_daten()
    kopf["X-Real-IP"] = "192.168.178.30"
    client.get("/", headers=kopf)

    erfolg = dict(gute_daten)
    erfolg["X-Real-IP"] = "192.168.178.30"
    client.get("/", headers=erfolg)

    assert "192.168.178.30" not in R.app_state.failed_logins


# ==============================================================================
# Bereinigung der Fehlversuchs-Liste
# ==============================================================================
def test_veraltete_eintraege_werden_entfernt():
    jetzt = time.time()
    R.app_state.failed_logins = {
        "10.0.0.1": {"count": 1, "lockout_until": 0, "last_seen": jetzt},
        "10.0.0.2": {"count": 1, "lockout_until": 0, "last_seen": jetzt - R.FAILED_LOGIN_TTL - 10},
    }
    with R.app_state.state_lock:
        R.cleanup_failed_logins(jetzt)

    assert "10.0.0.1" in R.app_state.failed_logins
    assert "10.0.0.2" not in R.app_state.failed_logins


def test_liste_waechst_nicht_unbegrenzt():
    """
    Ohne Obergrenze bliebe jede Adresse, die je einen Fehlversuch hatte, fuer
    immer im Speicher - auf einem Geraet mit 512 MB kein theoretisches Problem.
    """
    jetzt = time.time()
    R.app_state.failed_logins = {
        f"10.0.{i // 256}.{i % 256}": {"count": 1, "lockout_until": 0, "last_seen": jetzt - i}
        for i in range(R.FAILED_LOGIN_MAX + 200)
    }
    with R.app_state.state_lock:
        R.cleanup_failed_logins(jetzt)

    assert len(R.app_state.failed_logins) <= R.FAILED_LOGIN_MAX


def test_bereinigung_laeuft_bei_jeder_anfrage(webclient):
    """
    Die Bereinigung muss im Anfragepfad haengen, nicht nur als Funktion
    existieren. Sonst waechst die Liste im Betrieb trotzdem unbegrenzt - und
    ein Test, der die Funktion direkt aufruft, wuerde das nie bemerken.
    """
    client, kopf = webclient
    R.app_state.failed_logins["10.9.9.9"] = {
        "count": 1,
        "lockout_until": 0,
        "last_seen": time.time() - R.FAILED_LOGIN_TTL - 10,
    }

    client.get("/", headers=kopf)

    assert "10.9.9.9" not in R.app_state.failed_logins


def test_bereinigung_behaelt_die_juengsten_eintraege():
    """Eine gerade aktive Sperre darf nicht durch die Notbremse verschwinden."""
    jetzt = time.time()
    R.app_state.failed_logins = {
        f"10.0.{i // 256}.{i % 256}": {"count": 1, "lockout_until": 0, "last_seen": jetzt - i}
        for i in range(R.FAILED_LOGIN_MAX + 200)
    }
    with R.app_state.state_lock:
        R.cleanup_failed_logins(jetzt)

    assert "10.0.0.0" in R.app_state.failed_logins      # juengster Eintrag
