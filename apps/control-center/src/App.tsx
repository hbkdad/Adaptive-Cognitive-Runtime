import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity, Bot, Brain, ChartNoAxesCombined, ChevronRight, CircleDollarSign,
  Database, FlaskConical, Gauge, Menu, Network, RefreshCw, ServerCog,
  ShieldCheck, Wrench, X,
} from 'lucide-react'
import './App.css'
import { fetchDashboard, fetchSeries } from './api'
import MemoryInspector from './MemoryInspector'
import SkillLab from './SkillLab'
import type {
  DashboardPayload, DashboardSeries, Metric, NavigationItem, SectionId,
} from './types'

const TelemetryChart = lazy(() => import('./TelemetryChart'))

const navigation: NavigationItem[] = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'tasks', label: 'Tasks', icon: Activity },
  { id: 'memory', label: 'Memory', icon: Database },
  { id: 'skills', label: 'Skills', icon: Brain },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'models', label: 'Models', icon: ServerCog },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'context', label: 'Context', icon: Network },
  { id: 'costs', label: 'Costs', icon: CircleDollarSign },
  { id: 'benchmarks', label: 'Benchmarks', icon: FlaskConical },
  { id: 'security', label: 'Security', icon: ShieldCheck },
]

const seriesBySection: Partial<Record<SectionId, string[]>> = {
  overview: ['tokens_per_day', 'success_rate', 'failed_tasks', 'learning_events'],
  tasks: ['tokens_per_task', 'failed_tasks'],
  memory: ['memory_usefulness'],
  skills: ['skill_roi'],
  models: ['model_routing'],
  context: ['context_waste'],
  costs: ['cost_per_task'],
}

const seriesLabels: Record<string, string> = {
  tokens_per_day: 'Tokens per day',
  tokens_per_task: 'Tokens per task',
  cost_per_task: 'Cost per task',
  success_rate: 'Task success rate',
  skill_roi: 'Approximate skill ROI',
  memory_usefulness: 'Memory usefulness',
  context_waste: 'Context waste',
  model_routing: 'Model routing',
  failed_tasks: 'Failed tasks',
  learning_events: 'Learning events',
}

function routeFromLocation(): SectionId {
  const candidate = window.location.pathname.split('/').filter(Boolean)[0]
  return navigation.some((item) => item.id === candidate)
    ? candidate as SectionId
    : 'overview'
}

function formatLabel(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatValue(value: unknown, unit?: string | null) {
  if (value === null || value === undefined) return 'Not available'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    if (unit === 'ratio') return `${(value * 100).toFixed(1)}%`
    if (unit === 'currency') return `$${value.toFixed(4)}`
    if (unit === 'approximate_roi') return value.toFixed(4)
    return new Intl.NumberFormat('en-CA', { maximumFractionDigits: 2 }).format(value)
  }
  return String(value)
}

function MetricCard({ label, metric }: { label: string; metric: Metric }) {
  return (
    <article className="metric-card">
      <div className="metric-card__eyebrow">{label}</div>
      <div className="metric-card__value">
        {metric.status === 'available'
          ? formatValue(metric.value, metric.unit)
          : metric.status === 'empty' ? 'No data yet' : 'Unavailable'}
      </div>
      <div className="metric-card__meta">
        {metric.sample_count} sample{metric.sample_count === 1 ? '' : 's'}
        {metric.reason ? ` · ${formatLabel(metric.reason)}` : ''}
      </div>
    </article>
  )
}

function LoadingPanel({ label }: { label: string }) {
  return (
    <section className="panel" aria-busy="true" aria-label={`Loading ${label}`}>
      <div className="skeleton skeleton--title" />
      <div className="skeleton" />
      <div className="skeleton skeleton--short" />
    </section>
  )
}

function ErrorPanel({ label, retry }: { label: string; retry: () => void }) {
  return (
    <section className="panel state-panel" role="alert">
      <ShieldCheck aria-hidden="true" />
      <h2>{label} unavailable</h2>
      <p>The local ACR API did not return a valid dashboard response.</p>
      <button className="button" type="button" onClick={retry}>
        <RefreshCw aria-hidden="true" size={16} /> Retry
      </button>
    </section>
  )
}

function SeriesPanel({ metric }: { metric: string }) {
  const query = useQuery({
    queryKey: ['dashboard-series', metric],
    queryFn: ({ signal }) => fetchSeries(metric, signal),
    refetchInterval: 30_000,
  })
  if (query.isPending) return <LoadingPanel label={seriesLabels[metric]} />
  if (query.isError) {
    return <ErrorPanel label={seriesLabels[metric]} retry={() => void query.refetch()} />
  }
  const series = query.data
  return <SeriesContent series={series} />
}

function SeriesContent({ series }: { series: DashboardSeries }) {
  const title = seriesLabels[series.metric] ?? formatLabel(series.metric)
  if (series.status !== 'available' || series.points.length === 0) {
    return (
      <section className="panel state-panel">
        <ChartNoAxesCombined aria-hidden="true" />
        <h2>{title}</h2>
        <p>
          {series.status === 'unavailable'
            ? formatLabel(series.reason ?? 'Evidence unavailable')
            : 'No observations recorded yet.'}
        </p>
      </section>
    )
  }
  const chronological = [...series.points].reverse()
  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Real telemetry</span>
          <h2>{title}</h2>
        </div>
        <span className="sample-count">{series.count} points</span>
      </div>
      <Suspense fallback={<div className="chart"><div className="skeleton" /></div>}>
        <TelemetryChart
          series={series}
          title={title}
          formatValue={formatValue}
        />
      </Suspense>
      <details className="data-fallback">
        <summary>View exact values</summary>
        <table>
          <thead><tr><th>Label</th><th>Value</th><th>Samples</th></tr></thead>
          <tbody>
            {chronological.map((point, index) => (
              <tr key={`${point.key}-${index}`}>
                <td>{point.key ?? 'Unknown'}</td>
                <td>{formatValue(point.value, series.unit)}</td>
                <td>{point.sample_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}

function RecordTable({ items, label }: { items: Record<string, unknown>[]; label: string }) {
  const columns = useMemo(() => {
    const keys = new Set<string>()
    items.slice(0, 20).forEach((item) => Object.keys(item).forEach((key) => keys.add(key)))
    return [...keys].slice(0, 9)
  }, [items])
  if (!items.length) {
    return (
      <section className="panel state-panel">
        <Database aria-hidden="true" />
        <h2>No {label.toLowerCase()} recorded</h2>
        <p>This section will populate from retained runtime facts.</p>
      </section>
    )
  }
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <div><span className="eyebrow">Bounded read model</span><h2>{label}</h2></div>
        <span className="sample-count">{items.length} shown</span>
      </div>
      <div className="table-scroll" role="region" aria-label={`${label} table`} tabIndex={0}>
        <table>
          <thead><tr>{columns.map((column) => <th key={column}>{formatLabel(column)}</th>)}</tr></thead>
          <tbody>
            {items.map((item, rowIndex) => (
              <tr key={String(item.id ?? item.name ?? rowIndex)}>
                {columns.map((column) => (
                  <td key={column} title={formatValue(item[column])}>
                    {formatValue(item[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ObjectSummary({ payload, label }: { payload: DashboardPayload; label: string }) {
  const metrics = payload.metrics
  const listGroups = Object.entries(payload).filter(([, value]) =>
    Array.isArray(value) && value.every((item) => typeof item === 'object' && item !== null),
  )
  const nestedCollections = Object.entries(payload).filter(([, value]) =>
    typeof value === 'object' && value !== null && !Array.isArray(value)
    && Array.isArray((value as DashboardPayload).items),
  )
  return (
    <>
      {metrics && (
        <div className="metrics-grid">
          {Object.entries(metrics).map(([name, metric]) => (
            <MetricCard key={name} label={formatLabel(name)} metric={metric} />
          ))}
        </div>
      )}
      {listGroups.map(([name, value]) => (
        <RecordTable key={name} items={value as Record<string, unknown>[]} label={formatLabel(name)} />
      ))}
      {nestedCollections.map(([name, value]) => (
        <RecordTable
          key={name}
          items={(value as DashboardPayload).items ?? []}
          label={formatLabel(name)}
        />
      ))}
      {!metrics && !listGroups.length && !nestedCollections.length && (
        <section className="panel state-panel">
          <Database aria-hidden="true" />
          <h2>{label}</h2>
          <p>{payload.reason ? formatLabel(payload.reason) : 'No retained observations yet.'}</p>
        </section>
      )}
    </>
  )
}

function DashboardSection({ section }: { section: SectionId }) {
  const endpoint = section === 'tasks' ? 'tasks' : section
  const query = useQuery({
    queryKey: ['dashboard-section', endpoint],
    queryFn: ({ signal }) => fetchDashboard(endpoint, signal),
    refetchInterval: 30_000,
  })
  const label = navigation.find((item) => item.id === section)?.label ?? formatLabel(section)
  if (query.isPending) return <LoadingPanel label={label} />
  if (query.isError) return <ErrorPanel label={label} retry={() => void query.refetch()} />
  const payload = query.data
  const items = payload.items ?? []
  return (
    <>
      {items.length > 0 && <RecordTable items={items} label={label} />}
      {items.length === 0 && section !== 'overview' && <ObjectSummary payload={payload} label={label} />}
      {section === 'overview' && <ObjectSummary payload={payload} label={label} />}
      <div className="charts-grid">
        {(seriesBySection[section] ?? []).map((metric) => (
          <SeriesPanel key={metric} metric={metric} />
        ))}
      </div>
    </>
  )
}

function App() {
  const [section, setSection] = useState<SectionId>(routeFromLocation)
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => {
    const update = () => setSection(routeFromLocation())
    window.addEventListener('popstate', update)
    return () => window.removeEventListener('popstate', update)
  }, [])
  const navigate = (target: SectionId) => {
    window.history.pushState({}, '', `/${target}`)
    setSection(target)
    setMenuOpen(false)
    document.querySelector<HTMLElement>('#main-content')?.focus()
  }
  const active = navigation.find((item) => item.id === section) ?? navigation[0]
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to dashboard content</a>
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`}>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true"><span /></div>
          <div><strong>ACR</strong><span>Control Center</span></div>
          <button className="icon-button sidebar-close" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation">
            <X />
          </button>
        </div>
        <nav aria-label="Dashboard sections">
          {navigation.map((item) => {
            const Icon = item.icon
            return (
              <a
                key={item.id}
                href={`/${item.id}`}
                aria-current={section === item.id ? 'page' : undefined}
                onClick={(event) => { event.preventDefault(); navigate(item.id) }}
              >
                <Icon aria-hidden="true" size={18} />
                <span>{item.label}</span>
                <ChevronRight className="nav-arrow" aria-hidden="true" size={14} />
              </a>
            )
          })}
        </nav>
        <div className="system-status">
          <span className="status-dot" />
          <div><strong>Local runtime</strong><span>30-second refresh</span></div>
        </div>
      </aside>
      {menuOpen && <button className="scrim" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation overlay" />}
      <div className="workspace">
        <header className="topbar">
          <button className="icon-button menu-button" type="button" onClick={() => setMenuOpen(true)} aria-label="Open navigation">
            <Menu />
          </button>
          <div className="breadcrumb"><span>Operations</span><ChevronRight size={14} /><strong>{active.label}</strong></div>
          <div className="live-indicator"><span /> {
            section === 'memory'
              ? 'Guarded memory controls'
              : section === 'skills' ? 'Governed skill controls' : 'Read-only telemetry'
          }</div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <span className="eyebrow">Adaptive Cognitive Runtime</span>
              <h1>{active.label}</h1>
              <p>{
                section === 'memory'
                  ? 'Inspect retained beliefs, their evidence, history, and governed lifecycle.'
                  : section === 'skills'
                    ? 'Inspect, compare, benchmark, and govern exact skill versions without hiding generated changes.'
                    : 'Operational facts from the local runtime. Missing evidence stays visibly missing.'
              }</p>
            </div>
            <div className="read-only-badge"><ShieldCheck size={16} /> {
              section === 'memory' || section === 'skills' ? 'Exact-scope controls' : 'Read only'
            }</div>
          </div>
          {section === 'memory'
            ? <MemoryInspector />
            : section === 'skills' ? <SkillLab /> : <DashboardSection section={section} />}
        </main>
      </div>
    </div>
  )
}

export default App
