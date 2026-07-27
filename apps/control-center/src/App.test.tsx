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

const skillDetail = {
  id: 'skill-1',
  reference: 'diagnostics@1.0.0',
  manifest_id: 'diagnostics',
  name: 'Diagnostics',
  version: '1.0.0',
  description: 'Inspect SQLite evidence.',
  instructions: 'Check schema before running a focused query.',
  instructions_truncated: false,
  origin: 'generated',
  author: 'ACR',
  origin_is_self_declared: true,
  lifecycle_status: 'active',
  verification_status: 'static_passed',
  token_cost: 9,
  reliability: .9,
  uses: 0,
  successful_uses: 0,
  failures: 0,
  success_rate: null,
  permissions: ['filesystem:read'],
  runtime_authority_status: 'separate_not_inferred',
  tools: [],
  models: [],
  dependencies: [],
  tests: { declared: ['schema-check'], validation_runs: [] },
  performance: [],
  history: [{ event: 'admitted', created_at: '2026-07-27T00:00:00Z' }],
  evolutions: [],
  benchmarks: [],
  content_hash: 'a'.repeat(64),
  revision: 'b'.repeat(64),
  generated_change_visibility: 'explicit',
}

const learningEvent = {
  id: 'context_optimization:event-1',
  category: 'context_optimization',
  action: 'optimize_context_budget',
  status: 'applied',
  autonomy: 'automatic_within_requested_run',
  actor: 'runtime',
  actor_attribution: 'retained_budget_plan',
  summary: 'Context selection and compression budget applied',
  occurred_at: '2026-07-27T00:00:00Z',
  evidence: {
    candidate_count: 4,
    selected_count: 2,
    tokens_saved: 80,
  },
  source_record: 'token_budget_plans+context_uses',
  content_minimized: true,
  reversible: false,
  audit_gap: null,
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
      if (path.endsWith('/skill-lab/v1/skills')) {
        return Promise.resolve(response({
          status: 'available',
          items: [
            {
              id: 'skill-1', manifest_id: 'diagnostics', name: 'Diagnostics',
              version: '1.0.0', description: 'Inspect SQLite evidence.',
              lifecycle_status: 'active', verification_status: 'static_passed',
              reliability: .9, uses: 0, successful_uses: 0, failures: 0,
              success_rate: null,
            },
            {
              id: 'skill-2', manifest_id: 'diagnostics', name: 'Diagnostics',
              version: '2.0.0', description: 'Inspect SQLite and FTS evidence.',
              lifecycle_status: 'quarantined', verification_status: 'static_passed',
              reliability: .9, uses: 2, successful_uses: 2, failures: 0,
              success_rate: 1,
            },
          ],
          count: 2, reason: null,
        }))
      }
      if (path.includes('/skill-lab/v1/skills/diagnostics%401.0.0')) {
        return Promise.resolve(response(skillDetail))
      }
      if (path.includes('/skill-lab/v1/skills/diagnostics%402.0.0')) {
        return Promise.resolve(response({
          ...skillDetail,
          id: 'skill-2',
          reference: 'diagnostics@2.0.0',
          version: '2.0.0',
          instructions: 'Check schema and FTS before running a focused query.',
          lifecycle_status: 'quarantined',
          revision: 'c'.repeat(64),
        }))
      }
      if (path.endsWith('/skill-lab/v1/compare')) {
        return Promise.resolve(response({
          left: skillDetail,
          right: { ...skillDetail, version: '2.0.0' },
          instruction_diff: [
            '--- diagnostics@1.0.0',
            '+++ diagnostics@2.0.0',
            '-Check schema before running a focused query.',
            '+Check schema and FTS before running a focused query.',
          ],
          diff_truncated: false,
          manifest_changes: { token_cost: { left: 9, right: 10 } },
          automatic_changes_hidden: false,
        }))
      }
      if (path.includes('/learning-dashboard/v1/events')) {
        return Promise.resolve(response({
          status: 'available',
          items: [learningEvent],
          count: 1,
          next_cursor: null,
          truncated: false,
          reason: null,
          as_of: '2026-07-27T00:00:00Z',
          categories: ['context_optimization', 'routing_change'],
          autonomy_states: ['automatic_within_requested_run', 'proposal_only'],
          truth_notice: 'No self-initiated autonomous improvement loop is enabled.',
        }))
      }
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
      'Overview', 'Tasks', 'Memory', 'Skills', 'Learning', 'Agents', 'Models',
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

  it('renders exact skill evidence and exposes every generated comparison change', async () => {
    window.history.replaceState({}, '', '/skills')
    renderApp()
    expect(await screen.findByText('Check schema before running a focused query.')).toBeInTheDocument()
    expect(screen.getByText('Self-declared, not verified identity')).toBeInTheDocument()
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0)
    expect(await screen.findByText('No automatically generated changes are hidden.')).toBeInTheDocument()
    expect(screen.getByText('+Check schema and FTS before running a focused query.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^activate$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^quarantine$/i })).toBeInTheDocument()
    expect(screen.getByText(/benchmark supplied evidence/i)).toBeInTheDocument()
  })

  it('renders a content-minimized learning audit without overstating autonomy', async () => {
    window.history.replaceState({}, '', '/learning')
    renderApp()
    expect(await screen.findByText('Context selection and compression budget applied')).toBeInTheDocument()
    expect(screen.getByText('No self-initiated improvement loop is enabled')).toBeInTheDocument()
    expect(screen.getAllByText('Automatic within requested run').length).toBeGreaterThan(0)
    expect(screen.getByText('Proposal only')).toBeInTheDocument()
    expect(screen.getByText('80')).toBeInTheDocument()
    expect(screen.queryByText(/self-improved|became smarter|deployed/i)).not.toBeInTheDocument()
  })
})
