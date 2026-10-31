import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import DesktopApps from './DesktopApps'

afterEach(() => vi.restoreAllMocks())

test('lists native apps with their state and opens a running one', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ apps: [
    { id: 'qdrant', name: 'Qdrant (Vector DB)', version: '1.16.3', installedVersion: '1.16.3', state: 'running', url: 'http://127.0.0.1:6333/' },
    { id: 'n8n', name: 'n8n', version: '2.41.6', installedVersion: null, state: 'available', url: null },
  ] }) })
  render(<DesktopApps />)
  expect(await screen.findByText('Qdrant (Vector DB)')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /open/i })).toHaveAttribute('href', 'http://127.0.0.1:6333/')
  expect(screen.getByText('Not installed')).toBeInTheDocument()
  expect(screen.getByText('-Apps n8n')).toBeInTheDocument()
})

test('shows the API address of a running embeddings app instead of an Open link', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ apps: [
    { id: 'embeddings', name: 'TEI (Embeddings)', version: '1.5', installedVersion: '1.5', state: 'running', url: null, apiUrl: 'http://127.0.0.1:8090/v1' },
  ] }) })
  render(<DesktopApps />)
  expect(await screen.findByText('http://127.0.0.1:8090/v1')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /open/i })).toBeNull()
})

test('says plainly when the list does not load', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 503, json: async () => ({}) })
  render(<DesktopApps />)
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503')
})
