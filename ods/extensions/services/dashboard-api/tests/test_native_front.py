"""The Windows Desktop dashboard boundary: who gets the server-held key.

Ports the dev-api-access.js localRequest() matrix. The inner app echoes the
Authorization header it received, so injection and stripping are observable.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

import native_front
from native_front import DashboardFiles, DashboardGate
from routers.dashboard_session import SESSION_COOKIE_NAME, issue_session

KEY = "server-held-key"
LISTENER = "http://127.0.0.1:3101"


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>ODS</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.3f9a.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "sw.js").write_text("// sw", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("secret", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(dist):
    inner = FastAPI()

    @inner.get("/api/echo")
    @inner.get("/api/talk/echo")
    @inner.post("/api/auth/dashboard-session/login")
    async def echo(request: Request):
        return {"authorization": request.headers.get("authorization")}

    inner.mount("/", DashboardFiles(directory=dist, html=True), name="dashboard")
    return DashboardGate(inner, api_key=KEY)


def get(app, path, *, client=("127.0.0.1", 50123), base=LISTENER, headers=None, method="GET"):
    async def go():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url=base) as http:
            return await http.request(method, path, headers=headers)
    return asyncio.run(go())


def admitted(response):
    return response.status_code == 200 and response.json()["authorization"] == f"Bearer {KEY}"


def refused(response):
    return (response.status_code == 401 and response.headers["x-ods-sign-in"] == "required"
            and response.json()["code"] == "dashboard_sign_in_required")


@pytest.mark.parametrize("base", ["http://127.0.0.1:3101", "http://localhost:3101", "http://[::1]:3101"])
def test_the_local_browser_gets_the_key(app, base):
    client = ("::1", 50123) if "::1" in base else ("127.0.0.1", 50123)
    assert admitted(get(app, "/api/echo", base=base, client=client))


def test_same_origin_browser_requests_are_trusted(app):
    assert admitted(get(app, "/api/echo", headers={"Origin": LISTENER, "Sec-Fetch-Site": "same-origin"}))
    assert admitted(get(app, "/api/echo", headers={"Sec-Fetch-Site": "none"}))


def test_a_client_authorization_is_replaced_not_trusted(app):
    assert admitted(get(app, "/api/echo", headers={"Authorization": "Bearer attacker"}))


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:3101"},                 # DNS rebinding
    {"Host": "localhost:9999"},                    # another listener's name
    {"X-Forwarded-For": "203.0.113.9"},            # a proxy in front of localhost
    {"Forwarded": "for=203.0.113.9"},
    {"Via": "1.1 tailscale"},
    {"X-Real-IP": "203.0.113.9"},
    {"Origin": "http://evil.example"},             # another website posting to localhost
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_untrusted_requests_need_a_session(app, headers):
    assert refused(get(app, "/api/echo", headers=headers))


def test_duplicate_host_headers_are_untrusted(app):
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50123))
        async with httpx.AsyncClient(transport=transport, base_url=LISTENER) as http:
            request = http.build_request("GET", "/api/echo")
            request.headers = httpx.Headers([*request.headers.raw, (b"host", b"127.0.0.1:3101")])
            return await http.send(request)
    assert refused(asyncio.run(go()))


def test_a_network_peer_needs_a_session(app):
    assert refused(get(app, "/api/echo", client=("192.168.1.20", 50123)))


def test_a_valid_session_cookie_admits_an_untrusted_request(app):
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={issue_session()}"}
    assert admitted(get(app, "/api/echo", client=("192.168.1.20", 50123), headers=cookie))
    forged = {"Cookie": f"{SESSION_COOKIE_NAME}=forged.value"}
    assert refused(get(app, "/api/echo", client=("192.168.1.20", 50123), headers=forged))


@pytest.mark.parametrize("path, method", [("/api/talk/echo", "GET"), ("/api/auth/dashboard-session/login", "POST")])
def test_talk_and_sign_in_never_receive_the_admin_key(app, path, method):
    for client in (("127.0.0.1", 50123), ("192.168.1.20", 50123)):
        response = get(app, path, client=client, method=method, headers={"Authorization": "Bearer attacker"})
        assert response.status_code == 200 and response.json()["authorization"] is None


def test_pixel_previews_do_not_exist_without_portal(app):
    assert get(app, "/pixel-preview/site-0123456789abcdef01234567/index.html").status_code == 404


SITE = "site-0123456789abcdef01234567"


@pytest.fixture
def preview_app(app):
    seen = []

    def edge(request):
        seen.append(request)
        if request.url.path == f"/preview/{SITE}/index.html":
            return httpx.Response(200, content=b"<h1>game</h1>", headers={
                "Content-Type": "text/html", "Content-Security-Policy": "sandbox allow-scripts",
                "X-Preview-SHA256": "a" * 64, "Set-Cookie": "edge=1", "Server": "edge"})
        return httpx.Response(404, json={"error": "not found"})

    gate = DashboardGate(app.app, api_key=KEY, preview_edge="http://127.0.0.1:9595/", preview_key="preview-key",
                         preview_transport=httpx.MockTransport(edge))
    return gate, seen


def test_portal_previews_relay_to_the_edge_with_the_preview_key(preview_app):
    gate, seen = preview_app
    response = get(gate, f"/pixel-preview/{SITE}/index.html", headers={"Authorization": "Bearer attacker"})
    assert response.status_code == 200 and response.content == b"<h1>game</h1>"
    assert str(seen[-1].url) == f"http://127.0.0.1:9595/preview/{SITE}/index.html"
    assert seen[-1].headers["authorization"] == "Bearer preview-key"
    # The edge's own isolating CSP is kept; nothing else from it leaks through.
    assert response.headers["content-security-policy"] == "sandbox allow-scripts"
    assert response.headers["x-preview-sha256"] == "a" * 64 and "set-cookie" not in response.headers
    assert get(gate, f"/pixel-preview/{SITE}/missing.js").status_code == 404


def test_portal_previews_need_the_dashboard_sign_in_like_any_api(preview_app):
    gate, seen = preview_app
    assert refused(get(gate, f"/pixel-preview/{SITE}/index.html", client=("192.168.1.20", 50123)))
    session = {"Cookie": f"{SESSION_COOKIE_NAME}={issue_session()}"}
    assert get(gate, f"/pixel-preview/{SITE}/index.html", client=("192.168.1.20", 50123), headers=session).status_code == 200


# What a sandboxed preview page (CSP sandbox without allow-same-origin) sends
# for its own files: no cookie, Sec-Fetch-Site cross-site, and Origin null on
# CORS-mode loads such as fonts. Every multi-file site needs these.
@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"},
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "font", "Origin": "null"},
])
def test_a_sandboxed_preview_page_can_load_its_own_files(preview_app, headers):
    gate, seen = preview_app
    response = get(gate, f"/pixel-preview/{SITE}/index.html", headers=headers)
    assert response.status_code == 200 and response.content == b"<h1>game</h1>"
    # The API keeps the stricter browser rule for the same request.
    assert refused(get(gate, "/api/echo", headers=headers))


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:3101", "Sec-Fetch-Site": "cross-site"},      # DNS rebinding
    {"X-Forwarded-For": "203.0.113.9", "Sec-Fetch-Site": "cross-site"},  # a proxy in front of localhost
    {"Via": "1.1 tailscale"},
])
def test_previews_still_refuse_rebinding_and_proxied_requests(preview_app, headers):
    gate, seen = preview_app
    assert refused(get(gate, f"/pixel-preview/{SITE}/index.html", headers=headers)) and not seen


def test_previews_accept_only_reads(preview_app):
    gate, seen = preview_app
    assert get(gate, f"/pixel-preview/{SITE}/index.html", method="POST").status_code == 404 and not seen


@pytest.mark.parametrize("path", [
    f"/pixel-preview/{SITE}/%2e%2e/%2e%2e/v1/models", f"/pixel-preview/{SITE}/a%2fb", f"/pixel-preview/{SITE}/a%5cb",
    "/pixel-preview/not-a-site/index.html", "/pixel-preview/",
])
def test_portal_previews_refuse_anything_but_a_plain_site_path(preview_app, path):
    gate, seen = preview_app
    assert get(gate, path).status_code == 404 and not seen


def test_a_raw_dot_segment_never_reaches_the_relay():
    # HTTP clients normalize a literal ../ before sending, so check the raw rule directly.
    assert not native_front._plain_preview_tail(f"{SITE}/../../v1/models".encode())
    assert native_front._plain_preview_tail(f"{SITE}/assets/game.v2.js".encode())


def test_the_dashboard_and_its_client_routes_are_served(app):
    for path in ("/", "/settings", "/models/library"):
        response = get(app, path)
        assert response.status_code == 200 and "ODS" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_an_unknown_api_path_is_a_json_404_not_the_dashboard(app):
    response = get(app, "/api/does-not-exist")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


def test_hashed_assets_are_cached_and_missing_files_are_404(app):
    asset = get(app, "/assets/app.3f9a.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert get(app, "/sw.js").headers["cache-control"] == "no-cache"
    assert get(app, "/assets/missing.js").status_code == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"])
def test_files_outside_the_dashboard_are_never_served(app, path):
    response = get(app, path)
    assert "secret" not in response.text


def test_every_response_carries_the_security_headers(app):
    for response in (get(app, "/"), get(app, "/api/echo"), get(app, "/api/echo", client=("192.168.1.20", 1))):
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in response.headers["content-security-policy"]


def test_dist_dir_must_be_an_absolute_built_dashboard(dist, tmp_path):
    assert native_front.dist_dir({}) is None
    assert native_front.dist_dir({"ODS_DASHBOARD_DIST": str(dist)}) == dist
    for bad in ("dist", str(tmp_path / "empty")):
        with pytest.raises(ValueError, match="ODS_DASHBOARD_DIST"):
            native_front.dist_dir({"ODS_DASHBOARD_DIST": bad})
