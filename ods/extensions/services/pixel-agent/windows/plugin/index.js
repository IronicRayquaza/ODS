// ODS Windows sandbox backend for the Desktop Portal (windows-desktop profile).
//
// Registers the "ods-windows" OpenClaw sandbox backend through the public
// plugin SDK. Every agent command and file operation runs through
// sandbox_run.py inside a per-account Windows AppContainer: no network
// (loopback included), the Portal workspace writable, the toolchain
// read-only, nothing else of the user profile, all in a kill-on-close Job
// Object. OpenClaw spawns the argv buildExecSpec returns; no OpenClaw module
// is patched.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { registerSandboxBackend } from 'openclaw/plugin-sdk/sandbox';
import { unwrapCancellable } from './cancellable.mjs';

const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;

export function readSettings(config) {
  const value = config?.plugins?.entries?.['ods-windows-sandbox']?.config ?? {};
  for (const key of ['python', 'runner', 'profile', 'toolPath', 'controlRoot']) {
    if (typeof value[key] !== 'string' || !value[key]) throw new Error(`ods-windows-sandbox needs config.${key}`);
  }
  for (const key of ['python', 'runner', 'controlRoot']) {
    if (!path.win32.isAbsolute(value[key])) throw new Error(`ods-windows-sandbox config.${key} must be absolute`);
  }
  return value;
}

export function execArgv(settings, workspace, workdir, command) {
  const { text, marker } = unwrapCancellable(command, settings.controlRoot);
  return [settings.python, settings.runner, 'run', '--profile', settings.profile, '--workspace', workspace,
    '--path', settings.toolPath, '--cwd', workdir || workspace, ...(marker ? ['--cancel-marker', marker] : []), '--', text];
}

function collect(argv, { stdin, env, signal } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(argv[0], argv.slice(1), { windowsHide: true, signal, env: { ...process.env, ...env } });
    const out = []; const err = []; let size = 0;
    const take = (list) => (chunk) => {
      size += chunk.length;
      if (size > MAX_OUTPUT_BYTES) { child.kill(); reject(new Error('sandbox output is too large')); return; }
      list.push(chunk);
    };
    child.stdout.on('data', take(out));
    child.stderr.on('data', take(err));
    child.on('error', reject);
    child.on('close', (code) => resolve({ stdout: Buffer.concat(out), stderr: Buffer.concat(err), code: code ?? 1 }));
    child.stdin.end(stdin ?? undefined);
  });
}

export function createFsBridge(settings, workspace) {
  const resolvePath = ({ filePath, cwd }) => {
    const hostPath = path.win32.resolve(cwd || workspace, filePath);
    return { hostPath, relativePath: path.win32.relative(workspace, hostPath), containerPath: hostPath };
  };
  const op = async (request, data, signal) => {
    const result = await collect([settings.python, settings.runner, 'fsop', '--profile', settings.profile,
      '--workspace', workspace, '--python', settings.python], { stdin: data, env: { ODS_FS_REQUEST: JSON.stringify(request) }, signal });
    if (result.code !== 0) {
      const reason = result.stderr.toString('utf8').trim().split(/\r?\n/).pop() || `exit ${result.code}`;
      throw new Error(`The sandbox refused ${request.op} ${request.path}: ${reason}`);
    }
    return result.stdout;
  };
  return {
    resolvePath,
    readFile: async (p) => op({ op: 'read', path: resolvePath(p).hostPath }, null, p.signal),
    writeFile: async (p) => {
      const data = Buffer.isBuffer(p.data) ? p.data : Buffer.from(p.data, p.encoding ?? 'utf8');
      await op({ op: 'write', path: resolvePath(p).hostPath, mkdir: p.mkdir !== false }, data, p.signal);
    },
    mkdirp: async (p) => { await op({ op: 'mkdirp', path: resolvePath(p).hostPath }, null, p.signal); },
    remove: async (p) => {
      await op({ op: 'remove', path: resolvePath(p).hostPath, recursive: Boolean(p.recursive), force: Boolean(p.force) }, null, p.signal);
    },
    rename: async (p) => {
      await op({ op: 'rename', path: resolvePath({ filePath: p.from, cwd: p.cwd }).hostPath,
        to: resolvePath({ filePath: p.to, cwd: p.cwd }).hostPath }, null, p.signal);
    },
    stat: async (p) => JSON.parse((await op({ op: 'stat', path: resolvePath(p).hostPath }, null, p.signal)).toString('utf8') || 'null'),
  };
}

export default definePluginEntry({
  id: 'ods-windows-sandbox',
  name: 'ODS Windows Sandbox',
  description: 'Runs Portal commands in a Windows AppContainer with no network.',
  register(api) {
    if (api.registrationMode === 'discovery') return;
    const settings = readSettings(api.config);
    registerSandboxBackend('ods-windows', {
      factory: async (params) => ({
        id: 'ods-windows',
        runtimeId: `ods-windows-${params.scopeKey}`,
        runtimeLabel: 'Windows AppContainer (no network)',
        workdir: params.workspaceDir,
        async buildExecSpec({ command, workdir }) {
          return { argv: execArgv(settings, params.workspaceDir, workdir, command),
            env: { SystemRoot: process.env.SystemRoot, LOCALAPPDATA: process.env.LOCALAPPDATA, TEMP: process.env.TEMP },
            stdinMode: 'pipe-closed' };
        },
        async runShellCommand({ script, args = [], stdin, allowFailure, signal }) {
          const result = await collect(execArgv(settings, params.workspaceDir, params.workspaceDir, [script, ...args].join(' ')),
            { stdin, signal });
          if (result.code !== 0 && !allowFailure) throw new Error(`sandbox command failed (exit ${result.code})`);
          return result;
        },
        createFsBridge: () => createFsBridge(settings, params.workspaceDir),
      }),
      resolveWorkdir: (params) => params.workspaceDir,
    });
  },
});
