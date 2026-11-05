# ODS Desktop phone access (prototype)

Use Portal and the dashboard on your phone while the model keeps running on your
Windows PC. Prototype for the Windows Desktop build only; not part of setup yet.

## How it works

```
phone --(Tailscale: WireGuard, your devices only)--> PC: Tailscale Serve (HTTPS)
      --> ODS on 127.0.0.1:3101 --> Portal --> llama.cpp on the PC's GPU or CPU
```

- ODS keeps listening on `127.0.0.1` only. Tailscale Serve forwards your tailnet's
  HTTPS address to it.
- A request that arrives through Tailscale carries a non-loopback `Host` and
  forwarding headers, so the Desktop gate (`native_front.py`) treats it as remote:
  it must present a signed `ods-dashboard-session` cookie. The phone's sign-in
  screen asks for one.
- `phone_link.py` asks the local ODS for a one-time sign-in link
  (`POST /api/auth/dashboard-session/link`, the same endpoint as `ods dashboard-login`),
  shows it as a QR code, and the phone signs in with it. The link works once for
  10 minutes; the session lasts 30 days. The token travels in the URL fragment
  (`#ods-login=`), so it never reaches server or proxy logs.
- The QR code is generated locally (byte mode, level L, versions 1 to 10) so the
  token is never handed to a website or a third-party script.

No ODS code changes are needed; this only uses what the build already has.

## Set up (once per PC)

1. Install Tailscale on the PC and the phone, signed in to the same account
   (tailscale.com/download).
2. On the PC, in PowerShell:
   ```
   & "C:\Program Files\Tailscale\tailscale.exe" serve --bg 3101
   ```
   Use your ODS port if it is not 3101 (see `%LOCALAPPDATA%\ODS\desktop\settings.json`).
   If it says Serve or HTTPS is not enabled for your tailnet, open the link it
   prints, enable it, and run it again.
3. Sign in a phone:
   ```
   & "$env:LOCALAPPDATA\ODS\desktop\python\python.exe" ods\installers\windows\phone-access\phone_link.py
   ```
   Scan the QR code with Tailscale on on the phone. `--test-qr` shows a harmless
   test code (no token) to check that your phone can read the QR codes.

Undo: `tailscale serve reset` stops the forwarding. Changing the dashboard
password signs every device out.

## Known problems

- **Networks with TLS inspection (for example Fortinet firewalls)** break Tailscale
  sign-in: `x509: certificate signed by unknown authority` while fetching
  `controlplane.tailscale.com`, and Tailscale's health check names the firewall.
  Use another network (a phone hotspot or home Wi-Fi). Never work around the
  certificate check.
- The PC must be on and awake, with ODS and Tailscale running.
- Anyone holding the signed-in phone can drive Portal on the PC (Portal runs
  commands in its sandbox). Treat the phone like the PC's keyboard.

## Verified (2026-11-03 and 2026-11-04, RTX 3050 laptop + Galaxy Z Flip6)

- Through Tailscale Serve: HTTPS with a valid certificate; without a session the
  API answers `dashboard_sign_in_required`.
- One-time link: sign-in gives a `Secure; HttpOnly; SameSite=Strict` 30-day
  session; the same link again is refused; a forged cookie is refused.
- The phone signed in by QR and ran a Portal turn; the PC's logs show the turn
  went to `http://127.0.0.1:8080` (the local llama.cpp, 28 GPU layers).

## Open questions for a real version

- How Portal's layout, streaming and previews behave on a phone screen.
- A "Sign in a phone" button in the dashboard instead of a script.
- Whether to support plain same-Wi-Fi access (needs a second listener, plain
  HTTP and a firewall prompt), or Tailscale only.
- Sign-out and device list for remote sessions on Desktop (the dashboard hides
  them today because Desktop reports `remoteAccess: false`).
