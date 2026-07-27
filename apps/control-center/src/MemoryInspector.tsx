import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive, BookOpen, CheckCircle2, CircleAlert, GitBranch, KeyRound,
  Pin, RotateCcw, Search, ShieldAlert, Trash2,
} from 'lucide-react'
import {
  fetchInspectorDetail, fetchInspectorRelation, fetchInspectorSearch,
  runMemoryAction,
} from './api'
import type { MemoryInspectorItem } from './types'

const memoryTypes = [
  'semantic', 'episodic', 'procedural', 'failure', 'decision', 'preference',
  'environment', 'temporary',
]
const statuses = ['candidate', 'confirmed', 'superseded', 'archived', 'quarantined']
const lifecycleStates = ['active', 'cold', 'archived']

function date(value: string | null) {
  if (!value) return 'Not recorded'
  return new Intl.DateTimeFormat('en-CA', {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value))
}

function score(value: number) {
  return `${Math.round(value * 100)}%`
}

function Score({ label, value }: { label: string; value: number }) {
  return (
    <div className="memory-score">
      <div><span>{label}</span><strong>{score(value)}</strong></div>
      <div className="memory-score__track"><span style={{ width: score(value) }} /></div>
    </div>
  )
}

function MemoryList({
  items, selectedId, select,
}: {
  items: MemoryInspectorItem[]
  selectedId: string | null
  select: (id: string) => void
}) {
  if (!items.length) {
    return (
      <div className="memory-empty">
        <Search aria-hidden="true" />
        <strong>No visible memories match</strong>
        <span>Try a different exact scope or loosen the filters.</span>
      </div>
    )
  }
  return (
    <div className="memory-list" aria-label="Memory search results">
      {items.map((item) => (
        <button
          type="button"
          key={item.id}
          className={selectedId === item.id ? 'memory-row memory-row--active' : 'memory-row'}
          onClick={() => select(item.id)}
        >
          <div className="memory-row__meta">
            <span>{item.type}</span>
            <span>{item.status}</span>
            {item.lifecycle.pinned && <Pin size={11} aria-label="Pinned" />}
          </div>
          <strong>{item.subject ?? 'Untitled memory'}</strong>
          <p>{item.content}</p>
          <time>{date(item.updated_at)}</time>
        </button>
      ))}
    </div>
  )
}

function History({
  title, items, select,
}: {
  title: string
  items: MemoryInspectorItem[]
  select: (id: string) => void
}) {
  return (
    <section className="memory-subsection">
      <h3>{title}</h3>
      {items.length === 0
        ? <p className="memory-muted">No visible records.</p>
        : (
          <ol className="memory-history">
            {items.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => select(item.id)}>
                  <span>{item.status} · {item.lifecycle.state}</span>
                  <strong>{item.content}</strong>
                  <time>{date(item.validity.valid_from)}</time>
                </button>
              </li>
            ))}
          </ol>
        )}
    </section>
  )
}

export default function MemoryInspector() {
  const queryClient = useQueryClient()
  const [scopeDraft, setScopeDraft] = useState('global')
  const [textDraft, setTextDraft] = useState('')
  const [filters, setFilters] = useState({
    scope: 'global', text: '', memoryType: '', status: '', lifecycle: '',
    minimumConfidence: 0, minimumUtility: 0,
  })
  const [token, setToken] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [correction, setCorrection] = useState({ content: '', evidence: '', reason: '' })
  const [deleteReason, setDeleteReason] = useState('')
  const [deletePlan, setDeletePlan] = useState<{ id: string; memory_id: string } | null>(null)
  const [confirmation, setConfirmation] = useState('')

  const searchQuery = useQuery({
    queryKey: ['memory-inspector', filters, token],
    queryFn: ({ signal }) => fetchInspectorSearch(filters, token, signal),
    retry: false,
  })
  useEffect(() => {
    if (!selectedId && searchQuery.data?.items[0]) {
      setSelectedId(searchQuery.data.items[0].id)
    }
  }, [searchQuery.data, selectedId])

  const detailQuery = useQuery({
    queryKey: ['memory-detail', selectedId, filters.scope, token],
    queryFn: ({ signal }) => fetchInspectorDetail(
      selectedId ?? '', filters.scope, token, signal,
    ),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const memory = detailQuery.data
  useEffect(() => {
    if (memory) {
      setCorrection({ content: memory.content, evidence: '', reason: '' })
      setDeletePlan(null)
      setConfirmation('')
    }
  }, [memory])

  const timelineQuery = useQuery({
    queryKey: ['memory-timeline', memory?.subject, filters.scope, token],
    queryFn: ({ signal }) => fetchInspectorRelation(
      'timeline', filters.scope, memory?.subject ?? '', token, undefined, signal,
    ),
    enabled: Boolean(memory?.subject),
    retry: false,
  })
  const relatedQuery = useQuery({
    queryKey: ['memory-related', memory?.subject, memory?.id, filters.scope, token],
    queryFn: ({ signal }) => fetchInspectorRelation(
      'related', filters.scope, memory?.subject ?? '', token, memory?.id, signal,
    ),
    enabled: Boolean(memory?.subject),
    retry: false,
  })

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['memory-inspector'] })
    await queryClient.invalidateQueries({ queryKey: ['memory-detail'] })
    await queryClient.invalidateQueries({ queryKey: ['memory-timeline'] })
    await queryClient.invalidateQueries({ queryKey: ['memory-related'] })
  }
  const action = useMutation({
    mutationFn: async ({
      path, body,
    }: {
      path: string
      body: Record<string, unknown>
    }) => runMemoryAction<Record<string, unknown>>(path, token, body),
    onSuccess: async () => {
      setNotice('Action completed and the retained view was refreshed.')
      await refresh()
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Action failed'),
  })

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault()
    setSelectedId(null)
    setFilters((current) => ({ ...current, scope: scopeDraft.trim(), text: textDraft.trim() }))
  }
  const lifecycle = (name: 'pin' | 'archive' | 'restore') => {
    if (!memory) return
    action.mutate({
      path: `/memory-inspector/v1/${encodeURIComponent(memory.id)}/lifecycle`,
      body: {
        scope: filters.scope,
        expected_updated_at: memory.updated_at,
        action: name,
        reason: name === 'pin' ? 'Pinned from Memory Inspector' : undefined,
      },
    })
  }

  return (
    <div className="memory-inspector">
      <form className="memory-toolbar panel" onSubmit={submitSearch}>
        <label className="memory-search">
          <span>Search retained beliefs</span>
          <div><Search size={16} /><input value={textDraft} onChange={(event) => setTextDraft(event.target.value)} placeholder="Content or subject…" /></div>
        </label>
        <label><span>Exact scope</span><input required value={scopeDraft} onChange={(event) => setScopeDraft(event.target.value)} /></label>
        <button className="button" type="submit">Search</button>
        <div className="memory-filters">
          <label><span>Type</span><select value={filters.memoryType} onChange={(event) => setFilters({ ...filters, memoryType: event.target.value })}><option value="">All types</option>{memoryTypes.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label><span>Status</span><select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label><span>Lifecycle</span><select value={filters.lifecycle} onChange={(event) => setFilters({ ...filters, lifecycle: event.target.value })}><option value="">All states</option>{lifecycleStates.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label><span>Min confidence</span><input type="number" min="0" max="1" step=".1" value={filters.minimumConfidence} onChange={(event) => setFilters({ ...filters, minimumConfidence: Number(event.target.value) })} /></label>
          <label><span>Min utility</span><input type="number" min="0" max="1" step=".1" value={filters.minimumUtility} onChange={(event) => setFilters({ ...filters, minimumUtility: Number(event.target.value) })} /></label>
        </div>
      </form>

      {searchQuery.isError && (
        <section className="panel memory-auth-state" role="alert">
          <ShieldAlert /><div><strong>Inspector API unavailable</strong><p>Check the exact scope. If the API uses a token, enter it below; it is held only in this page’s memory.</p></div>
        </section>
      )}

      <div className="memory-layout">
        <aside className="panel memory-results">
          <div className="panel-heading"><div><span className="eyebrow">Exact scope</span><h2>{filters.scope}</h2></div><span className="sample-count">{searchQuery.data?.count ?? 0} shown</span></div>
          {searchQuery.isPending
            ? <div className="skeleton" />
            : <MemoryList items={searchQuery.data?.items ?? []} selectedId={selectedId} select={setSelectedId} />}
        </aside>

        <section className="panel memory-detail">
          {!memory && !detailQuery.isPending && <div className="memory-empty"><BookOpen /><strong>Select a memory</strong><span>Inspect what the runtime believes and why.</span></div>}
          {detailQuery.isPending && <><div className="skeleton skeleton--title" /><div className="skeleton" /><div className="skeleton skeleton--short" /></>}
          {memory && (
            <>
              <header className="memory-detail__header">
                <div><span className="eyebrow">{memory.type} · {memory.sensitivity}</span><h2>{memory.subject ?? 'Untitled memory'}</h2></div>
                <div className="memory-tags"><span>{memory.status}</span><span>{memory.lifecycle.state}</span>{memory.lifecycle.pinned && <span><Pin size={11} /> pinned</span>}</div>
              </header>
              <p className="memory-belief">{memory.content}</p>
              <div className="memory-scores">
                <Score label="Confidence" value={memory.confidence} />
                <Score label="Utility" value={memory.utility} />
                <Score label="Importance" value={memory.importance} />
              </div>
              <div className="memory-facts">
                <div><span>Source</span><strong>{memory.provenance.source_type ?? 'Not recorded'}</strong><small>{memory.provenance.source_id ?? 'No source ID'}</small></div>
                <div><span>Usage</span><strong>{memory.usage.access_count} accesses</strong><small>{memory.usage.successful_uses} successful · {memory.usage.failed_uses} failed</small></div>
                <div><span>Valid from</span><strong>{date(memory.validity.valid_from)}</strong><small>{memory.validity.valid_until ? `Until ${date(memory.validity.valid_until)}` : 'No recorded end'}</small></div>
                <div><span>Supersession</span><strong>{memory.supersession.supersedes ? 'Replaces an earlier belief' : 'No visible predecessor'}</strong><small>{memory.supersession.superseded_by ? 'A visible replacement exists' : 'No visible replacement'}</small></div>
              </div>
              <section className="memory-subsection">
                <h3>Evidence</h3>
                {memory.provenance.evidence.length
                  ? <ul className="memory-evidence">{memory.provenance.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
                  : <p className="memory-muted">No evidence reference was retained.</p>}
              </section>
              <p className="memory-caveat"><CircleAlert size={14} /> Usage history is aggregate-only; individual use events were not retained.</p>
              {memory.subject && <History title="Timeline" items={timelineQuery.data?.items ?? []} select={setSelectedId} />}
              {memory.subject && <History title="Related memories" items={relatedQuery.data?.items ?? []} select={setSelectedId} />}

              <section className="memory-actions">
                <div className="memory-actions__heading"><div><KeyRound /><div><h3>Guarded actions</h3><p>Requires token, server-bound operator, exact-scope grant, and a current version.</p></div></div><form className="token-form" onSubmit={(event) => event.preventDefault()}><input aria-label="Action API token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Session action token" /></form></div>
                <div className="memory-action-buttons">
                  <button type="button" onClick={() => lifecycle('pin')} disabled={!token || memory.lifecycle.pinned || action.isPending}><Pin size={14} /> Pin</button>
                  <button type="button" onClick={() => lifecycle('archive')} disabled={!token || memory.lifecycle.pinned || memory.lifecycle.state === 'archived' || action.isPending}><Archive size={14} /> Archive</button>
                  <button type="button" onClick={() => lifecycle('restore')} disabled={!token || memory.lifecycle.state !== 'archived' || action.isPending}><RotateCcw size={14} /> Restore</button>
                </div>
                <details>
                  <summary><GitBranch size={14} /> Correct with a superseding version</summary>
                  <form onSubmit={(event) => {
                    event.preventDefault()
                    action.mutate({
                      path: `/memory-inspector/v1/${encodeURIComponent(memory.id)}/correct`,
                      body: { scope: filters.scope, expected_updated_at: memory.updated_at, content: correction.content, evidence: [correction.evidence], reason: correction.reason },
                    })
                  }}>
                    <label><span>Corrected belief</span><textarea required value={correction.content} onChange={(event) => setCorrection({ ...correction, content: event.target.value })} /></label>
                    <label><span>Evidence reference</span><input required value={correction.evidence} onChange={(event) => setCorrection({ ...correction, evidence: event.target.value })} /></label>
                    <label><span>Reason</span><input required value={correction.reason} onChange={(event) => setCorrection({ ...correction, reason: event.target.value })} /></label>
                    <button className="button" disabled={action.isPending || !token}>Create superseding version</button>
                  </form>
                </details>
                <details>
                  <summary className="danger"><Trash2 size={14} /> Delete through verified erasure</summary>
                  {!deletePlan ? (
                    <form onSubmit={async (event) => {
                      event.preventDefault()
                      try {
                        const plan = await runMemoryAction<{ id: string; memory_id: string }>(
                          `/memory-inspector/v1/${encodeURIComponent(memory.id)}/deletion-plan`,
                          token,
                          { scope: filters.scope, expected_updated_at: memory.updated_at, reason: deleteReason },
                        )
                        setDeletePlan(plan)
                        setNotice('Deletion planned. No content has been erased yet.')
                      } catch (error) {
                        setNotice(error instanceof Error ? error.message : 'Deletion planning failed')
                      }
                    }}>
                      <p>Planning records the current version. Approval securely erases retained content; backups require separate cleanup.</p>
                      <label><span>Deletion reason</span><input required value={deleteReason} onChange={(event) => setDeleteReason(event.target.value)} /></label>
                      <button className="button button--danger" disabled={!token}>Plan deletion</button>
                    </form>
                  ) : (
                    <form onSubmit={async (event) => {
                      event.preventDefault()
                      try {
                        await runMemoryAction(
                          `/memory-inspector/v1/deletion-requests/${encodeURIComponent(deletePlan.id)}/approve`,
                          token,
                          { scope: filters.scope, confirmation },
                        )
                        setNotice('Verified erasure completed.')
                        setSelectedId(null)
                        await refresh()
                      } catch (error) {
                        setNotice(error instanceof Error ? error.message : 'Deletion approval failed')
                      }
                    }}>
                      <p>Type the full memory ID to approve irreversible content erasure:</p>
                      <code>{memory.id}</code>
                      <label><span>Exact confirmation</span><input required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
                      <button className="button button--danger" disabled={confirmation !== memory.id}>Approve verified erasure</button>
                    </form>
                  )}
                </details>
                {notice && <p className="memory-notice" role="status"><CheckCircle2 size={14} /> {notice}</p>}
              </section>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
