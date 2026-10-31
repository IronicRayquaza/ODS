"""Portal display identity in the windows-desktop profile (no host agent)."""

from __future__ import annotations

import asyncio
import json

import pytest

import desktop_portal_identity
import pixel_chat_identity
import runtime_profile
from routers import portal_identity


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    profile = runtime_profile.RuntimeProfile(runtime_profile.WINDOWS_DESKTOP, str(tmp_path))
    for module in (desktop_portal_identity, pixel_chat_identity, portal_identity):
        monkeypatch.setattr(module, "RUNTIME_PROFILE", profile)

    async def no_host_agent(*args, **kwargs):
        raise AssertionError("the Desktop profile must never dial the host agent")

    monkeypatch.setattr(pixel_chat_identity, "async_request_json", no_host_agent)
    monkeypatch.setattr(portal_identity, "request_agent_json", no_host_agent)
    return tmp_path


def test_a_new_install_uses_the_default_name(desktop):
    assert desktop_portal_identity.read_document() == {"schemaVersion": 1, "revision": 0, "displayName": "Portal"}
    assert asyncio.run(pixel_chat_identity.confirmed_display_name()) == "Portal"


def test_a_saved_name_is_what_each_turn_uses(desktop):
    saved = desktop_portal_identity.save_document({"expectedRevision": 0, "displayName": "Iris"})
    assert saved == {"schemaVersion": 1, "revision": 1, "displayName": "Iris"}
    assert json.loads((desktop / "portal-identity.json").read_text(encoding="utf-8")) == saved
    assert asyncio.run(pixel_chat_identity.confirmed_display_name()) == "Iris"


def test_a_save_against_an_old_revision_is_refused(desktop):
    desktop_portal_identity.save_document({"expectedRevision": 0, "displayName": "Iris"})
    with pytest.raises(desktop_portal_identity.IdentityConflict):
        desktop_portal_identity.save_document({"expectedRevision": 0, "displayName": "Other"})
    assert desktop_portal_identity.read_document()["displayName"] == "Iris"


def test_a_damaged_file_is_refused_not_rewritten(desktop):
    (desktop / "portal-identity.json").write_text('{"schemaVersion": 1, "revision": 0, "displayName": " x "}', encoding="utf-8")
    with pytest.raises(ValueError):
        desktop_portal_identity.read_document()


def test_the_identity_routes_use_the_desktop_store(desktop):
    response = asyncio.run(portal_identity._request("GET"))
    assert json.loads(response.body) == {"schemaVersion": 1, "revision": 0, "displayName": "Portal"}
    saved = asyncio.run(portal_identity._request("POST", {"expectedRevision": 0, "displayName": "Iris"}))
    assert json.loads(saved.body)["revision"] == 1
    with pytest.raises(portal_identity.HTTPException) as conflict:
        asyncio.run(portal_identity._request("POST", {"expectedRevision": 0, "displayName": "Again"}))
    assert conflict.value.status_code == 409
