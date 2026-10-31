import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { previewSocketPath, socketRequest } from "../plugin/workspace-preview.mjs";

const SID = "S-1-5-21-1854970894-4250033066-4162452771-1001";
const PIPE = `\\\\.\\pipe\\ods-pixel-preview-${SID}`;

test("the Windows preview pipe comes from the Portal supervisor and nowhere else", () => {
  assert.equal(previewSocketPath("win32", { ODS_PIXEL_PREVIEW_PIPE: PIPE }), PIPE);
  assert.equal(previewSocketPath("linux", { ODS_PIXEL_PREVIEW_PIPE: PIPE }), "/run/ods-pixel-preview/control.sock");
  for (const value of [undefined, "", "C:\\preview.sock", "\\\\.\\pipe\\other", `${PIPE}x`, `\\\\server\\pipe\\ods-pixel-preview-${SID}`]) {
    assert.throws(() => previewSocketPath("win32", value === undefined ? {} : { ODS_PIXEL_PREVIEW_PIPE: value }),
      /Windows preview pipe is not configured/);
  }
});

test("the win32 request writes its line without a half-close and reads the reply", async () => {
  // The service answers as soon as the framed line arrives; a Windows named
  // pipe never delivers the half-close end() would send.
  const address = process.platform === "win32"
    ? `\\\\.\\pipe\\ods-preview-test-${process.pid}-${Date.now()}`
    : path.join(mkdtempSync(path.join(tmpdir(), "ods-preview-")), "control.sock");
  let replied = false;
  let halfClosedBeforeReply = false;
  const server = net.createServer({ allowHalfOpen: true }, (connection) => {
    let text = "";
    connection.on("end", () => { if (!replied) halfClosedBeforeReply = true; });
    connection.on("data", (chunk) => {
      text += chunk;
      if (!text.endsWith("\n")) return;
      replied = true;
      connection.end(JSON.stringify({ echoed: JSON.parse(text).action }) + "\n");
    });
  });
  await new Promise((resolve) => server.listen(address, resolve));
  try {
    const reply = await socketRequest({ action: "publish" }, { socketPath: address, platform: "win32" });
    assert.deepEqual(reply, { echoed: "publish" });
    assert.equal(halfClosedBeforeReply, false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("the Windows preview service satisfies the plugin's own tool and verifier", { skip: process.platform !== "win32" }, async () => {
  const { spawn } = await import("node:child_process");
  const { writeFileSync, mkdirSync } = await import("node:fs");
  const { createWorkspacePreviewTool, createWorkspacePreviewVerifier } = await import("../plugin/workspace-preview.mjs");
  const root = mkdtempSync(path.join(tmpdir(), "ods-preview-service-"));
  const workspace = path.join(root, "workspace");
  mkdirSync(path.join(workspace, "game"), { recursive: true });
  mkdirSync(path.join(workspace, "empty"), { recursive: true });
  writeFileSync(path.join(workspace, "game", "index.html"), "<!doctype html><button>0</button><script src=app.js></script>");
  writeFileSync(path.join(workspace, "game", "app.js"), "let n = 0;");
  writeFileSync(path.join(workspace, "game", ".env"), "SECRET=1");
  const pipe = `\\\\.\\pipe\\ods-preview-service-test-${process.pid}`;
  const port = 20000 + (process.pid % 20000);
  const service = spawn(process.env.ODS_TEST_PYTHON || "python", ["-I", new URL("../windows/preview_service.py", import.meta.url).pathname.replace(/^\//, ""),
    pipe, workspace, path.join(root, "previews"), String(port)], { stdio: ["ignore", "pipe", "inherit"] });
  try {
    await new Promise((resolve, reject) => {
      service.stdout.on("data", (chunk) => { if (String(chunk).includes("preview ready")) resolve(); });
      service.on("exit", (code) => reject(new Error(`preview service exited ${code}`)));
    });
    const request = (payload, options) => socketRequest(payload, { ...options, socketPath: pipe, platform: "win32" });
    const tool = createWorkspacePreviewTool({ request });
    const published = await tool.execute("t1", { relativeDirectory: "game" });
    assert.match(published.content[0].text, /ODS independently published and read back 2 workspace static files/);
    assert.ok(!published.details.publishedPaths.includes(".env"));
    const missing = await tool.execute("t2", { relativeDirectory: "empty" });
    assert.doesNotMatch(missing.content[0].text, /could not publish a verified browser preview/, "a folder problem is reported as such, not as an unavailable service");
    const verify = createWorkspacePreviewVerifier({ request });
    assert.equal(await verify(published.details), true);
    writeFileSync(path.join(workspace, "game", "app.js"), "let n = 1;");
    assert.equal(await verify(published.details), false);
  } finally {
    service.kill();
  }
});
