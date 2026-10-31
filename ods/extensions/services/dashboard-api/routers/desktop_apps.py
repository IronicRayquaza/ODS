"""Native apps on ODS Desktop (windows-desktop profile): what can run here and its state. Read-only.

Installing, starting and removing stay with Desktop setup (-Apps <id>) and
uninstall, which own the sign-in tasks. This route only reads the manifest
recipes, the app folders under %LOCALAPPDATA%\\ODS\\apps and each app's
health path on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends

import desktop_app_recipes
from security import verify_api_key

router = APIRouter(tags=["desktop-apps"])
_HEALTH_SECONDS = 2.0


def _apps_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ODS" / "apps"


def _services_dir() -> Path:
    return Path(os.environ["ODS_EXTENSIONS_DIR"])


def _installed_version(app_id: str) -> str | None:
    marker = _apps_root() / app_id / "bin" / "ods-app.json"
    if not marker.is_file():
        return None
    return str(json.loads(marker.read_text(encoding="utf-8-sig")).get("Version") or "") or None


async def _healthy(client: httpx.AsyncClient, port: int, path: str) -> bool:
    try:
        response = await client.get(f"http://127.0.0.1:{port}{path}", timeout=_HEALTH_SECONDS)
    except (httpx.ConnectError, httpx.TimeoutException):
        return False
    return response.status_code < 500


async def _app_state(client: httpx.AsyncClient, recipe: dict) -> dict:
    installed = _installed_version(recipe["id"])
    running = installed is not None and await _healthy(client, recipe["port"], recipe["health"])
    return {
        "id": recipe["id"], "name": recipe["name"], "version": recipe["version"],
        "installedVersion": installed,
        "state": "running" if running else "installed" if installed else "available",
        "url": f"http://127.0.0.1:{recipe['port']}/" if running else None,
        "installCommand": f"windows-desktop.ps1 -Apps {recipe['id']}",
    }


@router.get("/api/desktop/apps", dependencies=[Depends(verify_api_key)])
async def desktop_apps() -> dict:
    recipes = desktop_app_recipes.load_recipes(_services_dir())
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        apps = await asyncio.gather(*(_app_state(client, recipe) for recipe in recipes))
    return {"apps": list(apps)}
