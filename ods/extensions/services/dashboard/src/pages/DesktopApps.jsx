import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react'

// Windows Desktop: apps that run natively from their manifest recipe (no
// Docker). Setup installs them (-Apps <id>); this page shows what runs and
// opens it. The list comes from GET /api/desktop/apps, which only reads.

const STATE = {
  running: { label: 'Running', className: 'text-emerald-400' },
  installed: { label: 'Installed, not answering', className: 'text-amber-400' },
  available: { label: 'Not installed', className: 'text-theme-text-muted' },
}

export default function DesktopApps() {
  const [apps, setApps] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const response = await fetch('/api/desktop/apps', { cache: 'no-store' })
      if (!response.ok) throw new Error(`The app list did not load (HTTP ${response.status}).`)
      setApps((await response.json()).apps)
    } catch (failure) {
      setError(failure.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 text-theme-text">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Apps</h1>
          <p className="mt-1 text-sm text-theme-text-muted">Run natively on this PC, on 127.0.0.1 only. No Docker, no WSL.</p>
        </div>
        <button type="button" onClick={load} aria-label="Refresh the app list"
          className="inline-flex items-center gap-2 rounded-lg border border-theme-border px-3 py-2 text-sm hover:bg-theme-card">
          <RefreshCw size={16} aria-hidden="true" /> Refresh
        </button>
      </div>
      {error && <p role="alert" className="mt-6 rounded-lg border border-theme-border p-4 text-sm text-amber-400">{error}</p>}
      {!apps && !error && <p className="mt-6 inline-flex items-center gap-2 text-sm text-theme-text-muted"><Loader2 size={16} className="animate-spin" aria-hidden="true" /> Loading apps…</p>}
      <ul className="mt-6 space-y-3">
        {(apps || []).map(app => {
          const state = STATE[app.state] || STATE.available
          return (
            <li key={app.id} className="rounded-xl border border-theme-border bg-theme-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="font-medium">{app.name}</h2>
                  <p className="text-sm text-theme-text-muted">
                    Version {app.installedVersion || app.version} · <span className={state.className}>{state.label}</span>
                  </p>
                </div>
                {app.url && (
                  <a href={app.url} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-2 rounded-lg bg-theme-accent px-3 py-2 text-sm font-medium text-zinc-950">
                    Open <ExternalLink size={16} aria-hidden="true" />
                  </a>
                )}
              </div>
              {app.apiUrl && (
                <p className="mt-3 text-sm text-theme-text-muted">
                  OpenAI-compatible API at <code className="rounded bg-black/30 px-1.5 py-0.5">{app.apiUrl}</code>. Its key is the <code className="rounded bg-black/30 px-1.5 py-0.5">api-key</code> file in the app's data folder.
                </p>
              )}
              {app.state === 'available' && (
                <p className="mt-3 text-sm text-theme-text-muted">
                  To install, run ODS setup with <code className="rounded bg-black/30 px-1.5 py-0.5">-Apps {app.id}</code>.
                </p>
              )}
            </li>
          )
        })}
      </ul>
      {apps && apps.length === 0 && <p className="mt-6 text-sm text-theme-text-muted">No app has a native Windows recipe yet.</p>}
    </div>
  )
}
