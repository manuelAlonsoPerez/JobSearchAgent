import type { ApplyStatus, Outcome, ReviewStatus } from '../types'

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
}

const APPLY_TONE: Record<string, Tone> = {
  applied: 'applied',
  failed: 'reject',
  manual: 'manual',
  external: 'external',
  skipped: 'muted',
  dry_run_ready: 'review',
}

function Badge({ tone, children }: { tone: Tone; children: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function OutcomeBadge({ value }: { value: Outcome | null | undefined }) {
  if (!value) return <Badge tone="muted">untriaged</Badge>
  return <Badge tone={OUTCOME_TONE[value] ?? 'muted'}>{value.replaceAll('_', ' ')}</Badge>
}

export function ApplyBadge({ value }: { value: ApplyStatus | null | undefined }) {
  if (!value) return <Badge tone="muted">none</Badge>
  return <Badge tone={APPLY_TONE[value] ?? 'muted'}>{value.replaceAll('_', ' ')}</Badge>
}

export function ReviewBadge({ value }: { value: ReviewStatus }) {
  return <Badge tone={value === 'pending' ? 'manual' : 'applied'}>{value}</Badge>
}
