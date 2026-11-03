"""Serve the dashboard from dashboard-api in the Windows Desktop profile.

In the Compose stack, nginx serves the built dashboard and adds the server-held
DASHBOARD_API_KEY to admin API requests from the local browser. The Desktop
profile has no nginx: dashboard-api is the only listener, so this module is
that boundary. It ports the stricter twin of nginx's gate, the Vite dev gate
(dashboard/dev-api-access.js): a request is trusted without a session only
from a loopback peer, with a loopback Host naming this listener's port, no
forwarding headers, no duplicated Host/Origin/Sec-Fetch-Site, and a same-origin
(or absent) Origin and Sec-Fetch-Site. A page on another website that sends a
request to localhost is therefore never trusted. Portal previews (read-only
GET/HEAD under /pixel-preview/) use nginx's own rule instead, without the
Origin and Sec-Fetch-Site checks, because a sandboxed preview page loads its
own files as a cross-site, cookieless request.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from routers.dashboard_session import SESSION_COOKIE_NAME, session_is_valid

_LOOPBACK_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$", re.IGNORECASE)
_PUBLIC_SESSION = re.compile(r"^/api/auth/dashboard-session/(login|logout)/?$")
_SIGN_IN_BODY = json.dumps({"detail": "Sign in to the ODS dashboard to continue.",
                            "code": "dashboard_sign_in_required"}).encode()
# nginx.conf's policy without the Pixel preview origins (no preview here).
_SECURITY_HEADERS = (
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
     b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: blob: "
     b"https://huggingface.co https://cdn-avatars.huggingface.co https://t3.gstatic.com/faviconV2; "
     b"connect-src 'self'; media-src 'self' blob:; font-src 'self' https://cdn.jsdelivr.net; frame-src 'self'"),
)


# nginx.conf's /pixel-preview/ location: the Portal workbench frames and reads
# published snapshots through the edge's authenticated /preview/ relay.
_PREVIEW_PATH = re.compile(rb"^/pixel-preview/(site-[a-f0-9]{24}/[^?#]*)$")
_PREVIEW_HEADERS = frozenset((
    "content-type", "content-security-policy", "cross-origin-opener-policy", "cross-origin-resource-policy",
    "access-control-allow-origin", "permissions-policy", "referrer-policy", "x-content-type-options",
    "x-preview-sha256", "cache-control", "content-disposition",
))
_PREVIEW_TIMEOUT_SECONDS = 15.0


def _plain_preview_tail(tail: bytes) -> bool:
    """No dot segment, encoded separator or backslash: the HTTP client would
    normalize those and the relay could name another edge route."""
    for segment in tail.split(b"/"):
        decoded = urllib.parse.unquote_to_bytes(segment)
        if decoded in (b".", b"..") or b"/" in decoded or b"\\" in decoded or b"\\" in segment:
            return False
    return True


def _loopback_peer(scope: Scope) -> bool:
    client = scope.get("client")
    if not client:
        return False
    address = client[0].lower()
    if address == "::1":
        return True
    address = address.removeprefix("::ffff:")
    parts = address.split(".")
    return len(parts) == 4 and parts[0] == "127" and all(part.isdigit() for part in parts)


def _local_listener(scope: Scope) -> Optional[tuple[dict[str, str], re.Match[str]]]:
    """Pure: a loopback peer naming this listener directly with no proxy in
    between, nginx's $ods_trusted_local. Returns the headers and the Host
    match, or None."""
    if not _loopback_peer(scope):
        return None
    server = scope.get("server")
    headers = [(name.decode("latin-1").lower(), value.decode("latin-1")) for name, value in scope["headers"]]
    counts: dict[str, int] = {}
    for name, _ in headers:
        counts[name] = counts.get(name, 0) + 1
    if any(counts.get(name, 0) > 1 for name in ("host", "origin", "sec-fetch-site")):
        return None
    if any(name in ("forwarded", "via", "x-real-ip") or name.startswith("x-forwarded-") for name, _ in headers):
        return None
    values = dict(headers)
    authority = _LOOPBACK_HOST.match(values.get("host", ""))
    if not authority or not server or int(authority.group(2) or 80) != server[1]:
        return None
    return values, authority


def trusted_local(scope: Scope) -> bool:
    """Pure: the dev-api-access.js localRequest() rules for an ASGI scope."""
    listener = _local_listener(scope)
    if listener is None:
        return False
    values, authority = listener
    server = scope["server"]
    site = values.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return False
    origin = values.get("origin")
    port = "" if server[1] == 80 else f":{server[1]}"
    return origin is None or origin == f"http://{authority.group(1).lower()}{port}"


def _session_cookie(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name.lower() == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, cookie = part.strip().partition("=")
                if key == SESSION_COOKIE_NAME:
                    return cookie
    return ""


def _uses_admin_key(path: str) -> bool:
    """Talk and dashboard sign-in carry their own session; never the admin key."""
    if not (path == "/api" or path.startswith("/api/")):
        return False
    return not (path.startswith("/api/talk/") or _PUBLIC_SESSION.match(path))


class DashboardGate:
    """ASGI middleware: the loopback trust boundary and security headers."""

    def __init__(self, app: ASGIApp, api_key: str, preview_edge: str = "", preview_key: str = "",
                 preview_transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.app = app
        self.authorization = f"Bearer {api_key}".encode("latin-1")
        # Set only when Portal runs (PIXEL_EDGE_URL and PIXEL_PREVIEW_PROXY_KEY);
        # without them /pixel-preview/ stays a 404, as before Portal existed.
        self.preview = (preview_edge.rstrip("/"), preview_key) if preview_edge and preview_key else None
        self.preview_transport = preview_transport

    async def _preview(self, scope: Scope, receive: Receive, send: Send) -> None:
        match = _PREVIEW_PATH.match(scope.get("raw_path") or scope["path"].encode("latin-1"))
        if match and not _plain_preview_tail(match.group(1)):
            match = None
        if self.preview is None or scope["type"] != "http" or scope["method"] not in ("GET", "HEAD") or not match:
            await Response(status_code=404)(scope, receive, send)
            return
        # nginx's rule, not the stricter API one: a preview runs in a CSP
        # sandbox without allow-same-origin, so the browser sends its own
        # script, style and image requests as Sec-Fetch-Site: cross-site
        # with no cookie. Requiring same-origin here broke every
        # multi-file site. This route only reads published snapshots.
        if not (_local_listener(scope) is not None or session_is_valid(_session_cookie(scope))):
            await Response(_SIGN_IN_BODY, status_code=401, media_type="application/json",
                           headers={"X-ODS-Sign-In": "required", "Cache-Control": "no-store"})(scope, receive, send)
            return
        edge, key = self.preview
        target = f"{edge}/preview/{match.group(1).decode('latin-1')}"
        query = scope.get("query_string") or b""
        if query:
            target += "?" + query.decode("latin-1")
        try:
            async with httpx.AsyncClient(timeout=_PREVIEW_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False,
                                         transport=self.preview_transport) as client:
                upstream = await client.get(target, headers={"Authorization": f"Bearer {key}"})
        except httpx.TransportError:
            await Response(status_code=502, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        headers = {name: value for name, value in upstream.headers.items() if name.lower() in _PREVIEW_HEADERS}
        body = b"" if scope["method"] == "HEAD" else upstream.content
        await Response(body, status_code=upstream.status_code, headers=headers)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            # Every response, including the gate's own 401 and 404.
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(name, value) for name, value in _SECURITY_HEADERS if name not in present]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        path = scope["path"]
        if path == "/pixel-preview" or path.startswith("/pixel-preview/"):
            await self._preview(scope, receive, secured)
            return
        # A client's own Authorization is never trusted; only the gate adds one.
        headers = [(name, value) for name, value in scope["headers"] if name.lower() != b"authorization"]
        if _uses_admin_key(path):
            if not (trusted_local(scope) or session_is_valid(_session_cookie(scope))):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await Response(_SIGN_IN_BODY, status_code=401, media_type="application/json",
                               headers={"X-ODS-Sign-In": "required", "Cache-Control": "no-store"})(scope, receive, secured)
                return
            headers.append((b"authorization", self.authorization))
        await self.app({**scope, "headers": headers}, receive, secured)


class DashboardFiles(StaticFiles):
    """The built dashboard with SPA fallback and nginx's cache rules."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        # Starlette hands over an OS-normalized path: backslashes on Windows.
        posix = path.replace("\\", "/")
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and (posix == "api" or posix.startswith("api/")):
                # An unknown API path is an API 404, as nginx forwards it;
                # never the dashboard page with a 200.
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            # Client-side routes (/settings, /models/...) load index.html;
            # a missing file with an extension is a real 404.
            if exc.status_code != 404 or "." in posix.rsplit("/", 1)[-1]:
                raise
            response = FileResponse(Path(str(self.directory)) / "index.html")
        hashed = posix.startswith("assets/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if hashed else "no-cache"
        return response


def dist_dir(env: Optional[dict[str, str]] = None) -> Optional[Path]:
    """ODS_DASHBOARD_DIST, the built dashboard, or None when it is not served."""
    raw = (env if env is not None else os.environ).get("ODS_DASHBOARD_DIST", "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or not (path / "index.html").is_file():
        raise ValueError(f"ODS_DASHBOARD_DIST must be an absolute built dashboard folder: {raw}")
    return path
