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
})
