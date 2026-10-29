import type { Job, ReviewItem, ReviewStatus, Stats } from './types'

const API_HINT =
  'Cannot reach the local API. In the project root run: PYTHONPATH=src python -m jobsearchagent serve'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new Error(API_HINT)
  }

  const contentType = response.headers.get('content-type') ?? ''
  const raw = await response.text()
  if (!contentType.includes('application/json')) {
    throw new Error(API_HINT)
  }

  const payload = raw ? (JSON.parse(raw) as T & { error?: string }) : ({} as T)
  if (!response.ok) {
    throw new Error(
      (payload as { error?: string }).error ?? `Request failed (${response.status})`,
    )
  }
  return payload
}

export function fetchStats(): Promise<Stats> {
  return request<Stats>('/api/stats')
}

export function fetchJobs(params: {
  q?: string
  outcome?: string
  apply_status?: string
}): Promise<{ jobs: Job[]; total: number }> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.outcome) search.set('outcome', params.outcome)
  if (params.apply_status) search.set('apply_status', params.apply_status)
  const query = search.toString()
  return request(`/api/jobs${query ? `?${query}` : ''}`)
}

export function fetchJob(id: string): Promise<Job> {
  return request<Job>(`/api/jobs/${encodeURIComponent(id)}`)
}

export function fetchReviewQueue(
  status: ReviewStatus | 'all' = 'pending',
): Promise<{ items: ReviewItem[]; total: number }> {
  return request(`/api/review-queue?status=${status}`)
}

export function setReviewStatus(
  id: number,
  status: ReviewStatus,
): Promise<ReviewItem> {
  return request<ReviewItem>(`/api/review-queue/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
}
