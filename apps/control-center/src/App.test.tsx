import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const overview = {
  status: 'available',
  as_of: '2026-07-27T00:00:00Z',
  metrics: {
    tasks: {
      status: 'available', value: 3, unit: 'tasks', sample_count: 3,
      coverage: null, reason: null, as_of: '2026-07-27T00:00:00Z',
    },
  },
}

const inspectorMemory = {
  id: 'memory-1',
  type: 'semantic',
  scope: 'global',
  subject: 'database',
  content: 'The runtime uses SQLite FTS5.',
  status: 'confirmed',
  sensitivity: 'internal',
  provenance: {
    source_type: 'operator',
    source_id: 'setup',
    evidence: ['test:verified'],
  },
  confidence: .94,
  importance: .8,
  utility: .7,
  usage: {
    last_accessed: null,
    access_count: 2,
    successful_uses: 2,
    failed_uses: 0,
    history_status: 'aggregate_only',
  },
  validity: { valid_from: '2026-07-27T00:00:00Z', valid_until: null },
  lifecycle: {
    state: 'active',
    updated_at: '2026-07-27T00:00:00Z',
    archived_at: null,
    pinned: false,
    pinned_at: null,
    pin_reason: null,
  },
  supersession: { supersedes: null, superseded_by: null },
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
}

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

describe('ACR control center', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/overview')
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/dashboard/v1/overview')) return Promise.resolve(response(overview))
      if (path.includes('/memory-inspector/v1/search')) {
        return Promise.resolve(response({
          status: 'available', items: [inspectorMemory], count: 1,
          next_cursor: null, reason: null, as_of: '2026-07-27T00:00:00Z',
        }))
      }
      if (path.includes('/memory-inspector/v1/timeline')) {
        return Promise.resolve(response({
          status: 'available', items: [inspectorMemory], count: 1,
          reason: null, as_of: '2026-07-27T00:00:00Z',
        }))
      }
      if (path.includes('/memory-inspector/v1/related')) {
        return Promise.resolve(response({
          status: 'empty', items: [], count: 0,
          reason: 'no_visible_related_memories', as_of: '2026-07-27T00:00:00Z',
        }))
      }
      if (path.includes('/memory-inspector/v1/memory-1')) {
        return Promise.resolve(response(inspectorMemory))
      }
      return Promise.resolve(response({
        metric: path.split('/').pop(),
        status: 'empty',
        unit: 'tokens',
        points: [],
        count: 0,
        reason: 'no_observations',
        as_of: '2026-07-27T00:00:00Z',
      }))
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders every operational section and real API metric', async () => {
    renderApp()
    expect(await screen.findByText('3')).toBeInTheDocument()
    for (const label of [
      'Overview', 'Tasks', 'Memory', 'Skills', 'Agents', 'Models',
      'Tools', 'Context', 'Costs', 'Benchmarks', 'Security',
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    expect(screen.queryByText(/sample data/i)).not.toBeInTheDocument()
  })

  it('navigates without issuing a write request', async () => {
    const user = userEvent.setup()
    renderApp()
    await screen.findByText('3')
    await user.click(screen.getByRole('link', { name: /^tasks$/i }))
    await waitFor(() => expect(window.location.pathname).toBe('/tasks'))
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock).toHaveBeenCalled()
    for (const call of fetchMock.mock.calls) {
      expect((call[1] as RequestInit | undefined)?.method ?? 'GET').toBe('GET')
    }
  })

  it('renders the inspectable memory evidence and guarded controls', async () => {
    window.history.replaceState({}, '', '/memory')
    renderApp()
    expect(await screen.findByText('The runtime uses SQLite FTS5.')).toBeInTheDocument()
    expect(await screen.findByText('test:verified')).toBeInTheDocument()
    expect(screen.getByText('94%')).toBeInTheDocument()
    expect(screen.getByText(/aggregate-only/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^pin$/i })).toBeInTheDocument()
    expect(screen.getByText(/correct with a superseding version/i)).toBeInTheDocument()
    expect(screen.getByText(/delete through verified erasure/i)).toBeInTheDocument()
  })
})
