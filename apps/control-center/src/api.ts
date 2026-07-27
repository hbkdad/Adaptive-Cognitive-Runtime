import type { DashboardPayload, DashboardSeries } from './types'

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

export function fetchDashboard(section: string, signal?: AbortSignal) {
  return getJson<DashboardPayload>(`/dashboard/v1/${encodeURIComponent(section)}`, signal)
}

export function fetchSeries(metric: string, signal?: AbortSignal) {
  return getJson<DashboardSeries>(
    `/dashboard/v1/series/${encodeURIComponent(metric)}`,
    signal,
  )
}
