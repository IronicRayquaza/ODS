import { renderHook, waitFor, act } from '@testing-library/react'
import { useModels } from '../useModels'

// Shadow jsdom's Document.prototype.hidden getter on the instance; deleting
// the own property in afterEach restores the prototype behavior.
const setDocumentHidden = (hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const modelsResponse = (models, overrides = {}) => ({
  ok: true,
  json: () => Promise.resolve({ models, gpu: null, currentModel: null, ...overrides })
})

describe('useModels', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete document.hidden
  })

  test('fetches models on mount', async () => {
    const mockData = {
      models: [{ id: 'qwen-32b', name: 'Qwen2.5 32B' }],
      gpu: { vramTotal: 16 },
      currentModel: 'qwen-32b',
      configuredModel: 'qwen-32b',
      recommendationAlternatives: [{ id: 'qwen-32b', name: 'Qwen2.5 32B' }]
    }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockData)
    })

    const { result } = renderHook(() => useModels())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.models).toHaveLength(1)
    expect(result.current.models[0].id).toBe('qwen-32b')
    expect(result.current.gpu.vramTotal).toBe(16)
    expect(result.current.currentModel).toBe('qwen-32b')
    expect(result.current.configuredModel).toBe('qwen-32b')
    expect(result.current.recommendationAlternatives[0].id).toBe('qwen-32b')
    expect(result.current.error).toBeNull()
  })

  test('sets error on fetch failure', async () => {
    fetch.mockResolvedValue({ ok: false })

    const { result } = renderHook(() => useModels())

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
    })
    expect(result.current.loading).toBe(false)
  })

  test('downloadModel calls POST and refreshes', async () => {
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [{ id: 'new-model' }], gpu: null, currentModel: null })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.downloadModel('new-model')
    })

    const postCall = fetch.mock.calls.find(c => c[1]?.method === 'POST')
    expect(postCall).toBeTruthy()
    expect(postCall[0]).toContain('new-model')
    expect(postCall[0]).toContain('/download')
  })

  test('clears a pending download when an independent refresh confirms completion', async () => {
    const downloadRequest = deferred()
    let snapshot = [{ id: 'new-model', status: 'available' }]
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return downloadRequest.promise
      return Promise.resolve(modelsResponse(snapshot))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let downloadPromise
    act(() => {
      downloadPromise = result.current.downloadModel('new-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('new-model'))

    snapshot = [{ id: 'new-model', status: 'downloaded' }]
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.actionLoading).toBeNull()

    await act(async () => {
      downloadRequest.resolve({ ok: true })
      await downloadPromise
    })
  })

  test('deleteModel calls DELETE and refreshes', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))

    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'DELETE') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [], gpu: null, currentModel: null })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.deleteModel('to-delete')
    })

    expect(confirm).toHaveBeenCalled()
    const deleteCall = fetch.mock.calls.find(c => c[1]?.method === 'DELETE')
    expect(deleteCall).toBeTruthy()
    expect(deleteCall[0]).toContain('to-delete')
  })

  test('clears a pending delete when an independent refresh confirms removal', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    const deleteRequest = deferred()
    let snapshot = [{ id: 'to-delete', status: 'downloaded' }]
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'DELETE') return deleteRequest.promise
      return Promise.resolve(modelsResponse(snapshot))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let deletePromise
    act(() => {
      deletePromise = result.current.deleteModel('to-delete')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('to-delete'))

    snapshot = []
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.actionLoading).toBeNull()

    await act(async () => {
      deleteRequest.resolve({ ok: true })
      await deletePromise
    })
  })

  test('late mutation settlement does not clear a newer action', async () => {
    const oldDownload = deferred()
    const newDownload = deferred()
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST') {
        return String(url).includes('old-model') ? oldDownload.promise : newDownload.promise
      }
      return Promise.resolve(modelsResponse([
        { id: 'old-model', status: 'available' },
        { id: 'new-model', status: 'available' }
      ]))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let oldPromise
    act(() => {
      oldPromise = result.current.downloadModel('old-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('old-model'))

    let newPromise
    act(() => {
      newPromise = result.current.downloadModel('new-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('new-model'))

    await act(async () => {
      oldDownload.resolve({ ok: true })
      await oldPromise
    })
    expect(result.current.actionLoading).toBe('new-model')

    await act(async () => {
      newDownload.resolve({ ok: true })
      await newPromise
    })
    expect(result.current.actionLoading).toBeNull()
  })

  test('keeps only one prompt poll in flight while a model action is pending', async () => {
    vi.useFakeTimers()
    const downloadRequest = deferred()
    const pendingPoll = deferred()
    let getCount = 0
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return downloadRequest.promise
      getCount += 1
      if (getCount === 1) return Promise.resolve(modelsResponse([{ id: 'new-model', status: 'available' }]))
      return pendingPoll.promise
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})
      expect(getCount).toBe(1)

      let downloadPromise
      act(() => {
        downloadPromise = result.current.downloadModel('new-model')
      })
      expect(result.current.actionLoading).toBe('new-model')

      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(getCount).toBe(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(getCount).toBe(2)

      await act(async () => {
        pendingPoll.resolve(modelsResponse([{ id: 'new-model', status: 'downloaded' }]))
        await pendingPoll.promise
      })
      expect(result.current.actionLoading).toBeNull()

      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(getCount).toBe(2)

      downloadRequest.resolve({ ok: false })
      await act(async () => { await downloadPromise })
    } finally {
      vi.useRealTimers()
    }
  })

  test('recovers polling after a models request exceeds its deadline', async () => {
    vi.useFakeTimers()
    let getCount = 0
    fetch.mockImplementation((_url, options) => {
      getCount += 1
      if (getCount > 1) {
        return Promise.resolve(modelsResponse([{ id: 'recovered', status: 'loaded' }]))
      }
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('The operation was aborted.')
          error.name = 'AbortError'
          reject(error)
        }, { once: true })
      })
    })

    try {
      const { result } = renderHook(() => useModels())
      expect(getCount).toBe(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(result.current.loading).toBe(false)
      expect(getCount).toBe(2)
      expect(result.current.models[0].id).toBe('recovered')
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('does not let an older models response overwrite a newer snapshot', async () => {
    const firstRequest = deferred()
    const secondRequest = deferred()
    let getCount = 0
    fetch.mockImplementation(() => {
      getCount += 1
      return getCount === 1 ? firstRequest.promise : secondRequest.promise
    })

    const { result } = renderHook(() => useModels())
    expect(getCount).toBe(1)

    let refreshPromise
    act(() => {
      refreshPromise = result.current.refresh()
    })
    expect(getCount).toBe(2)

    await act(async () => {
      secondRequest.resolve(modelsResponse([{ id: 'new-snapshot', status: 'loaded' }]))
      await refreshPromise
    })
    expect(result.current.models[0].id).toBe('new-snapshot')

    await act(async () => {
      firstRequest.resolve(modelsResponse([{ id: 'stale-snapshot', status: 'available' }]))
      await firstRequest.promise
      await Promise.resolve()
    })
    expect(result.current.models[0].id).toBe('new-snapshot')
  })

  test('benchmarkModel calls POST and refreshes', async () => {
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST' && String(url).includes('/benchmark')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ tokensPerSecond: 42 }) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [{ id: 'qwen3.5-9b-q4' }], gpu: null, currentModel: 'qwen3.5-9b-q4' })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.benchmarkModel('qwen3.5-9b-q4')
    })

    const benchmarkCall = fetch.mock.calls.find(c => c[1]?.method === 'POST' && String(c[0]).includes('/benchmark'))
    expect(benchmarkCall).toBeTruthy()
    expect(benchmarkCall[0]).toContain('qwen3.5-9b-q4')
    expect(benchmarkCall[1].body).toContain('max_tokens')
  })

  test('pauses 30s polling while the tab is hidden and refreshes on visibilitychange', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ models: [], gpu: null, currentModel: null })
    })

    vi.useFakeTimers()
    try {
      renderHook(() => useModels())
      await act(async () => {})
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(90000) })
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      expect(fetch).toHaveBeenCalledTimes(2)

      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(fetch).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  test('deleteModel aborts when user cancels confirm', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))

    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ models: [{ id: 'keep-me' }], gpu: null, currentModel: null })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    const callCountBefore = fetch.mock.calls.length

    await act(async () => {
      await result.current.deleteModel('keep-me')
    })

    expect(fetch.mock.calls.length).toBe(callCountBefore)
  })
})
