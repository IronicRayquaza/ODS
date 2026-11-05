"""ODS Desktop phone access (prototype, this PC only).

Prints a one-time sign-in link for your phone and shows it as a QR code.
The phone reaches ODS through Tailscale Serve (HTTPS, only your own devices);
ODS itself keeps listening on 127.0.0.1 only.

Before the first run:
  1. Install Tailscale on this PC and on your phone, signed in to the same account.
  2. On this PC: tailscale serve --bg 3101

Run with ODS's own Python (no packages needed):
  "%LOCALAPPDATA%\\ODS\\desktop\\python\\python.exe" phone_link.py

The link works once and expires after 10 minutes. The phone then stays signed
in for 30 days; changing the dashboard password signs every device out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# QR code (byte mode, error correction level L, versions 1-10): enough for a
# ~120-character link. Written here so the sign-in token is never handed to
# a third-party script or website.
# --------------------------------------------------------------------------

_ECC_PER_BLOCK_L = [None, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18]
_BLOCKS_L = [None, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4]
_FORMAT_BITS_L = 1


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_data_modules(ver) // 8 - _ECC_PER_BLOCK_L[ver] * _BLOCKS_L[ver]


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    count = ver // 7 + 2
    step = (ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return [6] + list(reversed([size - 7 - i * step for i in range(count - 1)]))


def qr_matrix(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    for ver in range(1, 11):
        count_bits = 8 if ver <= 9 else 16
        if 4 + count_bits + 8 * len(data) <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError(f"The link is too long for this QR generator ({len(data)} bytes).")
    capacity = _data_codewords(ver) * 8
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # Split into blocks, add error correction, interleave.
    blocks_count, ecc_len = _BLOCKS_L[ver], _ECC_PER_BLOCK_L[ver]
    raw = _raw_data_modules(ver) // 8
    short_blocks = blocks_count - raw % blocks_count
    short_len = raw // blocks_count
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_count):
        chunk = codewords[k:k + short_len - ecc_len + (0 if i < short_blocks else 1)]
        k += len(chunk)
        ecc = _rs_remainder(chunk, divisor)
        if i < short_blocks:
            chunk = chunk + [0]
        blocks.append(chunk + ecc)
    final = [blk[i] for i in range(len(blocks[0])) for j, blk in enumerate(blocks)
             if i != short_len - ecc_len or j >= short_blocks]

    size = ver * 4 + 17
    modules = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool) -> None:
        modules[y][x] = dark
        function[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _alignment_positions(ver, size)
    last = len(pos) - 1
    for i, ax in enumerate(pos):
        for j, ay in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask: int) -> None:
        value = _FORMAT_BITS_L << 3 | mask
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fmt = (value << 10 | rem) ^ 0x5412
        bit = lambda n: (fmt >> n) & 1 != 0  # noqa: E731
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6))
        setf(8, 8, bit(7))
        setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)

    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = ver << 12 | rem
        for i in range(18):
            dark = (vbits >> i) & 1 != 0
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, dark)
            setf(b, a, dark)

    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not function[y][x] and i < len(final) * 8:
                    modules[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 != 0
                    i += 1
        right -= 2

    masks = [
        lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    ]

    def apply(mask: int) -> None:
        for y in range(size):
            for x in range(size):
                if not function[y][x] and masks[mask](x, y):
                    modules[y][x] = not modules[y][x]

    def penalty() -> int:
        score = 0
        for grid in (modules, [list(col) for col in zip(*modules)]):
            for row in grid:
                run, color = 1, row[0]
                for cell in row[1:] + [None]:
                    if cell == color:
                        run += 1
                    else:
                        if run >= 5:
                            score += 3 + run - 5
                        run, color = 1, cell
        for y in range(size - 1):
            for x in range(size - 1):
                c = modules[y][x]
                if c == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, modules))
        total = size * size
        score += (abs(dark * 20 - total * 10) + total - 1) // total * 10 - 10
        return score

    best, best_score = 0, None
    for mask in range(8):
        apply(mask)
        draw_format(mask)
        score = penalty()
        if best_score is None or score < best_score:
            best, best_score = mask, score
        apply(mask)  # XOR again undoes it
    apply(best)
    draw_format(best)
    return modules


def qr_terminal(modules: list[list[bool]]) -> str:
    size = len(modules)
    quiet = 2
    def dark(x: int, y: int) -> bool:
        return 0 <= x < size and 0 <= y < size and modules[y][x]
    lines = []
    for y in range(-quiet, size + quiet, 2):
        line = ""
        for x in range(-quiet, size + quiet):
            top, bottom = dark(x, y), dark(x, y + 1)
            # Light on dark terminals: print the light modules.
            line += {(False, False): "\u2588", (True, False): "\u2584", (False, True): "\u2580", (True, True): " "}[(top, bottom)]
        lines.append(line)
    return "\n".join(lines)


def qr_svg(modules: list[list[bool]], caption: str) -> str:
    size, quiet, scale = len(modules), 4, 8
    full = (size + 2 * quiet) * scale
    rects = "".join(f'<rect x="{(x + quiet) * scale}" y="{(y + quiet) * scale}" width="{scale}" height="{scale}"/>'
                    for y, row in enumerate(modules) for x, dark in enumerate(row) if dark)
    return (f'<!doctype html><meta charset="utf-8"><title>ODS phone sign-in</title>'
            f'<body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff;font:16px system-ui">'
            f'<div style="text-align:center"><h2>Scan with your phone</h2>'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{full}" height="{full}" viewBox="0 0 {full} {full}">'
            f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{rects}</g></svg>'
            f'<p>{caption}</p><p style="color:#666">Works once, for 10 minutes. Close this page afterwards.</p></div></body>')


# --------------------------------------------------------------------------
# Tailscale and ODS
# --------------------------------------------------------------------------

def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def tailscale_exe() -> str:
    found = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    if default.is_file():
        return str(default)
    fail("Tailscale is not installed on this PC. Install it from tailscale.com/download, sign in, then run this again.")


def tailscale_json(exe: str, *args: str) -> dict:
    out = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        fail(f"tailscale {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return json.loads(out.stdout or "{}")


def ods_port() -> int:
    settings = Path(os.environ["LOCALAPPDATA"]) / "ODS" / "desktop" / "settings.json"
    if settings.is_file():
        port = json.loads(settings.read_text(encoding="utf-8")).get("Port") or 0
        if port:
            return int(port)
    return 3101


def serves_ods(serve: dict, port: int) -> bool:
    # tailscale serve status --json: {"Web": {"host:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3101"}}}}}
    for site in (serve.get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            proxy = str(handler.get("Proxy", ""))
            if proxy.rstrip("/").endswith(f":{port}") and ("127.0.0.1" in proxy or "localhost" in proxy):
                return True
    return False


def mint_link(port: int) -> str:
    # From this PC ODS trusts the request (loopback) and adds its own key.
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/auth/dashboard-session/link", method="POST", data=b"")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())["fragment"]
    except urllib.error.URLError as exc:
        fail(f"ODS is not answering on 127.0.0.1:{port} ({exc}). Open ODS first, then run this again.")


def show(url: str, caption: str) -> None:
    modules = qr_matrix(url)
    page = Path(tempfile.gettempdir()) / "ods-phone-sign-in.html"
    page.write_text(qr_svg(modules, caption), encoding="utf-8")
    print(qr_terminal(modules))
    os.startfile(page)  # noqa: S606 - opens the local page in the default browser


def main() -> None:
    if sys.argv[1:] == ["--test-qr"]:
        # No token, no network: only checks that phones can read these QR codes.
        url = "https://example.com/ods-phone-qr-test-0123456789/#ods-login=TEST-not-a-real-token-abcdefghijklmnopqrstuvwxy"
        show(url, "QR test: your phone should show " + url)
        print(f"\nTest QR (no sign-in token). Your phone camera should show:\n  {url}")
        return
    exe = tailscale_exe()
    status = tailscale_json(exe, "status")
    if status.get("BackendState") != "Running":
        fail("Tailscale is installed but not connected. Open Tailscale, sign in, then run this again.")
    host = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not host:
        fail("Tailscale did not report this PC's name. Turn on MagicDNS in the Tailscale admin console.")
    port = ods_port()
    if not serves_ods(tailscale_json(exe, "serve", "status"), port):
        fail(f"Tailscale is not forwarding to ODS yet. Run this once on the PC, then run this again:\n  tailscale serve --bg {port}")
    url = f"https://{host}/{mint_link(port)}"
    show(url, f"https://{host}/")
    print(f"\nScan the QR code with your phone (Tailscale must be on there too), or open:\n  {url}")
    print("It works once, for 10 minutes. A larger QR code is open in your browser.")


if __name__ == "__main__":
    main()
