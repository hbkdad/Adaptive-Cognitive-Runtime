import { useMemo, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Clock3, DatabaseZap, KeyRound,
  RefreshCw, Route, ShieldCheck, Sparkles, Workflow,
} from 'lucide-react'
import { fetchLearningEvents } from './api'
import type { LearningEvent } from './types'

const categoryLabels: Record<string, string> = {
  memory_promotion: 'Memory promotions',
  memory_deletion: 'Memory deletions',
  new_skill: 'New skills',
  skill_mutation: 'Skill mutations',
  routing_change: 'Routing proposals',
  topology_discovery: 'Topology discoveries',
  context_optimization: 'Context optimizations',
}

const autonomyLabels: Record<string, string> = {
  explicit_approval: 'Explicit approval',
  proposal_only: 'Proposal only',
  workflow_unattributed: 'Workflow actor unretained',
  runtime_derived_advisory: 'Runtime-derived advisory',
  automatic_within_requested_run: 'Automatic within requested run',
}

function label(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatValue(value: unknown) {
  if (value === null || value === undefined) return 'Not recorded'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return new Intl.NumberFormat('en-CA', { maximumFractionDigits: 3 }).format(value)
  if (Array.isArray(value)) return value.map(String).join(', ')
  return String(value)
}

function EventIcon({ category }: { category: string }) {
  if (category.startsWith('memory')) return <DatabaseZap aria-hidden="true" />
  if (category === 'routing_change') return <Route aria-hidden="true" />
  if (category === 'topology_discovery') return <Workflow aria-hidden="true" />
  return <Sparkles aria-hidden="true" />
}

function LearningEventCard({ event }: { event: LearningEvent }) {
  const date = new Date(event.occurred_at)
  const readable = Number.isNaN(date.valueOf())
    ? event.occurred_at
    : date.toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' })
  return (
    <article className="learning-event" aria-labelledby={`event-${event.id}`}>
      <div className="learning-event__rail"><EventIcon category={event.category} /></div>
      <div className="learning-event__body">
        <header>
          <div>
            <span className="eyebrow">{categoryLabels[event.category] ?? label(event.category)}</span>
            <h2 id={`event-${event.id}`}>{event.summary}</h2>
          </div>
          <time dateTime={event.occurred_at}><Clock3 size={12} /> {readable}</time>
        </header>
        <div className="learning-event__badges">
          <span>{label(event.status)}</span>
          <span>{autonomyLabels[event.autonomy] ?? label(event.autonomy)}</span>
          <span>{event.content_minimized ? 'Content minimized' : 'Content unavailable'}</span>
        </div>
        <dl className="learning-evidence">
          {Object.entries(event.evidence).map(([key, value]) => (
            <div key={key}>
              <dt>{label(key)}</dt>
              <dd>{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
        <footer>
          <span>Audit source: <code>{event.source_record}</code></span>
          <span>Actor evidence: {label(event.actor_attribution)}</span>
          <span>{event.reversible ? 'Reversible through its governed workflow' : 'No dashboard rollback'}</span>
        </footer>
        {event.audit_gap && (
          <p className="learning-gap">
            <AlertTriangle size={13} aria-hidden="true" />
            Audit limitation: {event.audit_gap}
          </p>
        )}
      </div>
    </article>
  )
}

export default function LearningDashboard() {
  const [token, setToken] = useState('')
  const [category, setCategory] = useState('')
  const [autonomy, setAutonomy] = useState('')
  const query = useInfiniteQuery({
    queryKey: ['learning-dashboard', token, category, autonomy],
    queryFn: ({ pageParam, signal }) => fetchLearningEvents(
      token,
      { category, autonomy },
      pageParam,
      signal,
    ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: false,
    refetchOnWindowFocus: true,
  })
  const events = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data?.pages],
  )
  const first = query.data?.pages[0]
  const shownByCategory = useMemo(() => {
    const counts: Record<string, number> = {}
    events.forEach((event) => { counts[event.category] = (counts[event.category] ?? 0) + 1 })
    return counts
  }, [events])

  return (
    <div className="learning-dashboard">
      <section className="panel learning-truth">
        <ShieldCheck aria-hidden="true" />
        <div>
          <span className="eyebrow">Truth boundary</span>
          <h2>No self-initiated improvement loop is enabled</h2>
          <p>
            This feed separates approved changes, proposal-only recommendations, advisory discoveries,
            unattributed workflows, and automatic measurements inside requested runs.
          </p>
        </div>
      </section>
      <section className="panel learning-toolbar" aria-label="Learning event filters">
        <label>
          <span><KeyRound size={12} /> Session API token</span>
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="off"
            placeholder="Kept only in browser memory"
          />
        </label>
        <label>
          <span>Event category</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">All categories</option>
            {(first?.categories ?? Object.keys(categoryLabels)).map((item) => (
              <option key={item} value={item}>{categoryLabels[item] ?? label(item)}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Governance state</span>
          <select value={autonomy} onChange={(event) => setAutonomy(event.target.value)}>
            <option value="">All states</option>
            {(first?.autonomy_states ?? Object.keys(autonomyLabels)).map((item) => (
              <option key={item} value={item}>{autonomyLabels[item] ?? label(item)}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void query.refetch()}><RefreshCw size={14} /> Refresh</button>
      </section>
      {events.length > 0 && (
        <section className="learning-summary" aria-label="Loaded event counts">
          {Object.entries(shownByCategory).map(([item, count]) => (
            <div key={item}>
              <strong>{count}</strong>
              <span>{categoryLabels[item] ?? label(item)} shown</span>
            </div>
          ))}
        </section>
      )}
      {query.isPending && (
        <section className="panel state-panel" aria-busy="true">
          <RefreshCw aria-hidden="true" /><h2>Loading retained audit events</h2>
        </section>
      )}
      {query.isError && (
        <section className="panel state-panel" role="alert">
          <AlertTriangle aria-hidden="true" />
          <h2>Learning audit unavailable</h2>
          <p>Check the local API and enter its token if authentication is enabled.</p>
          <button className="button" type="button" onClick={() => void query.refetch()}>Retry</button>
        </section>
      )}
      {!query.isPending && !query.isError && events.length === 0 && (
        <section className="panel state-panel">
          <CheckCircle2 aria-hidden="true" />
          <h2>No matching retained events</h2>
          <p>No candidates or changes are inferred from missing records.</p>
        </section>
      )}
      {events.length > 0 && (
        <>
          <p className="learning-order" id="learning-order">
            Newest retained event first · {events.length} loaded
          </p>
          <ol className="learning-timeline" aria-describedby="learning-order">
            {events.map((event) => <li key={event.id}><LearningEventCard event={event} /></li>)}
          </ol>
          {query.hasNextPage && (
            <button
              className="button learning-more"
              type="button"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? 'Loading…' : 'Load older events'}
            </button>
          )}
        </>
      )}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {query.isFetchingNextPage ? 'Loading older learning events' : `${events.length} learning audit events loaded`}
      </div>
    </div>
  )
}
