import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowRightLeft, CheckCircle2, FlaskConical,
  History, KeyRound, RefreshCw, RotateCcw, ShieldCheck,
} from 'lucide-react'
import {
  compareSkills, fetchSkillDetail, fetchSkillLab, runSkillLabAction,
} from './api'
import type { SkillLabDetail } from './types'

function formatRate(value: number | null) {
  return value === null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `skill-lab-${Date.now()}`
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="skill-json">{JSON.stringify(value, null, 2)}</pre>
}

function Empty({ title, children }: { title: string; children: string }) {
  return (
    <div className="skill-empty">
      <FlaskConical aria-hidden="true" />
      <strong>{title}</strong>
      <span>{children}</span>
    </div>
  )
}

function StatusNotice({ notice }: { notice: string }) {
  if (!notice) return null
  return (
    <p className="skill-notice" role="status">
      <CheckCircle2 size={14} aria-hidden="true" /> {notice}
    </p>
  )
}

function ActionConsole({
  detail,
  token,
  setNotice,
}: {
  detail: SkillLabDetail
  token: string
  setNotice: (notice: string) => void
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const transition = async (action: 'activate' | 'quarantine' | 'retire') => {
    setBusy(true)
    setError('')
    try {
      await runSkillLabAction(
        `/skill-lab/v1/skills/${encodeURIComponent(detail.reference)}/lifecycle`,
        token,
        {
          action,
          expected_revision: detail.revision,
          reason,
          confirmation: action === 'retire' ? confirmation : null,
        },
        idempotencyKey(),
      )
      setNotice(`${detail.reference} was ${action === 'retire' ? 'retired' : `${action}d`}.`)
      setReason('')
      setConfirmation('')
      await queryClient.invalidateQueries({ queryKey: ['skill-lab'] })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The governed action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="skill-actions" aria-label="Governed skill lifecycle controls">
      <div className="skill-actions__heading">
        <div>
          <ShieldCheck size={17} aria-hidden="true" />
          <div>
            <h3>Exact-scope lifecycle controls</h3>
            <p>Every write requires a server-bound operator, exact grant, revision, and one-use key.</p>
          </div>
        </div>
      </div>
      <label>
        <span>Required reason</span>
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={2000}
          placeholder="Record why this change is necessary."
        />
      </label>
      <div className="skill-action-buttons">
        <button
          type="button"
          disabled={busy || !reason.trim() || detail.lifecycle_status === 'active' || detail.lifecycle_status === 'retired'}
          onClick={() => void transition('activate')}
        >
          Activate
        </button>
        <button
          type="button"
          disabled={busy || !reason.trim() || detail.lifecycle_status === 'quarantined' || detail.lifecycle_status === 'retired'}
          onClick={() => void transition('quarantine')}
        >
          Quarantine
        </button>
      </div>
      <details>
        <summary className="danger"><AlertTriangle size={14} /> Retire permanently</summary>
        <label>
          <span>Type {detail.reference} to confirm</span>
          <input
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
          />
        </label>
        <button
          className="button button--danger"
          type="button"
          disabled={busy || !reason.trim() || confirmation !== detail.reference || detail.lifecycle_status === 'retired'}
          onClick={() => void transition('retire')}
        >
          Retire exact version
        </button>
      </details>
      {error && <p className="skill-error" role="alert">{error}</p>}
    </section>
  )
}

function RollbackControl({
  evolution,
  token,
  setNotice,
}: {
  evolution: Record<string, unknown>
  token: string
  setNotice: (notice: string) => void
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (evolution.status !== 'promoted') return null

  const rollback = async () => {
    setBusy(true)
    setError('')
    try {
      const [source, candidate] = await Promise.all([
        fetchSkillDetail(String(evolution.source_skill_id), token),
        fetchSkillDetail(String(evolution.candidate_skill_id), token),
      ])
      await runSkillLabAction(
        `/skill-lab/v1/evolutions/${encodeURIComponent(String(evolution.id))}/rollback`,
        token,
        {
          expected_source_revision: source.revision,
          expected_candidate_revision: candidate.revision,
          reason,
        },
        idempotencyKey(),
      )
      setNotice(`Evolution ${String(evolution.id)} was rolled back atomically.`)
      setReason('')
      await queryClient.invalidateQueries({ queryKey: ['skill-lab'] })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Rollback failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="skill-rollback">
      <div>
        <RotateCcw size={15} aria-hidden="true" />
        <strong>Promoted evolution</strong>
        <span>{String(evolution.source_version)} → {String(evolution.candidate_version)}</span>
      </div>
      <label>
        <span>Rollback reason</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} />
      </label>
      <button type="button" disabled={busy || !reason.trim()} onClick={() => void rollback()}>
        Roll back both versions
      </button>
      {error && <p className="skill-error" role="alert">{error}</p>}
    </div>
  )
}

function BenchmarkControl({
  detail,
  versions,
  token,
  setNotice,
}: {
  detail: SkillLabDetail
  versions: string[]
  token: string
  setNotice: (notice: string) => void
}) {
  const queryClient = useQueryClient()
  const alternative = versions.find((reference) => reference !== detail.reference) ?? ''
  const [candidate, setCandidate] = useState(alternative)
  const [trials, setTrials] = useState('[]')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const benchmark = async () => {
    setBusy(true)
    setError('')
    try {
      const parsed = JSON.parse(trials) as unknown
      if (!Array.isArray(parsed) || parsed.length === 0) {
        throw new Error('Paste a non-empty JSON array of measured three-arm trials.')
      }
      const result = await runSkillLabAction<{ run_id: string }>(
        '/skill-lab/v1/benchmark',
        token,
        {
          skill_name: detail.manifest_id ?? detail.name,
          existing_ref: detail.reference,
          candidate_ref: candidate,
          trials: parsed,
        },
        idempotencyKey(),
      )
      setNotice(`Benchmark ${result.run_id} stored. Its recommendations are proposal-only.`)
      await queryClient.invalidateQueries({ queryKey: ['skill-lab'] })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Benchmark failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="skill-benchmark-control">
      <summary><FlaskConical size={14} /> Benchmark supplied evidence</summary>
      <p>
        This analyzes operator-supplied measurements; it does not run tasks and never changes lifecycle state.
      </p>
      <label>
        <span>Candidate exact version</span>
        <select value={candidate} onChange={(event) => setCandidate(event.target.value)}>
          <option value="">Select a version</option>
          {versions.filter((reference) => reference !== detail.reference).map((reference) => (
            <option key={reference} value={reference}>{reference}</option>
          ))}
        </select>
      </label>
      <label>
        <span>Measured trials JSON</span>
        <textarea value={trials} onChange={(event) => setTrials(event.target.value)} spellCheck={false} />
      </label>
      <button type="button" disabled={busy || !candidate} onClick={() => void benchmark()}>
        Analyze and retain benchmark
      </button>
      {error && <p className="skill-error" role="alert">{error}</p>}
    </details>
  )
}

export default function SkillLab() {
  const [token, setToken] = useState('')
  const [selected, setSelected] = useState('')
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [notice, setNotice] = useState('')

  const listQuery = useQuery({
    queryKey: ['skill-lab', 'list', token],
    queryFn: ({ signal }) => fetchSkillLab(token, signal),
    retry: false,
  })
  const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data?.items])
  const activeReference = selected || (items[0] ? `${items[0].manifest_id}@${items[0].version}` : '')
  const detailQuery = useQuery({
    queryKey: ['skill-lab', 'detail', activeReference, token],
    queryFn: ({ signal }) => fetchSkillDetail(activeReference, token, signal),
    enabled: Boolean(activeReference),
    retry: false,
  })
  const detail = detailQuery.data
  const familyVersions = useMemo(() => {
    if (!detail) return []
    return items
      .filter((item) => item.manifest_id === detail.manifest_id)
      .map((item) => `${item.manifest_id}@${item.version}`)
  }, [detail, items])
  const compareLeft = left || familyVersions[0] || ''
  const compareRight = right || familyVersions[1] || ''
  const comparisonQuery = useQuery({
    queryKey: ['skill-lab', 'comparison', compareLeft, compareRight, token],
    queryFn: ({ signal }) => compareSkills(compareLeft, compareRight, token, signal),
    enabled: Boolean(compareLeft && compareRight && compareLeft !== compareRight),
    retry: false,
  })

  if (listQuery.isPending) {
    return <section className="panel state-panel" aria-busy="true"><RefreshCw /><h2>Loading Skill Lab</h2></section>
  }

  return (
    <div className="skill-lab">
      <section className="panel skill-toolbar">
        <label>
          <span><KeyRound size={12} /> Session API token</span>
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="off"
            placeholder="Kept only in this browser session"
          />
        </label>
        <p>The token is held in React memory and is never persisted by this interface.</p>
        <button type="button" onClick={() => void listQuery.refetch()}><RefreshCw size={14} /> Refresh</button>
      </section>
      {listQuery.isError && (
        <section className="panel state-panel" role="alert">
          <ShieldCheck />
          <h2>Skill Lab unavailable</h2>
          <p>Check the local API and enter its token if authentication is enabled.</p>
        </section>
      )}
      {!listQuery.isError && items.length === 0 && (
        <section className="panel"><Empty title="No registered skills">Admit a skill package to begin.</Empty></section>
      )}
      {items.length > 0 && (
        <div className="skill-layout">
          <aside className="panel skill-rail" aria-label="Skill versions">
            <div className="panel-heading">
              <div><span className="eyebrow">Registry</span><h2>Versions</h2></div>
              <span className="sample-count">{items.length} shown</span>
            </div>
            <div className="skill-version-list">
              {items.map((item) => {
                const reference = `${item.manifest_id}@${item.version}`
                return (
                  <button
                    type="button"
                    key={item.id}
                    className={reference === activeReference ? 'skill-version skill-version--active' : 'skill-version'}
                    onClick={() => {
                      setSelected(reference)
                      setLeft('')
                      setRight('')
                      setNotice('')
                    }}
                  >
                    <span>{item.lifecycle_status} · {item.verification_status}</span>
                    <strong>{item.name}</strong>
                    <code>{item.version}</code>
                    <small>{formatRate(item.success_rate)} success · {item.uses} uses</small>
                  </button>
                )
              })}
            </div>
          </aside>
          <div className="skill-workbench">
            {detailQuery.isPending && <section className="panel state-panel" aria-busy="true"><RefreshCw /><h2>Loading exact version</h2></section>}
            {detailQuery.isError && <section className="panel state-panel" role="alert"><AlertTriangle /><h2>Version unavailable</h2></section>}
            {detail && (
              <>
                <StatusNotice notice={notice} />
                <section className="panel skill-detail">
                  <header className="skill-detail__header">
                    <div>
                      <span className="eyebrow">Exact immutable reference</span>
                      <h2>{detail.name} <code>{detail.version}</code></h2>
                      <p>{detail.description || 'No description declared.'}</p>
                    </div>
                    <div className="skill-badges">
                      <span>{detail.lifecycle_status}</span>
                      <span>{detail.verification_status}</span>
                    </div>
                  </header>
                  <div className="skill-facts">
                    <div><span>Origin</span><strong>{detail.origin ?? 'Not declared'}</strong><small>Self-declared, not verified identity</small></div>
                    <div><span>Author</span><strong>{detail.author ?? 'Not declared'}</strong><small>Manifest claim</small></div>
                    <div><span>Token cost</span><strong>{detail.token_cost}</strong><small>Instruction tokens</small></div>
                    <div><span>Success rate</span><strong>{formatRate(detail.success_rate)}</strong><small>{detail.uses} recorded uses</small></div>
                    <div><span>Reliability</span><strong>{(detail.reliability * 100).toFixed(1)}%</strong><small>Declared/runtime score</small></div>
                    <div><span>Runtime authority</span><strong>Separate</strong><small>Never inferred from manifest</small></div>
                  </div>
                  <section className="skill-subsection">
                    <h3>Instructions</h3>
                    <pre className="skill-instructions">{detail.instructions}</pre>
                    {detail.instructions_truncated && <p className="skill-warning">Instructions were explicitly truncated at the API safety limit.</p>}
                  </section>
                  <div className="skill-two-column">
                    <section className="skill-subsection">
                      <h3>Declared permissions</h3>
                      {detail.permissions.length ? <ul>{detail.permissions.map((item) => <li key={item}><code>{item}</code></li>)}</ul> : <p>None declared.</p>}
                    </section>
                    <section className="skill-subsection">
                      <h3>Declared tests</h3>
                      {detail.tests.declared.length ? <ul>{detail.tests.declared.map((item) => <li key={item}>{item}</li>)}</ul> : <p>None declared.</p>}
                    </section>
                  </div>
                  <details className="skill-data-section">
                    <summary>Validation evidence ({detail.tests.validation_runs.length})</summary>
                    {detail.tests.validation_runs.length ? <JsonBlock value={detail.tests.validation_runs} /> : <Empty title="No validator runs">Activation remains unavailable without all mandatory stages.</Empty>}
                  </details>
                  <details className="skill-data-section">
                    <summary>Benchmark results ({detail.benchmarks.length})</summary>
                    {detail.benchmarks.length ? <JsonBlock value={detail.benchmarks} /> : <Empty title="No retained benchmarks">Measured evidence remains visibly absent.</Empty>}
                  </details>
                  <details className="skill-data-section">
                    <summary><History size={14} /> Runtime history ({detail.history.length})</summary>
                    {detail.history.length ? <JsonBlock value={detail.history} /> : <Empty title="No history">No lifecycle events were retained.</Empty>}
                  </details>
                  <details className="skill-data-section">
                    <summary>Performance by task and model ({detail.performance.length})</summary>
                    {detail.performance.length ? <JsonBlock value={detail.performance} /> : <Empty title="No performance evidence">No use has been attributed to this version.</Empty>}
                  </details>
                  {detail.evolutions.map((evolution) => (
                    <RollbackControl key={String(evolution.id)} evolution={evolution} token={token} setNotice={setNotice} />
                  ))}
                  <ActionConsole detail={detail} token={token} setNotice={setNotice} />
                  <BenchmarkControl detail={detail} versions={familyVersions} token={token} setNotice={setNotice} />
                </section>
                {familyVersions.length > 1 && (
                  <section className="panel skill-compare">
                    <div className="panel-heading">
                      <div><span className="eyebrow">Version evidence</span><h2>Compare v1 vs v2</h2></div>
                      <span className="sample-count"><ArrowRightLeft size={12} /> exact refs</span>
                    </div>
                    <div className="skill-compare-selectors">
                      <label><span>From</span><select value={compareLeft} onChange={(event) => setLeft(event.target.value)}>{familyVersions.map((reference) => <option key={reference}>{reference}</option>)}</select></label>
                      <ArrowRightLeft aria-hidden="true" />
                      <label><span>To</span><select value={compareRight} onChange={(event) => setRight(event.target.value)}>{familyVersions.map((reference) => <option key={reference}>{reference}</option>)}</select></label>
                    </div>
                    {comparisonQuery.isError && <p className="skill-error" role="alert">These exact versions could not be compared.</p>}
                    {comparisonQuery.data && (
                      <>
                        <p className="skill-visible-change"><CheckCircle2 size={14} /> No automatically generated changes are hidden.</p>
                        <div className="skill-diff" role="region" aria-label="Instruction changes" tabIndex={0}>
                          {comparisonQuery.data.instruction_diff.map((line, index) => {
                            const kind = line.startsWith('+') && !line.startsWith('+++')
                              ? 'added'
                              : line.startsWith('-') && !line.startsWith('---') ? 'removed' : 'context'
                            return <div key={`${index}-${line}`} className={`skill-diff__${kind}`}><span>{kind}</span><code>{line || ' '}</code></div>
                          })}
                        </div>
                        {comparisonQuery.data.diff_truncated && <p className="skill-warning">The diff reached its explicit 800-line safety limit.</p>}
                        <details className="skill-data-section">
                          <summary>Manifest changes ({Object.keys(comparisonQuery.data.manifest_changes).length})</summary>
                          <JsonBlock value={comparisonQuery.data.manifest_changes} />
                        </details>
                      </>
                    )}
                  </section>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
