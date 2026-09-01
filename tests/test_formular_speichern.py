"""
Tests fuer das Speicherformular im Web-Interface.

Geprueft wird hier die VERDRAHTUNG: dass die Pruefungen aus konfiguration.py
tatsaechlich im Anfrageweg sitzen und nicht nur als Funktionen existieren, und
dass bei einer Ablehnung wirklich nichts in der config.json landet.

Die Pruefregeln selbst stehen in tests/test_konfiguration_pruefung.py.
"""
import json

import tuerschild as R


def formular(kopf_token, **felder):
    daten = {"csrf_token": kopf_token, "ROOM_NAME": "Raum101"}
    daten.update(felder)
    return daten


def plan_text(**abweichungen):
    grundlage = {
        "DAY_START": "07:55",
        "DAY_END": "15:30",
        "LESSONS": [{"start": "08:00", "end": "08:45", "name": "1. Std."}],
        "BREAKS": [],
    }
    grundlage.update(abweichungen)
    return json.dumps(grundlage)


# ==============================================================================
# Stundenplan bearbeiten
# ==============================================================================
def test_stundenplan_laesst_sich_speichern(webclient):
    client, kopf = webclient
    antwort = client.post("/save", headers=kopf, data=formular(
        R.app_state.csrf_token,
        SCHEDULE=plan_text(LESSONS=[{"start": "09:00", "end": "09:45", "name": "Block A"}])))

    assert antwort.status_code == 302
    gespeichert = R.get_cached_config()["SCHEDULE"]
    assert gespeichert["LESSONS"] == [{"start": "09:00", "end": "09:45", "name": "Block A"}]


def test_das_textfeld_zeigt_den_gespeicherten_plan(webclient):
    """Ohne diesen Weg zurueck koennte man den Plan nur ueberschreiben, nicht ändern."""
    client, kopf = webclient
    seite = client.get("/", headers=kopf).get_data(as_text=True)

    assert 'name="SCHEDULE"' in seite
    assert "DAY_START" in seite
    assert "1. Std." in seite


def test_kaputter_stundenplan_wird_nicht_gespeichert(webclient):
    client, kopf = webclient
    vorher = R.get_cached_config()["SCHEDULE"]

    client.post("/save", headers=kopf, data=formular(
        R.app_state.csrf_token, SCHEDULE='{"DAY_START": "08:00",}'))

    assert R.get_cached_config()["SCHEDULE"] == vorher


def test_der_grund_der_ablehnung_erscheint_auf_der_seite(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data=formular(
        R.app_state.csrf_token,
        SCHEDULE=plan_text(LESSONS=[{"start": "0800", "end": "08:45", "name": "1. Std."}])))

    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" in seite
    assert "HH:MM" in seite


def test_die_abgelehnte_eingabe_geht_nicht_verloren(webclient):
    """
    Eine laengere Eingabe darf nicht verschwinden, weil in einer Zeile ein Komma
    fehlt - sonst tippt man den ganzen Plan noch einmal.
    """
    client, kopf = webclient
    eingabe = plan_text(LESSONS=[{"start": "0800", "end": "08:45", "name": "Meine Stunde"}])
    client.post("/save", headers=kopf, data=formular(R.app_state.csrf_token, SCHEDULE=eingabe))

    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Meine Stunde" in seite


def test_die_fehlermeldung_erscheint_nur_einmal(webclient):
    """
    Sie gehoert zu einem einzelnen Speicherversuch. Bliebe sie stehen, warnte
    die Seite noch Stunden spaeter vor einem laengst behobenen Fehler.
    """
    client, kopf = webclient
    client.post("/save", headers=kopf, data=formular(
        R.app_state.csrf_token, SCHEDULE="kein json"))

    assert "Nicht gespeichert" in client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" not in client.get("/", headers=kopf).get_data(as_text=True)


def test_erfolgreiches_speichern_wird_bestaetigt(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data=formular(R.app_state.csrf_token,
                                                     SCHEDULE=plan_text()))

    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "gespeichert" in seite.lower()


def test_ohne_textfeld_bleibt_der_plan_unveraendert(webclient):
    """
    Aeltere Seiten, die noch im Browser offen sind, senden das Feld nicht mit.
    Dann darf der Stundenplan weder geloescht noch die Eingabe abgelehnt werden.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()["SCHEDULE"]

    antwort = client.post("/save", headers=kopf,
                          data=formular(R.app_state.csrf_token, ROOM_NAME="Neuer Raum"))

    assert antwort.status_code == 302
    assert R.get_cached_config()["SCHEDULE"] == vorher
    assert R.get_cached_config()["ROOM_NAME"] == "Neuer Raum"


# ==============================================================================
# Raumname
# ==============================================================================
def test_leerer_raumname_wird_abgelehnt(webclient):
    client, kopf = webclient
    vorher = R.get_cached_config()["ROOM_NAME"]

    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": "   "})

    assert R.get_cached_config()["ROOM_NAME"] == vorher
    assert "Nicht gespeichert" in client.get("/", headers=kopf).get_data(as_text=True)


def test_zu_langer_raumname_wird_abgelehnt(webclient):
    client, kopf = webclient
    vorher = R.get_cached_config()["ROOM_NAME"]

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "R" * (R.ROOM_NAME_MAX_LEN + 1)})

    assert R.get_cached_config()["ROOM_NAME"] == vorher


def test_raumname_wird_von_leerzeichen_befreit(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": "  Chemie 2  "})
    assert R.get_cached_config()["ROOM_NAME"] == "Chemie 2"


# ==============================================================================
# Alles oder nichts
# ==============================================================================
def test_bei_einer_ablehnung_wird_gar_nichts_gespeichert(webclient):
    """
    Der wichtigste Test dieser Datei. Wuerde das Formular haeppchenweise
    uebernommen, waere nach einer Fehlermeldung unklar, welcher Stand nun in der
    Datei steht: Der Raum geaendert, der Stundenplan nicht - und niemand sieht
    es der Seite an.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Ganz neuer Raum",
        "AUTO_UPDATE_SECONDS": "1800",
        "SCHEDULE": '{"DAY_START": "kaputt"}'})

    nachher = R.get_cached_config()
    assert nachher["ROOM_NAME"] == vorher["ROOM_NAME"]
    assert nachher["AUTO_UPDATE_SECONDS"] == vorher["AUTO_UPDATE_SECONDS"]
    assert nachher["SCHEDULE"] == vorher["SCHEDULE"]


def test_gueltiges_formular_uebernimmt_alle_felder(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Physik 1",
        "AUTO_UPDATE_SECONDS": "1800",
        "SCHEDULE": plan_text(DAY_END="16:00")})

    conf = R.get_cached_config()
    assert conf["ROOM_NAME"] == "Physik 1"
    assert conf["AUTO_UPDATE_SECONDS"] == 1800
    assert conf["SCHEDULE"]["DAY_END"] == "16:00"


def test_speichern_braucht_weiterhin_einen_csrf_token(webclient):
    """Die neuen Felder duerfen den Schutz nicht ausgehebelt haben."""
    client, kopf = webclient
    antwort = client.post("/save", headers=kopf, data={"ROOM_NAME": "Ohne Token"})
    assert antwort.status_code == 403
