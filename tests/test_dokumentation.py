"""
Tests gegen das Auseinanderdriften von Dokumentation und Vorlage.

WARUM DAS NOETIG IST:
Die Beispielkonfiguration steht an drei Stellen: in config.example.json, im
readme und in der Installationsanleitung. Kommt ein Feld hinzu, wird leicht
nur eine davon gepflegt - genau das war zweimal der Fall. Wer der
Installationsanleitung folgt, legt seine config.json nach der dortigen Fassung
an und hat dann ein Feld weniger als vorgesehen.

Das faellt beim Programmieren nie auf, denn fehlende Felder haben Vorgaben. Es
faellt erst dem auf, der die Anleitung benutzt - und der kann es nicht wissen.
"""
import json
import os
import re


def projektverzeichnis():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lies(name):
    with open(os.path.join(projektverzeichnis(), name), encoding="utf-8") as datei:
        return datei.read()


def vorlagenschluessel():
    return list(json.loads(lies("config.example.json")))


def test_das_readme_kennt_alle_felder_der_vorlage():
    text = lies("readme.md")
    fehlend = [s for s in vorlagenschluessel() if f'"{s}"' not in text]
    assert not fehlend, f"Im readme fehlen Felder aus config.example.json: {fehlend}"


def test_die_installationsanleitung_kennt_alle_felder_der_vorlage():
    text = lies("Installationsanleitung.md")
    fehlend = [s for s in vorlagenschluessel() if f'"{s}"' not in text]
    assert not fehlend, (
        f"In der Installationsanleitung fehlen Felder aus config.example.json: "
        f"{fehlend}. Wer ihr folgt, legt eine unvollständige config.json an."
    )


def test_der_json_block_im_readme_ist_gueltig_und_vollstaendig():
    """
    Der Block ist als JSON ausgezeichnet und wird zum Kopieren benutzt. Ein
    fehlendes Komma darin faellt sonst erst dem auf, der ihn einfuegt.
    """
    bloecke = re.findall(r"```json\n(.*?)```", lies("readme.md"), re.DOTALL)
    assert bloecke, "Im readme steht kein als JSON ausgezeichneter Block mehr"

    beispiel = json.loads(bloecke[0])
    assert set(beispiel) == set(vorlagenschluessel()), (
        "Die Beispielkonfiguration im readme weicht von config.example.json ab"
    )


def test_die_beispielzeiten_sind_zweistellig():
    """
    Die Vorlage ist das, was abgeschrieben wird. Stuende dort "8:00", zoege
    sich der Fehler durch jede Installation - und das Display bliebe ohne
    Stundennamen, ohne dass jemand die Ursache sieht.
    """
    plan = json.loads(lies("config.example.json"))["SCHEDULE"]
    zeiten = [plan["DAY_START"], plan["DAY_END"]]
    for eintrag in plan["LESSONS"] + plan["BREAKS"]:
        zeiten += [eintrag["start"], eintrag["end"]]

    for zeit in zeiten:
        assert re.fullmatch(r"\d{2}:\d{2}", zeit), f"'{zeit}' ist nicht zweistellig"
