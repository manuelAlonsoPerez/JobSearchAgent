import type { ApplyStatus, ReviewStatus } from '../types'

type Tone =
  | 'accept'
  | 'reject'
  | 'review'
  | 'manual'
  | 'muted'
  | 'applied'
  | 'external'

const OUTCOME_TONE: Record<string, Tone> = {
  accept: 'accept',
  reject: 'reject',
  needs_confirmation: 'review',
  eligible: 'accept',
  ineligible: 'reject',
  unknown: 'review',
}

const APPLY_TONE: Record<string, Tone> = {
  applied: 'applied',
  failed: 'reject',
  manual: 'manual',
  external: 'external',
  skipped: 'muted',
  dry_run_ready: 'review',
  rejected: 'reject',
}

function Badge({ tone, children }: { tone: Tone; children: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function OutcomeBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <Badge tone="muted">untriaged</Badge>
  return <Badge tone={OUTCOME_TONE[value] ?? 'muted'}>{value.replaceAll('_', ' ')}</Badge>
}

export function ApplyBadge({ value }: { value: ApplyStatus | null | undefined }) {
  if (!value) return <Badge tone="muted">none</Badge>
  return <Badge tone={APPLY_TONE[value] ?? 'muted'}>{value.replaceAll('_', ' ')}</Badge>
}

const REVIEW_TONE: Record<ReviewStatus, Tone> = {
  pending: 'manual',
  applied: 'applied',
  rejected: 'reject',
  done: 'applied',
}

export function ReviewBadge({ value }: { value: ReviewStatus }) {
  const label = value === 'done' ? 'applied' : value
  return <Badge tone={REVIEW_TONE[value] ?? 'muted'}>{label}</Badge>
}

const EMPLOYMENT_TONE: Record<string, Tone> = {
  contract: 'manual',
  full_time: 'applied',
  part_time: 'external',
  temporary: 'review',
  internship: 'muted',
}

export function EmploymentBadge({ value }: { value: string | null | undefined }) {
  if (!value || value === 'unknown') return null
  return (
    <Badge tone={EMPLOYMENT_TONE[value] ?? 'external'}>
      {value.replaceAll('_', ' ')}
    </Badge>
  )
}
