export function formatWhen(value: string | null | undefined): string {
  if (!value) return '—'
  const iso = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatFit(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return score.toFixed(2)
}

export function label(value: string | null | undefined, fallback = '—'): string {
  const trimmed = value?.trim()
  return trimmed ? trimmed : fallback
}

export function jevChoice(
  answer: { choice?: string; answer?: string } | string | null | undefined,
): string | null {
  if (!answer) return null
  if (typeof answer === 'string') return answer.trim() || null
  const value = answer.choice ?? answer.answer
  return value?.trim() ? value : null
}

export function formatConfidence(value: number | null | undefined): string | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null
  const percent = value <= 1 ? value * 100 : value
  return `${percent.toFixed(0)}%`
}

export function jevReason(
  answer: {
    rationale?: string
    reasoning?: string
    reason?: string
    explanation?: string
  } | string | null | undefined,
): string | null {
  if (!answer) return null
  if (typeof answer === 'string') return answer.trim() || null
  const value =
    answer.rationale || answer.reasoning || answer.reason || answer.explanation
  return value?.trim() ? value : null
}
