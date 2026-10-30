import { useEffect, useMemo, useState } from 'react'
import { fetchJob, fetchJobs } from '../api'
import { Pagination } from '../components/Pagination'
import { ApplyBadge, EmploymentBadge, OutcomeBadge } from '../components/StatusBadge'
import {
  formatConfidence,
  formatFit,
  formatWhen,
  jevChoice,
  jevReason,
  label,
} from '../format'
import type { Job } from '../types'
import { PAGE_SIZE, paginate } from '../pagination'

type JobsPageProps = {
  onChanged: () => void
}

const OUTCOMES = [
  { value: '', label: 'All outcomes' },
  { value: 'accept', label: 'Accept' },
  { value: 'reject', label: 'Reject' },
  { value: 'needs_confirmation', label: 'Needs confirmation' },
  { value: 'untriaged', label: 'Untriaged' },
]

const STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'manual', label: 'Manual' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'failed', label: 'Failed' },
  { value: 'external', label: 'External' },
  { value: 'applied', label: 'Applied' },
  { value: 'dry_run_ready', label: 'Dry-run ready' },
]

export function JobsPage({ onChanged }: JobsPageProps) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [outcome, setOutcome] = useState('')
  const [applyStatus, setApplyStatus] = useState('')
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 200)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    let cancelled = false
    setLoading(jobs.length === 0)
    fetchJobs({
      q: debounced,
      outcome: outcome || undefined,
      apply_status: applyStatus || undefined,
    })
      .then((payload) => {
        if (cancelled) return
        setJobs(payload.jobs)
        setPage(1)
        setError(null)
        onChanged()
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load jobs')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [debounced, outcome, applyStatus, onChanged])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    fetchJob(selectedId)
      .then((job) => {
        if (!cancelled) setDetail(job)
      })
      .catch(() => {
        if (!cancelled) setDetail(jobs.find((job) => job.dedup_key === selectedId) ?? null)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId, jobs])

  const selected = useMemo(
    () => jobs.find((job) => job.dedup_key === selectedId) ?? detail,
    [jobs, selectedId, detail],
  )
  const paging = paginate(jobs, page, pageSize)
  const visible = paging.slice

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Analyzed jobs</h1>
          <p>Everything JEV has seen, from the <code>jobs</code> table.</p>
        </div>
        <div className="filters">
          <input
            type="search"
            placeholder="Search title, company, location…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
            {OUTCOMES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={applyStatus}
            onChange={(event) => setApplyStatus(event.target.value)}
          >
            {STATUSES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error ? <p className="banner">{error}</p> : null}

      <div className={`workspace ${selected ? 'with-detail' : ''}`}>
        <div className="list-stack">
        <div className="table-wrap">
          {loading && jobs.length === 0 ? <p className="empty">Loading jobs…</p> : null}
          {!loading && jobs.length === 0 && !error ? (
            <p className="empty">
              No jobs yet. Run{' '}
              <code>PYTHONPATH=src python -m jobsearchagent run --dry-run</code>
            </p>
          ) : null}
          {jobs.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Location</th>
                  <th>Outcome</th>
                  <th>Fit</th>
                  <th>Apply</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((job) => (
                  <tr
                    key={job.dedup_key}
                    className={job.dedup_key === selectedId ? 'is-selected' : ''}
                    onClick={() => setSelectedId(job.dedup_key)}
                  >
                    <td>
                      <strong>{label(job.title, 'Untitled role')}</strong>
                      <span className="sub">{label(job.company, 'Unknown company')}</span>
                    </td>
                    <td>
                      {label(job.location)}
                      {job.employment_type && job.employment_type !== 'unknown' ? (
                        <span className="sub">
                          <EmploymentBadge value={job.employment_type} />
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <OutcomeBadge value={job.outcome} />
                    </td>
                    <td className="mono">{formatFit(job.fit_score)}</td>
                    <td>
                      <ApplyBadge value={job.apply_status} />
                    </td>
                    <td className="muted">{formatWhen(job.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
          <Pagination
            page={paging.page}
            pageSize={pageSize}
            total={paging.total}
            onPage={setPage}
            onPageSize={(size) => {
              setPageSize(size)
              setPage(1)
            }}
            noun="jobs"
          />
        </div>

        {selected ? (
          <JobDetail
            job={detail ?? selected}
            onClose={() => setSelectedId(null)}
          />
        ) : null}
      </div>
    </section>
  )
}

function JevSection({
  title,
  answer,
}: {
  title: string
  answer: Job['location_eligibility']
}) {
  return (
    <section>
      <h3>{title}</h3>
      {answer ? (
        <>
          <div className="detail-meta">
            <OutcomeBadge value={jevChoice(answer)} />
            {typeof answer === 'object' && formatConfidence(answer.confidence) ? (
              <span className="mono">{formatConfidence(answer.confidence)}</span>
            ) : null}
          </div>
          {jevReason(answer) ? <pre className="reason">{jevReason(answer)}</pre> : null}
        </>
      ) : (
        <p>Not saved with this job. The next triage run stores it.</p>
      )}
    </section>
  )
}

function JobDetail({ job, onClose }: { job: Job; onClose: () => void }) {
  const href = job.apply_url || job.url
  return (
    <aside className="detail">
      <header>
        <div>
          <h2>{label(job.title, 'Untitled role')}</h2>
          <p>
            {label(job.company, 'Unknown company')}
            {job.location ? ` · ${job.location}` : ''}
          </p>
        </div>
        <button type="button" className="ghost" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="detail-meta">
        <OutcomeBadge value={job.outcome} />
        <ApplyBadge value={job.apply_status} />
        {job.easy_apply ? <span className="badge badge-muted">Easy Apply</span> : null}
        <EmploymentBadge value={job.employment_type} />
        <span className="mono">fit {formatFit(job.fit_score)}</span>
      </div>
      {job.review_reason ? (
        <section>
          <h3>Review reason</h3>
          <pre className="reason">{job.review_reason}</pre>
        </section>
      ) : null}
      <JevSection title="Location outcome" answer={job.location_eligibility} />
      <JevSection title="Skills outcome" answer={job.skills_outcome} />
      <section>
        <h3>Description</h3>
        <pre className="description">{job.raw_description || 'No description stored.'}</pre>
      </section>
      <footer>
        <a className="button" href={href} target="_blank" rel="noreferrer">
          Open on LinkedIn
        </a>
        {job.source_job_id ? (
          <span className="mono muted">id {job.source_job_id}</span>
        ) : null}
      </footer>
    </aside>
  )
}
