"""The read-only Desktop apps route: recipes, install markers and live health."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from routers import desktop_apps

SERVICES = Path(__file__).resolve().parents[2]


class _Healthy(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib name
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def apps_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ODS_EXTENSIONS_DIR", str(SERVICES))
    return tmp_path / "ODS" / "apps"


def _install_marker(apps_home, app_id, version):
    folder = apps_home / app_id / "bin"
    folder.mkdir(parents=True)
    (folder / "ods-app.json").write_text(json.dumps({"Version": version, "Sha256": "a" * 64}), encoding="utf-8")


def test_an_app_with_a_recipe_is_listed_as_available(apps_home):
    apps = {app["id"]: app for app in asyncio.run(desktop_apps.desktop_apps())["apps"]}
    assert apps["qdrant"]["state"] == "available" and apps["qdrant"]["url"] is None
    assert apps["qdrant"]["installCommand"].endswith("-Apps qdrant")


def test_an_installed_app_that_answers_is_running(apps_home, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Healthy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        real = desktop_apps.desktop_app_recipes.load_recipes

        def with_test_port(services):
            return [{**recipe, "port": server.server_port} if recipe["id"] == "qdrant" else recipe for recipe in real(services)]

        monkeypatch.setattr(desktop_apps.desktop_app_recipes, "load_recipes", with_test_port)
        _install_marker(apps_home, "qdrant", "1.16.3")
        qdrant = next(app for app in asyncio.run(desktop_apps.desktop_apps())["apps"] if app["id"] == "qdrant")
        assert qdrant["state"] == "running" and qdrant["installedVersion"] == "1.16.3"
        assert qdrant["url"] == f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()


def test_an_installed_app_that_does_not_answer_is_installed_not_running(apps_home, monkeypatch):
    real = desktop_apps.desktop_app_recipes.load_recipes
    monkeypatch.setattr(desktop_apps.desktop_app_recipes, "load_recipes",
                        lambda services: [{**recipe, "port": 1} for recipe in real(services)])
    _install_marker(apps_home, "qdrant", "1.16.3")
    qdrant = next(app for app in asyncio.run(desktop_apps.desktop_apps())["apps"] if app["id"] == "qdrant")
    assert qdrant["state"] == "installed" and qdrant["url"] is None
