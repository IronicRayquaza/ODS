// Pure helper: which runtime this dashboard is served by, from /api/status.
// "windows-desktop" is dashboard-api running natively on Windows with its own
// llama-server: no Docker, no WSL and no host agent. Its Portal (the native
// OpenClaw agent) is optional; portalEnabled says whether this install runs it.
export const WINDOWS_DESKTOP = 'windows-desktop'

export function isWindowsDesktop(status) {
  return status?.runtimeProfile === WINDOWS_DESKTOP
}

export function hasPortal(status) {
  return !isWindowsDesktop(status) || status?.portalEnabled === true
}
