import { useState, useEffect, useCallback, useRef } from 'react'

// Mock data for development/demo - gated behind VITE_USE_MOCK_DATA env var
const USE_MOCK_DATA = import.meta.env.VITE_USE_MOCK_DATA === 'true'

function getMockModels() {
  return [
    {
      id: 'Qwen/Qwen2.5-32B-Instruct-AWQ',
      name: 'Qwen2.5 32B AWQ',
      size: '15.7 GB',
      sizeGb: 15.7,
      vramRequired: 14,
      contextLength: 32768,
      specialty: 'General',
      description: 'High-quality general purpose, recommended for most users',
      tokensPerSec: 54,
      quantization: 'AWQ',
      status: 'loaded',
      fitsVram: true,
      fitsCurrentVram: false
    },
    {
      id: 'Qwen/Qwen2.5-7B-Instruct',
      name: 'Qwen2.5 7B',
      size: '4.2 GB',
      sizeGb: 4.2,
      vramRequired: 6,
      contextLength: 32768,
      specialty: 'Fast',
      description: 'Fast general-purpose model, good for simple tasks',
      tokensPerSec: 120,
      quantization: null,
      status: 'available',
      fitsVram: true,
      fitsCurrentVram: true
    },
    {
      id: 'Qwen/Qwen2.5-32B-Instruct-AWQ',
      name: 'Qwen2.5 Coder 32B AWQ',
      size: '15.7 GB',
      sizeGb: 15.7,
      vramRequired: 14,
      contextLength: 32768,
      specialty: 'Code',
      description: 'Optimized for coding tasks and technical work',
      tokensPerSec: 54,
      quantization: 'AWQ',
      status: 'downloaded',
      fitsVram: true,
      fitsCurrentVram: false
    },
    {
      id: 'Qwen/Qwen2.5-72B-Instruct-AWQ',
      name: 'Qwen2.5 72B AWQ',
      size: '35.0 GB',
      sizeGb: 35.0,
      vramRequired: 42,
      contextLength: 32768,
      specialty: 'Quality',
      description: 'Maximum quality, requires high-end GPU',
      tokensPerSec: 28,
      quantization: 'AWQ',
      status: 'available',
      fitsVram: false,
      fitsCurrentVram: false
    }
  ]
}

const MOCK_GPU = { vramTotal: 16, vramUsed: 13.2, vramFree: 2.8 }
const MOCK_CURRENT_MODEL = 'Qwen/Qwen2.5-32B-Instruct-AWQ'
const DEFAULT_POLL_MS = 30000
const PENDING_MODEL_ACTION_POLL_MS = 2000
const MODELS_FETCH_TIMEOUT_MS = 30000

// Named export for dev-only mocking (explicit opt-in via VITE_USE_MOCK_DATA)
export { getMockModels }

async function errorMessageFromResponse(response, fallback) {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail
    if (typeof data?.message === 'string' && data.message.trim()) return data.message
    if (typeof data?.error === 'string' && data.error.trim()) return data.error
  } catch {
    // Keep the stable fallback when the server returns non-JSON error content.
  }
  return fallback
}

export function useModels() {
  const [models, setModels] = useState(USE_MOCK_DATA ? getMockModels() : [])
  const [gpu, setGpu] = useState(USE_MOCK_DATA ? MOCK_GPU : null)
  const [currentModel, setCurrentModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [configuredModel, setConfiguredModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [recommendationAlternatives, setRecommendationAlternatives] = useState([])
  const [loading, setLoading] = useState(USE_MOCK_DATA ? false : true)
  const [error, setError] = useState(null)
  const [pendingAction, setPendingActionState] = useState(null)
  const pendingActionRef = useRef(null)
  const actionTokenRef = useRef(0)
  const modelsRequestRef = useRef(0)
  const latestSettledModelsRequestRef = useRef(0)
  const pollInFlightRef = useRef(false)
  const loadActiveRef = useRef(false)

  const setPendingAction = useCallback((action) => {
    pendingActionRef.current = action
    setPendingActionState(action)
  }, [])

  const startAction = useCallback((modelId, kind) => {
    const action = { modelId, kind, token: ++actionTokenRef.current }
    setPendingAction(action)
    return action
  }, [setPendingAction])

  const finishAction = useCallback((token) => {
    if (pendingActionRef.current?.token === token) setPendingAction(null)
  }, [setPendingAction])

  const reconcilePendingAction = useCallback((nextModels) => {
    const action = pendingActionRef.current
    if (!action || !Array.isArray(nextModels)) return

    const model = nextModels.find(candidate => candidate.id === action.modelId)
    const downloadFinished = action.kind === 'download' &&
      (model?.status === 'downloaded' || model?.status === 'loaded')
    const deleteFinished = action.kind === 'delete' &&
      (!model || model.status === 'available')

    if (downloadFinished || deleteFinished) finishAction(action.token)
  }, [finishAction])

  const fetchModels = useCallback(async () => {
    // If using mock data, don't attempt API call
    if (USE_MOCK_DATA) {
      setLoading(false)
      return
    }

    const requestId = ++modelsRequestRef.current
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), MODELS_FETCH_TIMEOUT_MS)
    try {
      const response = await fetch('/api/models', { signal: controller.signal })
      if (!response.ok) throw new Error('Failed to fetch models')
      const data = await response.json()

      // A slower, older request must not overwrite a newer snapshot.
      if (requestId < latestSettledModelsRequestRef.current) return data
      latestSettledModelsRequestRef.current = requestId

      setModels(data.models)
      setGpu(data.gpu)
      setCurrentModel(data.currentModel)
      setConfiguredModel(data.configuredModel ?? null)
      setRecommendationAlternatives(data.recommendationAlternatives ?? [])
      setError(null)
      reconcilePendingAction(data.models)
      return data
    } catch (err) {
      if (requestId >= latestSettledModelsRequestRef.current) {
        latestSettledModelsRequestRef.current = requestId
        setError(err.message)
      }
      // No silent fallback - let error propagate to UI
    } finally {
      clearTimeout(timeout)
      setLoading(false)
    }
  }, [reconcilePendingAction])

  const pollModels = useCallback(async () => {
    if (document.hidden || pollInFlightRef.current) return
    pollInFlightRef.current = true
    try {
      await fetchModels()
    } finally {
      pollInFlightRef.current = false
    }
  }, [fetchModels])

  useEffect(() => {
    pollModels()
  }, [pollModels])

  const pollInterval = pendingAction?.kind === 'download' || pendingAction?.kind === 'delete'
    ? PENDING_MODEL_ACTION_POLL_MS
    : DEFAULT_POLL_MS

  useEffect(() => {
    // Poll promptly while a model mutation is pending, while keeping at most
    // one scheduled request in flight. Hidden tabs remain idle (#1490).
    const interval = setInterval(pollModels, pollInterval)

    // Resume immediately when the tab becomes visible again
    const onVisibility = () => { if (!document.hidden) pollModels() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [pollInterval, pollModels])

  const downloadModel = async (modelId) => {
    const action = startAction(modelId, 'download')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/download`, {
        method: 'POST'
      })
      if (!response.ok) throw new Error('Failed to start download')
      await fetchModels() // Refresh
    } catch (err) {
      setError(err.message)
    } finally {
      finishAction(action.token)
    }
  }

  const loadModel = async (modelId) => {
    // Prevent concurrent activations — only one model can load at a time
    if (loadActiveRef.current) return
    loadActiveRef.current = true
    const action = startAction(modelId, 'load')
    setError(null)

    // Model activation is long-running (20-60s).  The browser connection
    // often drops before the backend responds (nginx 499 / NetworkError),
    // but the server still completes the activation.  Fire the POST
    // without blocking and poll for status instead.
    let serverError = null
    fetch(`/api/models/${encodeURIComponent(modelId)}/load`, { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          const detail = body.detail || ''
          // 409/lock = another activation running — not a real error, keep polling
          if (/in progress|lock|already/i.test(detail)) return
          serverError = detail || 'Failed to load model'
        }
      })
      .catch(() => {}) // NetworkError — server still processing

    // Poll until model loads, server error, or timeout (2.5 min)
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 5000))
      if (serverError) {
        setError(serverError)
        break
      }
      try {
        const data = await fetchModels()
        if (data?.currentModel === modelId) break
      } catch { /* poll failure, retry */ }
    }

    await fetchModels()
    finishAction(action.token)
    loadActiveRef.current = false
  }

  const deleteModel = async (modelId) => {
    if (!confirm(`Delete ${modelId}? This cannot be undone.`)) return
    
    const action = startAction(modelId, 'delete')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}`, {
        method: 'DELETE'
      })
      if (!response.ok) throw new Error('Failed to delete model')
      await fetchModels() // Refresh
    } catch (err) {
      setError(err.message)
    } finally {
      finishAction(action.token)
    }
  }

  const benchmarkModel = async (modelId) => {
    const action = startAction(modelId, 'benchmark')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/benchmark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_tokens: 128 })
      })
      if (!response.ok) throw new Error(await errorMessageFromResponse(response, 'Failed to benchmark model'))
      await fetchModels()
    } catch (err) {
      setError(err.message)
    } finally {
      finishAction(action.token)
    }
  }

  const actionLoading = pendingAction?.modelId ?? null

  return {
    models,
    gpu,
    currentModel,
    configuredModel,
    recommendationAlternatives,
    loading,
    error,
    actionLoading,
    downloadModel,
    loadModel,
    benchmarkModel,
    deleteModel,
    refresh: fetchModels
  }
}
