"""Portal display identity for the windows-desktop profile, which has no host agent.

Compose keeps this document with the host agent (/v1/pixel/identity). Desktop
keeps the same document, under the same contract, in its private data folder:
portal-identity.json. A save must name the revision it replaces, so two
windows cannot silently overwrite each other.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from portal_identity_contract import default_document, normalize_document
from runtime_profile import RUNTIME_PROFILE

_LOCK = threading.Lock()
_FILE = "portal-identity.json"


class IdentityConflict(ValueError):
    """The saved identity changed since the editor read it."""


def _path() -> Path:
    return Path(RUNTIME_PROFILE.data_dir) / _FILE


def read_document() -> dict:
    path = _path()
    if not path.exists():
        return default_document()
    return normalize_document(json.loads(path.read_text(encoding="utf-8")))


def save_document(edit: dict) -> dict:
    """Apply a normalized edit ({expectedRevision, displayName}); return the saved document."""
    with _LOCK:
        current = read_document()
        if current["revision"] != edit["expectedRevision"]:
            raise IdentityConflict("identity-revision-changed")
        document = {"schemaVersion": 1, "revision": current["revision"] + 1, "displayName": edit["displayName"]}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".portal-identity.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return document
