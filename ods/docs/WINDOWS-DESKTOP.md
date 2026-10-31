# ODS Windows Desktop (preview)

ODS Windows Desktop runs the real ODS dashboard and a local AI model directly
on Windows: **no Docker, no WSL, no administrator rights**. It installs for
your Windows account only, starts by itself when you sign in, and listens on
`127.0.0.1` only, so nothing leaves the PC. The design and its review steps
are tracked in [RFC #7968](https://github.com/Osmantic/ODS/issues/7968).

This is an opt-in preview. `.\install.ps1` and the
[Docker Desktop + WSL2 path](WINDOWS-QUICKSTART.md) are unchanged.

## What you get today

- **Local chat** in the ODS window: answers stream as they are written,
  follow-up questions keep their context, chats are saved in the window and
  can be saved as Markdown or JSON.
- **The model the ODS catalog picks for your PC**, run by the pinned
  llama.cpp build: on an AMD Radeon GPU over Vulkan when llama.cpp qualifies
  it, otherwise on the CPU. Setup says which, and why.
- **A Start menu and desktop icon** that opens ODS in its own Microsoft Edge
  app window (or your default browser when Edge is missing). Right after you
  sign in it shows "ODS is starting" for a few seconds, then opens by itself.
- **Portal, the ODS agent**: the real OpenClaw agent with the ODS plugin,
  running natively. It plans, writes files and runs commands, and every
  command runs in a Windows sandbox (see [Portal](#portal)). Websites it
  builds open as previews in the ODS window.
- **Native apps** from the extension catalog where a Windows recipe exists:
  Qdrant (vector database) today, installed with `-Apps qdrant`.
- **An Apps & Features entry** to remove it, which asks before deleting your data.

Not in the preview yet: switching models from the dashboard (rerun setup
instead), NVIDIA and Intel GPUs (they use the CPU for now), installing apps
from the Extensions page, voice and remote access.

## Requirements

| | |
|---|---|
| Windows | Windows 10 version 1803 or later, or Windows 11, on an Intel or AMD (x64) processor |
| Memory | 8 GB minimum; 16 GB recommended |
| GPU (optional) | An AMD Radeon GPU with a current Adrenalin driver runs the model over Vulkan; without one, the CPU does |
| Disk | About 6 GB free on the drive that holds your user profile |
| Account | A normal (non-Administrator) PowerShell window |
| Internet | For the first setup only (llama.cpp, Python, the model) |

The model engine needs the Microsoft Visual C++ runtime (2015-2022, version
14.40 or newer). Most PCs already have it. If yours does not, setup offers to
install it from microsoft.com; Windows asks for permission once, and setup
runs the installer only after Windows confirms it is signed by Microsoft.

## Install (testers)

Until the built dashboard ships as a release file, build it once with Node.js
from an ODS checkout that contains the Desktop branches:

```powershell
cd ods\extensions\services\dashboard
npm ci
npm run build
cd ..\..\..\..
powershell -NoProfile -ExecutionPolicy Bypass -File ods\installers\windows-desktop.ps1
```

Setup runs seven steps and says what each one does: checks, the Visual C++
runtime, the model engine, the AI model, starting the model, Portal, and the
ODS app.
The model is the large download; if it is interrupted, run the same command
again and it continues where it stopped. At the end ODS opens in its window.

Run the same command again at any time to repair or update. It keeps your
model, port and chats. Add `-Repair` to re-check every file hash, `-Port <n>`
to choose the window's port, `-ContextSize <tokens>` to choose the model's
context, `-Device gpu` to try an NVIDIA GPU (or `-Device cpu` to use none), `-NoPortal` to leave Portal out, `-Apps qdrant` to add native apps,
and `-NonInteractive` for a scripted run (it never installs prerequisites; it
explains what is missing instead).

## Where things live

| Path | What |
|---|---|
| `%LOCALAPPDATA%\ODS\desktop` | The ODS app, its Python, launcher, logs and data |
| `%LOCALAPPDATA%\ODS\lemonade\models` | The AI model (the folder name is shared with the WSL Portal path) |
| `%LOCALAPPDATA%\ODS\lemonade\portal-runtime` | The model server's plan, private key and log |
| `%LOCALAPPDATA%\ODS\llama.cpp` | The pinned llama.cpp build |
| `%LOCALAPPDATA%\ODS\portal` | Portal: its Node, OpenClaw, settings, logs, previews, and `workspace` (the agent's projects) |
| `%LOCALAPPDATA%\ODS\apps\<app>` | A native app: its files, `data` and logs |

Sign-in tasks run them, as you, with limited rights:
`ODSLlamaServerRuntime-<your SID>` (the model), `ODSDesktopApi-<your SID>`
(the app), `ODSPortal-<your SID>` (Portal) and `ODSApp-<app>-<your SID>` (each
native app).

## Remove

Use **Settings > Apps > Installed apps > ODS Desktop (preview)**, or run
`%LOCALAPPDATA%\ODS\desktop\uninstall.ps1` with `-KeepData` or `-RemoveData`.
Uninstall stops only what ODS Desktop started, removes its tasks and the
shortcuts it made (not ones you changed), and then shows each data folder with
its size and asks whether to delete it. Portal's workspace and chat history
and each app's data count as your data.

Chats are saved by the ODS window itself, as Microsoft Edge site data for
`localhost`, not in these folders. Reinstalling on the same port shows them
again. To erase them, delete the site data for `localhost` in Edge
(Settings > Cookies and site permissions).

## Portal

Portal runs the same agent as the Linux and WSL installs, with no Docker:

- **One sign-in task** starts, in order, the preview service, the OpenClaw
  gateway (`127.0.0.1:18789`), the ingress (a named pipe only your account can
  write to) and the Pixel edge (`127.0.0.1:9595`). They share a Windows Job
  Object, so they stop together.
- **Every command the agent runs** goes to a Windows AppContainer created for
  your account: no network at all (not even `127.0.0.1`, so it cannot reach the
  model, the app or Portal's key), write access to Portal's workspace only,
  read access to its Node and Python, nothing else in your profile. Cancelling
  a task ends every process the command started.
- **The sandbox shell is `cmd.exe`**, with `node` and `python`. There is no
  git, bash or package installer in it, and programs there cannot start child
  processes with piped output, so `node --test` runs its files in-process
  (`--experimental-test-isolation=none`). The agent is told all of this.
- **Previews** are snapshots of the agent's workspace folder, served on
  `127.0.0.1:18791`, each site on its own `site-<id>.localhost` address under a
  strict Content-Security-Policy. Links that lead outside the workspace and
  files such as `.env` or keys are never published.
- **Context:** with Portal, the CPU route uses 32K tokens of context. A small
  multi-step task (write a page, test it, publish it) needed 20-25K.

Portal is not yet available: web search, Full Access mode, headless preview
inspection, and image input.

## Living alongside other ODS installs

- **WSL Portal on the same PC:** Desktop never stops or replaces a model
  runtime a WSL Portal set up; setup stops and says so.
- **Docker-based ODS:** both can be installed. Desktop uses its own ports
  (the window tries 3101, then 13101 and 23101).

## Security

- Everything listens on `127.0.0.1` or a per-user named pipe. Portal's agent
  commands run in an AppContainer with no network. The model server needs a
  key that only the app and Portal hold; the dashboard is served behind the same loopback rules
  nginx applies in the Docker stack, so another website cannot drive it.
- Downloads are pinned by size and SHA-256 (llama.cpp, Python, pip, Node,
  native apps), by SHA-256 and SHA-512 (OpenClaw, installed without running
  package scripts) or by the catalog's SHA-256 (the model). Python packages install from the hash-locked
  `requirements.lock`, wheels only. The native path does not use LiteLLM.
- Private files (keys, plans, the app environment) are readable by your
  account only.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "This window is running as Administrator" | Open PowerShell normally (not "Run as administrator") and run setup again. |
| "The Microsoft Visual C++ runtime is not installed" | Let setup install it, or install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and rerun. |
| "No free port for the ODS window" | Close the program using it, or rerun with `-Port <free port>`. |
| The model download stopped | Rerun setup; it resumes. |
| Smart App Control or antivirus blocked llama.cpp | Setup names the policy; allow the pinned llama.cpp files or rerun after a restart. |
| The window says "ODS is starting" | Normal for a few seconds after sign-in; it opens by itself. After two minutes it says so: restart the PC, or rerun setup. |
| The window says the model is still loading or stopped | Wait a minute after sign-in, or rerun setup. |

Logs: `%LOCALAPPDATA%\ODS\desktop\logs` (the app),
`%LOCALAPPDATA%\ODS\portal\logs` (Portal),
`%LOCALAPPDATA%\ODS\apps\<app>\logs` (native apps) and
`%LOCALAPPDATA%\ODS\lemonade\portal-runtime\llama-server.log` (the model).

## Known limits

- **CPU threads:** llama.cpp picks its own thread count. On PCs with many
  cores it can use more threads than is fastest; a `--threads` option needs a
  change to the shared llama.cpp launcher and is discussed in RFC #7968.
- **GPU:** AMD Radeon GPUs are used by default, the route ODS already
  qualifies natively on Windows. An NVIDIA GPU is used only with `-Device gpu`:
  llama.cpp runs over Vulkan with as many layers as the card holds, and setup
  keeps the GPU only if the model then answers a test question correctly
  (otherwise it says so and uses the CPU). On a 4 GB RTX 3050 Laptop GPU with
  32K of context this measured about 2.5 times the CPU's prompt speed and twice
  its writing speed. Without the option the model runs on the CPU. When llama.cpp lists no usable Vulkan device
  (an old driver), setup says so and uses the CPU; update the driver and run
  setup again. A rerun keeps the model and context you already have.
- **Speed of Portal on the CPU:** the agent's instructions are about 6K
  tokens, so the first Portal task after the model starts reads them for a few
  minutes on a typical laptop CPU; later turns reuse them and start in seconds.
  A multi-step task can take many minutes.
- **Context:** the CPU route uses up to 16K tokens, or 32K with Portal; the GPU
  route the catalog's context for the GPU's memory. `-ContextSize` overrides
  both, also on a rerun. Older parts of a long chat
  stay saved and on screen; the model sees the most recent part, and the chat
  says so.
