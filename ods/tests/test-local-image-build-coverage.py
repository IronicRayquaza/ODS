#!/usr/bin/env python3
"""Local-image build coverage contract.

Every service defined in docker-compose.base.yml with a ``build:`` section and
no pull-able ``image:`` must appear in each installer's local-build list, or the
installer runs ``docker compose up --no-build`` against an image that was never
built and fails with "No such image" on a fresh host.

This is the negative self-test for the model-router fleet regression
(build-only core service missing from the hardcoded installer build lists):
CI validated compose config but never ran the real build->up flow, so the gap
only surfaced on live hosts. This contract closes that gap.

Stdlib only. Run: python3 tests/test-local-image-build-coverage.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.base.yml"
LINUX = ROOT / "installers" / "phases" / "11-services.sh"
MACOS = ROOT / "installers" / "macos" / "install-macos.sh"
WINDOWS = ROOT / "installers" / "windows" / "install-windows.ps1"

# Services intentionally NOT built by the base install path (documented):
#   llama-server  — pulled image on most backends; AMD builds it via a
#                   backend-specific branch, not the base build list.
#   comfyui       — optional, gated on ENABLE_COMFYUI.
_KNOWN_NON_BASE_BUILD = {"llama-server", "comfyui"}


def base_build_only_services() -> set[str]:
    """Top-level base.yml services that have build: and no image:."""
    text = BASE.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_services = False
    services: dict[str, dict[str, bool]] = {}
    current: str | None = None
    for line in lines:
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([a-z0-9][a-z0-9-]*):\s*$", line)
        if m:
            current = m.group(1)
            services[current] = {"build": False, "image": False}
            continue
        if current and re.match(r"^    build:\s*$", line):
            services[current]["build"] = True
        elif current and re.match(r"^    image:\s", line):
            services[current]["image"] = True
    return {
        name for name, flags in services.items()
        if flags["build"] and not flags["image"]
    }


def installer_build_list(path: Path, pattern: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found: set[str] = set()
    for m in re.finditer(pattern, text):
        found.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))
    # every conditional single-add line: += "svc" / +=(svc)
    return found


def main() -> int:
    required = base_build_only_services() - _KNOWN_NON_BASE_BUILD
    errors: list[str] = []

    # Linux: _candidate_build_services=(...) plus any += lines
    linux_text = LINUX.read_text(encoding="utf-8")
    linux = set()
    m = re.search(r"_candidate_build_services=\(([^)]*)\)", linux_text)
    if m:
        linux.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))
    linux.update(re.findall(r"_candidate_build_services\+=\(([a-z0-9-]+)\)", linux_text))

    # macOS
    macos_text = MACOS.read_text(encoding="utf-8")
    macos = set()
    m = re.search(r"_macos_candidate_build_services=\(([^)]*)\)", macos_text)
    if m:
        macos.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))

    # Windows: $_buildServices = @(...) plus += "svc" lines
    win_text = WINDOWS.read_text(encoding="utf-8")
    windows = set()
    m = re.search(r"\$_buildServices = @\(([^)]*)\)", win_text)
    if m:
        windows.update(re.findall(r'"([a-z0-9-]+)"', m.group(1)))
    windows.update(re.findall(r'\$_buildServices \+= "([a-z0-9-]+)"', win_text))

    for name, present in (("Linux 11-services.sh", linux),
                          ("macOS install-macos.sh", macos),
                          ("Windows install-windows.ps1", windows)):
        missing = required - present
        if missing:
            errors.append(f"{name}: build-only base services not in build list: {sorted(missing)}")

    if errors:
        print("[FAIL] local-image build coverage:")
        for e in errors:
            print(f"  - {e}")
        print(f"  (base build-only services requiring coverage: {sorted(required)})")
        return 1
    print(f"[OK] local-image build coverage: {sorted(required)} covered by all installers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
