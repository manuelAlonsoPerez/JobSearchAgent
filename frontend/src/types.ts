export type Outcome = 'accept' | 'reject' | 'needs_confirmation'

export type ApplyStatus =
  | 'applied'
  | 'failed'
  | 'manual'
  | 'external'
  | 'skipped'
  | 'dry_run_ready'

export type ReviewStatus = 'pending' | 'done'

export type Job = {
  dedup_key: string
  site: string
  source_job_id: string | null
  url: string
  title: string | null
  company: string | null
  location: string | null
  employment_type: string | null
  easy_apply: boolean
  apply_url: string | null
  profile_id: string | null
  outcome: Outcome | null
  fit_score: number | null
  review_reason: string | null
  apply_status: ApplyStatus | null
  attempts: number
  discovered_at: string
  updated_at: string
  applied_at: string | null
  raw_description?: string | null
}

export type ReviewItem = {
  id: number
  url: string
  reason: string
  link: string | null
  status: ReviewStatus
  created_at: string
  dedup_key: string | null
  source_job_id: string | null
  title: string | null
  company: string | null
  location: string | null
  employment_type: string | null
  outcome: Outcome | null
  fit_score: number | null
  apply_status: ApplyStatus | null
  easy_apply: boolean | null
  profile_id: string | null
}

export type Stats = {
  jobs: number
  outcomes: Record<string, number>
  apply_statuses: Record<string, number>
  review_pending: number
  review_done: number
  review_total: number
}
