# Dream Server Validation Matrix

Last updated: 2026-05-20

This page describes the real hardware and workflow coverage used to validate
Dream Server between changes. It is intentionally sanitized: it publishes
hardware classes, operating systems, GPU paths, and test phases without private
hostnames, LAN addresses, usernames, or filesystem paths.

## Hardware Surface

| Test surface | OS family | Architecture | Accelerator path | Memory class | Fleet role |
|---|---|---:|---|---:|---|
| Linux NVIDIA workstation | Ubuntu 24.04 | x86_64 | Dual high-memory Blackwell CUDA GPUs | 90 GB+ VRAM per GPU | Primary multi-GPU CUDA install, dashboard, UI, and capability target |
| Linux AMD unified-memory workstation | Ubuntu 24.04 | x86_64 | AMD Strix Halo / ROCm-Lemonade path | 120 GB+ unified | Primary AMD install/runtime validation target |
| Linux NVIDIA unified-memory appliance | NVIDIA Ubuntu derivative | aarch64 | Grace Blackwell / CUDA path | 120 GB+ unified | ARM Linux + NVIDIA appliance validation target |
| macOS constrained Apple Silicon | macOS | arm64 | Native Metal inference + Docker services | 16 GB unified | Smoke gate and tight-memory macOS validation target |
| macOS high-memory Apple Silicon | macOS | arm64 | Native Metal inference + Docker services | 120 GB+ unified | Large-model macOS validation target |
| Windows hybrid GPU laptop | Windows 11 + Docker Desktop/WSL2 | x86_64 | NVIDIA mobile GPU plus Intel Arc integrated GPU | 32 GB+ system RAM | Windows installer, Docker Desktop, WSL2, and mobile-GPU validation target |

This standing fleet is the repeatable release surface: it can run in parallel
whenever installer, bootstrap, dashboard, agent, model, or extension code
changes. Community and volunteer testers add broader coverage on other GPUs,
distros, operating-system versions, storage layouts, and network environments,
but those reports are complementary evidence rather than the always-on release
gate.

## Fleet Phases

The private fleet harness runs these phases and records structured artifacts for
each host where the phase is applicable.

| Phase | What it proves | Normal cadence |
|---|---|---|
| Regression replay | Previously fixed fleet bugs have not returned | Every full fleet run |
| Smoke gate | The smallest Apple Silicon target can fresh-install and pass core health before the larger fleet starts | Every full fleet run |
| Preflight | OS, RAM, disk, Docker, firewall, port conflicts, prior install state | Every install run |
| Fresh install | The public bootstrap path can nuke prior state and install non-interactively | Every full fleet run |
| Core verify | Dashboard API, dashboard UI, llama-server models/chat, and Hermes proxy are reachable | Every post-install run |
| Dashboard API flows | Model listing, model download/switch, and extension install state transitions | Every post-install run |
| Hermes auth/chat | Magic-link session auth, gated Hermes access, and seed echo through the agent path | Every post-install run |
| Browser UI | Dashboard navigation, model/extension surfaces, and Open WebUI model proxy behavior | Default UI target every run; scheduled wider UI sweeps |
| Capability probes | Chat coherence, web search, file write/read, code execution, skills list, and loaded-model identity | Every post-install run, with LLM probes deferred while bootstrap is still active |
| Lifecycle | Reinstall, restart, and doctor checks after state changes | Release-candidate or explicit lifecycle runs |

## Representative Evidence

Recent fleet runs on 2026-05-20 exercised the Linux NVIDIA, Linux AMD, Linux
ARM NVIDIA, constrained macOS, and high-memory macOS surfaces with fresh
install, verify, dashboard, Hermes, and capability phases. The harness also
surfaced an external DNS/download failure on one macOS run, which is evidence
that environment failures are visible rather than silently converted into
product passes.

The Windows laptop is part of the validation surface so Windows + Docker
Desktop/WSL2 + mobile NVIDIA/Intel hybrid GPU behavior is not inferred from
Linux or macOS results. Treat Windows evidence as release-relevant only when
the Windows target produces preflight, install, verify, dashboard, and UI
artifacts for the release candidate being claimed.

## What This Proves

- The installer is repeatedly exercised on real machines, not only CI
  containers.
- The release path covers heterogeneous GPU vendors, memory sizes, operating
  systems, and CPU architectures.
- The harness records environment state before install so firewall, Docker,
  disk, DNS, and port issues can be separated from product bugs.
- The user-facing path is tested beyond service liveness: dashboard actions,
  model switching, Hermes auth, agent capabilities, and regression fixtures are
  part of the gate.

## What This Does Not Claim

- Every Linux distribution is exhaustively installed on real hardware for every
  change. CI and distrobox cover broader distro logic; the fleet covers the
  high-value hardware paths.
- OS and distro rotation is periodic because reprovisioning real machines is
  intentionally slower than running the standing fleet. Release notes should
  call out any rotated distro or OS image that was included for that candidate.
- Intel Arc is still experimental unless a release cites a successful Arc fleet
  run for that release candidate.
- AP-mode and packaged appliance handoff still require target-image validation
  because router, Wi-Fi, mDNS, and client-device behavior vary.
- A fresh fleet pass is not a long-term soak test. Bench, thermal, and
  overnight stability runs are separate evidence.

## Release Readiness Receipt

Before a release is described as ready, the release notes should cite:

- the Dream Server version and matching Git tag or release;
- the fleet run date and sanitized hardware classes covered;
- regression replay result;
- install/verify/dashboard/Hermes/capability result summary;
- any skipped, deferred, blocked-by-environment, or not-run phases;
- known gaps that should not be read as supported behavior.

The version signal should be internally consistent before publication:
`manifest.json`, the changelog section, the Git tag/release, and any release
notes should all name the same version. If a candidate has not been tagged yet,
describe it as unreleased or release-candidate evidence rather than as a shipped
stable release.
