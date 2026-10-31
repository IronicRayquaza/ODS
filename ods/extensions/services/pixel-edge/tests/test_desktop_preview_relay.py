"""ODS Desktop (Windows) preview relay: loopback TCP to the snapshot's own origin.

Windows asyncio has no Unix socket client, so with PIXEL_PREVIEW_ORIGIN_PORT
set the edge reaches the Desktop preview service on 127.0.0.1 under the
site's <site>.localhost origin. TCP only, so this runs on every platform.
"""
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

from aiohttp import web, ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TOKEN = "desktop-preview-token-" + "x" * 24


class TestDesktopPreviewRelay(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.site = "site-" + "c" * 24
        self.body = b"<h1>desktop preview</h1>"
        self.hosts = []

        async def snapshot(request):
            self.hosts.append(request.headers.get("Host"))
            return web.Response(body=self.body, content_type="text/html",
                                headers={"X-Preview-SHA256": hashlib.sha256(self.body).hexdigest()})

        upstream = web.Application()
        upstream.router.add_get(f"/{self.site}/", snapshot)
        self.up_runner = web.AppRunner(upstream)
        await self.up_runner.setup()
        site = web.TCPSite(self.up_runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.saved = {name: os.environ.get(name) for name in (
            "PIXEL_OPENWEBUI_KEY", "PIXEL_PREVIEW_PROXY_KEY", "PIXEL_INGRESS_SOCKET", "PIXEL_PREVIEW_SOCKET",
            "PIXEL_PREVIEW_ORIGIN_PORT")}
        absent = os.path.join(tempfile.gettempdir(), "ods-absent.sock")
        os.environ.update({"PIXEL_OPENWEBUI_KEY": TOKEN, "PIXEL_PREVIEW_PROXY_KEY": TOKEN, "PIXEL_INGRESS_SOCKET": absent,
                           "PIXEL_PREVIEW_SOCKET": absent, "PIXEL_PREVIEW_ORIGIN_PORT": str(self.port)})
        import pixel_edge
        self.pe = importlib.reload(pixel_edge)
        self.edge_runner = web.AppRunner(self.pe.create_app())
        await self.edge_runner.setup()
        edge = web.TCPSite(self.edge_runner, "127.0.0.1", 0)
        await edge.start()
        self.edge = f"http://127.0.0.1:{edge._server.sockets[0].getsockname()[1]}"
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.edge_runner.cleanup()
        await self.up_runner.cleanup()
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    async def test_snapshot_bytes_come_from_the_site_origin_with_the_proxy_token(self):
        async with self.client.get(f"{self.edge}/preview/{self.site}/", headers={"Authorization": f"Bearer {TOKEN}"}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), self.body)
            self.assertIn("sandbox", response.headers["Content-Security-Policy"])
        self.assertEqual(self.hosts, [f"{self.site}.localhost:{self.port}"])

    async def test_the_relay_still_requires_the_proxy_token(self):
        async with self.client.get(f"{self.edge}/preview/{self.site}/") as response:
            self.assertEqual(response.status, 401)
        self.assertEqual(self.hosts, [])


if __name__ == "__main__":
    unittest.main()
