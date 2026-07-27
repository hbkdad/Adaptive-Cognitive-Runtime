import type { LucideIcon } from 'lucide-react'

export type SectionId =
  | 'overview' | 'tasks' | 'memory' | 'skills' | 'agents' | 'models'
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
