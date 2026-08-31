"""
Tests fuer die Zeitsimulation und ihre Verfallsgrenze.

Die Simulation ist ein Testwerkzeug: Sie erlaubt es, Ferien, Wochenenden und
Stundenwechsel zu pruefen, ohne auf den passenden Tag zu warten. Genau deshalb
ist sie aber gefaehrlich, wenn man sie vergisst - in der Kopfzeile des Schilds
steht dann ein voellig plausibles, aber falsches Datum.
"""
import datetime
import time

import raumanzeige as R
from conftest import uhrzeit


def test_ohne_simulation_gilt_die_echte_uhr():
    R.app_state.simulated_datetime = None
    vorher = datetime.datetime.now()
    ergebnis = R.get_now()
    assert abs((ergebnis - vorher).total_seconds()) < 5


def test_gesetzte_simulation_wird_geliefert():
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = time.time()
    assert R.get_now() == uhrzeit(9, 0)


def test_simulation_laeuft_nach_der_frist_ab():
    """
    Der Kernpunkt: Ohne Grenze zeigte das Schild auf unbestimmte Zeit einen
    falschen Tag - und zwar unauffaellig.
    """
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = time.time() - R.SIMULATION_MAX_SECONDS - 1

    ergebnis = R.get_now()

    assert ergebnis != uhrzeit(9, 0)
    assert R.app_state.simulated_datetime is None, "Die Simulation wurde nicht zurueckgesetzt"


def test_ablauf_loest_ein_display_update_aus():
    """Sonst bliebe das falsche Datum bis zum naechsten Intervall stehen."""
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = time.time() - R.SIMULATION_MAX_SECONDS - 1
    R.app_state.force_update_flag = False

    R.get_now()
    assert R.app_state.force_update_flag is True


def test_kurz_vor_ablauf_gilt_die_simulation_noch():
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = time.time() - R.SIMULATION_MAX_SECONDS + 60
    assert R.get_now() == uhrzeit(9, 0)


def test_fehlender_startzeitpunkt_verwirft_die_simulation_nicht():
    """
    Zwei zusammengehoerige Felder sind eine Fehlerquelle. Fehlt der
    Startzeitpunkt, soll die Frist einfach jetzt beginnen - die Simulation darf
    nicht stillschweigend wirkungslos werden.
    """
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = None

    assert R.get_now() == uhrzeit(9, 0)
    assert R.app_state.simulation_started_at is not None


def test_frist_ist_nicht_versehentlich_winzig():
    """Sicherung gegen ein versehentliches Herabsetzen der Konstante."""
    assert R.SIMULATION_MAX_SECONDS >= 600


# ==============================================================================
# Bedienung ueber das Web-Interface
# ==============================================================================
def test_simulation_setzen_startet_die_frist(webclient):
    client, kopf = webclient
    client.post("/simulate_time", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "SIM_TIME": "2026-08-31T09:00",
    })

    assert R.app_state.simulated_datetime == datetime.datetime(2026, 8, 31, 9, 0)
    assert R.app_state.simulation_started_at is not None


def test_zuruecksetzen_beendet_die_simulation(webclient):
    client, kopf = webclient
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    R.app_state.simulation_started_at = time.time()

    client.post("/reset_time", headers=kopf, data={"csrf_token": R.app_state.csrf_token})

    assert R.app_state.simulated_datetime is None
    assert R.app_state.simulation_started_at is None


def test_unbrauchbare_eingabe_aendert_nichts(webclient):
    client, kopf = webclient
    client.post("/simulate_time", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "SIM_TIME": "voelliger Unsinn",
    })
    assert R.app_state.simulated_datetime is None
