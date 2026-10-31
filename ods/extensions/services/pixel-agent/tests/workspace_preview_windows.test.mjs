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
