"""native.windows recipes from service manifests (windows-desktop profile). Pure: no I/O but reading manifests.

A recipe says how ODS Desktop runs a service without Docker: one pinned
release zip, the executable inside it, its environment and a loopback port.
Setup installs from these recipes; dashboard-api reads them for status. The
manifest schema (extensions/schema/service-manifest.v1.json) defines the
block; this module applies the same rules and resolves the placeholders.

Run as a script, it prints every recipe as JSON for setup:
    python -I desktop_app_recipes.py <extensions\\services folder>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PureWindowsPath

import yaml

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_URL = re.compile(r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/download/\S+\.zip$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.exe$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,80}$")
_HEALTH = re.compile(r"^/[A-Za-z0-9._/-]{0,120}$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,40}$")
_PLACEHOLDER = re.compile(r"\{([a-z]+)\}")
_KEYS = {"version", "download", "executable", "args", "env", "port", "health"}


class RecipeError(ValueError):
    """A manifest's native.windows block breaks the recipe rules."""


def parse_recipe(service_id: str, name: str, block: object) -> dict:
    """Validate one native.windows block; return it in a fixed shape."""
    if not _ID.match(service_id):
        raise RecipeError(f"{service_id!r}: invalid service id")
    if not isinstance(block, dict) or not {"version", "download", "executable", "port", "health"} <= set(block) <= _KEYS:
        raise RecipeError(f"{service_id}: native.windows needs version, download, executable, port and health only")
    download = block["download"]
    if not isinstance(download, dict) or set(download) != {"url", "size", "sha256"}:
        raise RecipeError(f"{service_id}: download needs exactly url, size and sha256")
    if not isinstance(download["url"], str) or not _URL.match(download["url"]):
        raise RecipeError(f"{service_id}: download url must be a GitHub release zip over HTTPS")
    if type(download["size"]) is not int or download["size"] < 1 or not _SHA256.match(str(download["sha256"])):
        raise RecipeError(f"{service_id}: download needs a positive size and a lowercase SHA-256")
    if not _VERSION.match(str(block["version"])) or not _EXECUTABLE.match(str(block["executable"])):
        raise RecipeError(f"{service_id}: invalid version or executable")
    if type(block["port"]) is not int or not 1024 <= block["port"] <= 65535 or not _HEALTH.match(str(block["health"])):
        raise RecipeError(f"{service_id}: invalid port or health path")
    args = block.get("args", [])
    env = block.get("env", {})
    if not isinstance(args, list) or not all(isinstance(item, str) and len(item) <= 512 for item in args):
        raise RecipeError(f"{service_id}: args must be a list of strings")
    if not isinstance(env, dict) or not all(_ENV_NAME.match(str(key)) and isinstance(value, str) for key, value in env.items()):
        raise RecipeError(f"{service_id}: env must map NAME to string")
    for value in [*args, *env.values()]:
        unknown = set(_PLACEHOLDER.findall(value)) - {"port", "data"}
        if unknown:
            raise RecipeError(f"{service_id}: unknown placeholder {sorted(unknown)[0]!r}")
    return {"id": service_id, "name": name, "version": str(block["version"]), "url": download["url"],
            "size": download["size"], "sha256": download["sha256"], "executable": block["executable"],
            "args": list(args), "env": dict(env), "port": block["port"], "health": block["health"]}


def resolve(recipe: dict, data_dir: str) -> dict:
    """Fill {port} and {data} for one install; data_dir must be an absolute Windows path."""
    if not PureWindowsPath(data_dir).is_absolute():
        raise RecipeError("the app data folder must be an absolute Windows path")
    values = {"port": str(recipe["port"]), "data": data_dir.rstrip("\\")}
    fill = lambda text: _PLACEHOLDER.sub(lambda match: values[match.group(1)], text)  # noqa: E731
    return {**recipe, "args": [fill(item) for item in recipe["args"]],
            "env": {key: fill(value) for key, value in recipe["env"].items()}}


def load_recipes(services_dir: Path) -> list[dict]:
    """Every service manifest with a native.windows block, sorted by id."""
    recipes = []
    for manifest in sorted(Path(services_dir).glob("*/manifest.yaml")):
        document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        block = (document.get("native") or {}).get("windows")
        if block is None:
            continue
        service = document.get("service") or {}
        recipes.append(parse_recipe(str(service.get("id", "")), str(service.get("name", "")), block))
    return recipes


if __name__ == "__main__":
    print(json.dumps(load_recipes(Path(sys.argv[1])), indent=1))
