import type {
  DashboardPayload, DashboardSeries, MemoryInspectorCollection,
  MemoryInspectorItem, SkillLabComparison, SkillLabDetail, SkillLabListItem,
} from './types'

class DashboardApiError extends Error {
  status: number
  retryAfter: string | null

  constructor(status: number, retryAfter: string | null) {
    super(`Dashboard API request failed with status ${status}`)
    this.name = 'DashboardApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) {
    throw new DashboardApiError(response.status, response.headers.get('Retry-After'))
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    throw new Error('Dashboard API returned a non-JSON response')
  }
  return response.json() as Promise<T>
}

function tokenHeaders(token?: string): Record<string, string> {
  return token ? { 'X-ACR-Token': token } : {}
}

async function inspectorJson<T>(
  path: string,
  token: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json', ...tokenHeaders(token) },
    signal,
  })
  if (!response.ok) {
    throw new DashboardApiError(response.status, response.headers.get('Retry-After'))
  }
  return response.json() as Promise<T>
}

async function inspectorMutation<T>(
  path: string,
  token: string,
  body: Record<string, unknown>,
  idempotencyKey?: string,
): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...tokenHeaders(token),
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
    },
    body: JSON.stringify(body),
  })
  const payload = await response.json() as T & { detail?: string }
  if (!response.ok) {
    throw new Error(payload.detail ?? `Governed action failed with status ${response.status}`)
  }
  return payload
}

export function fetchDashboard(section: string, signal?: AbortSignal) {
  return getJson<DashboardPayload>(`/dashboard/v1/${encodeURIComponent(section)}`, signal)
}

export function fetchSeries(metric: string, signal?: AbortSignal) {
  return getJson<DashboardSeries>(
    `/dashboard/v1/series/${encodeURIComponent(metric)}`,
    signal,
  )
}

export interface MemorySearchFilters {
  scope: string
  text: string
  memoryType: string
  status: string
  lifecycle: string
  minimumConfidence: number
  minimumUtility: number
}

export function fetchInspectorSearch(
  filters: MemorySearchFilters,
  token: string,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ scope: filters.scope, limit: '50' })
  if (filters.text) params.set('text', filters.text)
  if (filters.memoryType) params.append('memory_type', filters.memoryType)
  if (filters.status) params.append('status', filters.status)
  if (filters.lifecycle) params.append('lifecycle', filters.lifecycle)
  if (filters.minimumConfidence) {
    params.set('minimum_confidence', String(filters.minimumConfidence))
  }
  if (filters.minimumUtility) {
    params.set('minimum_utility', String(filters.minimumUtility))
  }
  return inspectorJson<MemoryInspectorCollection>(
    `/memory-inspector/v1/search?${params}`,
    token,
    signal,
  )
}

export function fetchInspectorDetail(
  memoryId: string,
  scope: string,
  token: string,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ scope })
  return inspectorJson<MemoryInspectorItem>(
    `/memory-inspector/v1/${encodeURIComponent(memoryId)}?${params}`,
    token,
    signal,
  )
}

export function fetchInspectorRelation(
  relation: 'timeline' | 'related',
  scope: string,
  subject: string,
  token: string,
  excludeId?: string,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ scope, subject })
  if (excludeId) params.set('exclude_id', excludeId)
  return inspectorJson<MemoryInspectorCollection>(
    `/memory-inspector/v1/${relation}?${params}`,
    token,
    signal,
  )
}

export function runMemoryAction<T>(
  path: string,
  token: string,
  body: Record<string, unknown>,
) {
  return inspectorMutation<T>(path, token, body)
}

export function fetchSkillLab(token: string, signal?: AbortSignal) {
  return inspectorJson<{
    status: 'available' | 'empty'
    items: SkillLabListItem[]
    count: number
    reason: string | null
  }>('/skill-lab/v1/skills', token, signal)
}

export function fetchSkillDetail(reference: string, token: string, signal?: AbortSignal) {
  return inspectorJson<SkillLabDetail>(
    `/skill-lab/v1/skills/${encodeURIComponent(reference)}`,
    token,
    signal,
  )
}

export async function compareSkills(
  leftRef: string,
  rightRef: string,
  token: string,
  signal?: AbortSignal,
) {
  const response = await fetch('/skill-lab/v1/compare', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...tokenHeaders(token),
    },
    body: JSON.stringify({ left_ref: leftRef, right_ref: rightRef }),
    signal,
  })
  if (!response.ok) {
    throw new DashboardApiError(response.status, response.headers.get('Retry-After'))
  }
  return response.json() as Promise<SkillLabComparison>
}

export function runSkillLabAction<T>(
  path: string,
  token: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
) {
  return inspectorMutation<T>(path, token, body, idempotencyKey)
}
