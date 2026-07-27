import type { LucideIcon } from 'lucide-react'

export type SectionId =
  | 'overview' | 'tasks' | 'memory' | 'skills' | 'learning' | 'agents' | 'models'
  | 'tools' | 'context' | 'costs' | 'benchmarks' | 'security'

export interface NavigationItem {
  id: SectionId
  label: string
  icon: LucideIcon
}

export interface Metric {
  status: 'available' | 'empty' | 'unavailable'
  value: number | string | null
  unit: string | null
  sample_count: number
  coverage: number | null
  reason: string | null
  as_of: string
}

export interface SeriesPoint {
  key: string | null
  value: number | null
  sample_count: number
  coverage?: number | null
}

export interface DashboardSeries {
  metric: string
  status: 'available' | 'empty' | 'unavailable'
  unit: string
  points: SeriesPoint[]
  count: number
  reason: string | null
  as_of: string
}

export interface DashboardPayload {
  status?: 'available' | 'empty' | 'unavailable'
  as_of?: string
  reason?: string | null
  count?: number
  next_cursor?: string | null
  items?: Record<string, unknown>[]
  metrics?: Record<string, Metric>
  [key: string]: unknown
}

export interface MemoryInspectorItem {
  id: string
  type: string
  scope: string
  subject: string | null
  content: string
  status: string
  sensitivity: string
  provenance: {
    source_type: string | null
    source_id: string | null
    evidence: string[]
  }
  confidence: number
  importance: number
  utility: number
  usage: {
    last_accessed: string | null
    access_count: number
    successful_uses: number
    failed_uses: number
    history_status: 'aggregate_only'
  }
  validity: { valid_from: string; valid_until: string | null }
  lifecycle: {
    state: string
    updated_at: string
    archived_at: string | null
    pinned: boolean
    pinned_at: string | null
    pin_reason: string | null
  }
  supersession: {
    supersedes: string | null
    superseded_by: string | null
  }
  created_at: string
  updated_at: string
}

export interface MemoryInspectorCollection {
  status: 'available' | 'empty'
  items: MemoryInspectorItem[]
  count: number
  next_cursor?: string | null
  truncated?: boolean
  reason: string | null
  as_of: string
}

export interface SkillLabListItem {
  id: string
  manifest_id: string | null
  name: string
  version: string
  description: string
  lifecycle_status: string
  verification_status: string
  reliability: number
  uses: number
  successful_uses: number
  failures: number
  success_rate: number | null
}

export interface SkillLabDetail {
  id: string
  reference: string
  manifest_id: string | null
  name: string
  version: string
  description: string
  instructions: string
  instructions_truncated: boolean
  origin: string | null
  author: string | null
  origin_is_self_declared: boolean
  lifecycle_status: string
  verification_status: string
  token_cost: number
  reliability: number
  uses: number
  successful_uses: number
  failures: number
  success_rate: number | null
  permissions: string[]
  runtime_authority_status: string
  tools: string[]
  models: string[]
  dependencies: string[]
  tests: {
    declared: string[]
    validation_runs: Record<string, unknown>[]
  }
  performance: Record<string, unknown>[]
  history: Record<string, unknown>[]
  evolutions: Record<string, unknown>[]
  benchmarks: Record<string, unknown>[]
  content_hash: string | null
  revision: string
  generated_change_visibility: 'explicit'
}

export interface SkillLabComparison {
  left: SkillLabDetail
  right: SkillLabDetail
  instruction_diff: string[]
  diff_truncated: boolean
  manifest_changes: Record<string, { left: unknown; right: unknown }>
  automatic_changes_hidden: false
}

export interface LearningEvent {
  id: string
  category:
    | 'memory_promotion' | 'memory_deletion' | 'new_skill'
    | 'skill_mutation' | 'routing_change' | 'topology_discovery'
    | 'context_optimization'
  action: string
  status: string
  autonomy: string
  actor: string
  actor_attribution: string
  summary: string
  occurred_at: string
  evidence: Record<string, unknown>
  source_record: string
  content_minimized: true
  reversible: boolean
  audit_gap: string | null
}

export interface LearningEventCollection {
  status: 'available' | 'empty'
  items: LearningEvent[]
  count: number
  next_cursor: string | null
  truncated: boolean
  reason: string | null
  as_of: string
  categories: string[]
  autonomy_states: string[]
  truth_notice: string
}
