// Native Windows ingress for the ODS Desktop Portal (windows-desktop profile).
//
// The same request handling as Linux/WSL (createIngressServer: body allowlist,
// forced openclaw/default model, hashed session user, chat history, cancel and
// activity), served on a per-user named pipe instead of a group-gated Unix
// socket. The gateway token is injected on the last hop only and never leaves
// this process.
//
// Boundaries:
// - Named pipe, never TCP. Windows gives a new pipe a default DACL with full
//   control for the creator, SYSTEM and Administrators and read-only access
//   for Everyone, so another account cannot write a request into it.
// - The token file is the gateway's openclaw.json under the owner-only Portal
//   home. Node reports every Windows file as mode 0o666, so the POSIX owner
//   and mode checks in readGatewayConfiguration cannot apply; setup sets and
//   verifies the owner-only DACL instead, and this reader refuses links.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createIngressServer, gatewayRuntimeFromConfig } from "./pixel_ingress.mjs";
import { createChatHistoryLedger } from "./chat_history_ledger.mjs";
import { createChatImageStore } from "./chat_image_store.mjs";

const PIPE_NAME = /^\\\\\.\\pipe\\ods-pixel-ingress-S-1-5-21-\d+-\d+-\d+-\d+$/;
const MAX_CONFIG_BYTES = 2 * 1024 * 1024;
const MAX_TOKEN_LENGTH = 4096;

export function windowsIngressConfig(env = process.env) {
  const pipe = env.PIXEL_INGRESS_PIPE || "";
  if (!PIPE_NAME.test(pipe)) throw new Error("PIXEL_INGRESS_PIPE must be \\\\.\\pipe\\ods-pixel-ingress-<user SID>");
  const gatewayPort = Number(env.PIXEL_GATEWAY_PORT || "18789");
  if (!Number.isInteger(gatewayPort) || gatewayPort < 1 || gatewayPort > 65535) throw new Error("PIXEL_GATEWAY_PORT is invalid");
  const configFile = env.PIXEL_GATEWAY_CONFIG || "";
  const chatStateDir = env.PIXEL_CHAT_STATE_DIR || "";
  if (!path.win32.isAbsolute(configFile) || !path.win32.isAbsolute(chatStateDir)) {
    throw new Error("PIXEL_GATEWAY_CONFIG and PIXEL_CHAT_STATE_DIR must be absolute paths");
  }
  return { pipe, gatewayPort, configFile, chatStateDir };
}

export function readWindowsGatewayToken(file) {
  const info = fs.lstatSync(file);
  if (info.isSymbolicLink() || !info.isFile()) throw new Error("gateway config must be a regular file");
  if (info.size > MAX_CONFIG_BYTES) throw new Error("gateway config is too large");
  const config = JSON.parse(fs.readFileSync(file, "utf8"));
  const token = config?.gateway?.auth?.token;
  if (typeof token !== "string" || !token || token.length > MAX_TOKEN_LENGTH || token.trim() !== token
      || /[\x00-\x1f\x7f]/.test(token)) {
    throw new Error("gateway token missing or invalid");
  }
  if (config?.gateway?.bind !== "loopback") throw new Error("the Portal gateway must bind loopback");
  return { token, runtime: gatewayRuntimeFromConfig(config) };
}

export async function startWindowsIngress(cfg = windowsIngressConfig()) {
  const { token } = readWindowsGatewayToken(cfg.configFile);
  const historyLedger = createChatHistoryLedger(cfg.chatStateDir);
  const imageStore = createChatImageStore(path.join(cfg.chatStateDir, "images"));
  const server = createIngressServer({ token, gatewayPort: cfg.gatewayPort, historyLedger, imageStore });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(cfg.pipe, () => { server.off("error", reject); resolve(); });
  });
  return server;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  const server = await startWindowsIngress();
  console.log("pixel-ingress ready (named pipe)");
  const shutdown = () => server.close(() => process.exit(0));
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}
