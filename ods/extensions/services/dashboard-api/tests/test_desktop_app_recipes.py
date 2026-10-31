"""native.windows recipes: what ODS Desktop accepts from a service manifest."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import desktop_app_recipes as recipes

SERVICES = Path(__file__).resolve().parents[2]
VALID = {
    "version": "1.16.3",
    "download": {"url": "https://github.com/qdrant/qdrant/releases/download/v1.16.3/qdrant-x86_64-pc-windows-msvc.zip",
                 "size": 27008258, "sha256": "a" * 64},
    "executable": "qdrant.exe",
    "env": {"QDRANT__SERVICE__HTTP_PORT": "{port}", "QDRANT__STORAGE__STORAGE_PATH": "{data}\\storage"},
    "port": 6333,
    "health": "/healthz",
}


def test_the_shipped_qdrant_recipe_loads():
    loaded = {recipe["id"]: recipe for recipe in recipes.load_recipes(SERVICES)}
    qdrant = loaded["qdrant"]
    assert qdrant["version"] == "1.16.3" and qdrant["port"] == 6333 and qdrant["executable"] == "qdrant.exe"
    assert qdrant["env"]["QDRANT__SERVICE__HOST"] == "127.0.0.1"


def test_every_shipped_recipe_binds_loopback_only():
    for recipe in recipes.load_recipes(SERVICES):
        hosts = [value for key, value in recipe["env"].items() if key.endswith("HOST")]
        assert hosts and all(value == "127.0.0.1" for value in hosts), recipe["id"]


def test_placeholders_resolve_for_one_install():
    resolved = recipes.resolve(recipes.parse_recipe("qdrant", "Qdrant", VALID), "C:\\Users\\O'Brien\\ODS\\apps\\qdrant\\data\\")
    assert resolved["env"]["QDRANT__SERVICE__HTTP_PORT"] == "6333"
    assert resolved["env"]["QDRANT__STORAGE__STORAGE_PATH"] == "C:\\Users\\O'Brien\\ODS\\apps\\qdrant\\data\\storage"


@pytest.mark.parametrize("change", [
    {"download": {"url": "http://github.com/a/b/releases/download/v1/x.zip", "size": 1, "sha256": "a" * 64}},
    {"download": {"url": "https://example.com/x.zip", "size": 1, "sha256": "a" * 64}},
    {"download": {"url": VALID["download"]["url"], "size": 0, "sha256": "a" * 64}},
    {"download": {"url": VALID["download"]["url"], "size": 1, "sha256": "A" * 64}},
    {"executable": "..\\cmd.exe"},
    {"executable": "run.bat"},
    {"port": 80},
    {"health": "healthz"},
    {"env": {"lower": "x"}},
    {"env": {"X": "{home}"}},
    {"args": ["--config", 5]},
    {"script": "anything"},
])
def test_a_recipe_outside_the_rules_is_refused(change):
    block = {**copy.deepcopy(VALID), **change}
    with pytest.raises(recipes.RecipeError):
        recipes.parse_recipe("qdrant", "Qdrant", block)


def test_a_relative_data_folder_is_refused():
    with pytest.raises(recipes.RecipeError):
        recipes.resolve(recipes.parse_recipe("qdrant", "Qdrant", VALID), "apps\\qdrant")


def test_the_script_lists_and_resolves_for_setup():
    import json
    listed = json.loads(recipes.main(["list", str(SERVICES)]))
    assert "qdrant" in [recipe["id"] for recipe in listed]
    resolved = json.loads(recipes.main(["resolve", str(SERVICES), "qdrant", "C:\\ODS\\apps\\qdrant\\data"]))
    assert resolved["env"]["QDRANT__STORAGE__STORAGE_PATH"] == "C:\\ODS\\apps\\qdrant\\data\\storage"
    with pytest.raises(recipes.RecipeError):
        recipes.main(["resolve", str(SERVICES), "not-an-app", "C:\\x"])
