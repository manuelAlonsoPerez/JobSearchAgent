import { useCallback, useEffect, useState } from 'react'
import { fetchJob, fetchReviewQueue, setReviewStatus } from '../api'
import { Pagination } from '../components/Pagination'
import { ApplyBadge, EmploymentBadge, OutcomeBadge, ReviewBadge } from '../components/StatusBadge'
import { formatWhen, label } from '../format'
import { PAGE_SIZE, paginate } from '../pagination'
import type { Job, ReviewItem, ReviewStatus } from '../types'

type ReviewPageProps = {
  onChanged: () => void
}

const FILTERS: { value: ReviewStatus | 'all'; label: string }[] = [
  { value: 'pending', label: 'Pending' },
  { value: 'done', label: 'Done' },
  { value: 'all', label: 'All' },
]

export function ReviewPage({ onChanged }: ReviewPageProps) {
  const [filter, setFilter] = useState<ReviewStatus | 'all'>('pending')
  const [items, setItems] = useState<ReviewItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const load = useCallback(() => {
    setLoading(items.length === 0)
    return fetchReviewQueue(filter)
      .then((payload) => {
        setItems(payload.items)
        setPage((current) => {
          const nextCount = Math.max(1, Math.ceil(payload.items.length / pageSize) || 1)
          return Math.min(current, nextCount)
        })
        setError(null)
        onChanged()
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load review queue')
      })
      .finally(() => setLoading(false))
  }, [filter, onChanged, pageSize])

  useEffect(() => {
    void load()
  }, [load])

  const selected = items.find((item) => item.id === selectedId) ?? null
  const paging = paginate(items, page, pageSize)
  const visible = paging.slice

  useEffect(() => {
    if (!selected?.dedup_key && !selected?.source_job_id) {
      setDetail(null)
      return
    }
    const id = selected.source_job_id || selected.dedup_key
    if (!id) return
    let cancelled = false
    fetchJob(id)
      .then((job) => {
        if (!cancelled) setDetail(job)
      })
      .catch(() => {
        if (!cancelled) setDetail(null)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  const mark = async (item: ReviewItem, status: ReviewStatus) => {
    setBusyId(item.id)
    try {
      await setReviewStatus(item.id, status)
      await load()
      if (filter === 'pending' && status === 'done' && selectedId === item.id) {
        setSelectedId(null)
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not update review item')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Manual apply</h1>
          <p>
            Jobs parked in <code>review_queue</code> — finish the application on LinkedIn,
            then mark done.
          </p>
        </div>
        <div className="filters">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={filter === option.value ? 'chip is-on' : 'chip'}
              onClick={() => {
                setFilter(option.value)
                setPage(1)
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      {error ? <p className="banner">{error}</p> : null}

      <div className={`workspace ${selected ? 'with-detail' : ''}`}>
        <div className="list-stack">
        <div className="table-wrap">
          {loading && items.length === 0 ? <p className="empty">Loading queue…</p> : null}
          {!loading && items.length === 0 && !error ? (
            <p className="empty">
              {filter === 'pending'
                ? 'Nothing waiting. Accepted Easy Apply jobs land here after fill → Save.'
                : 'No items in this filter.'}
            </p>
          ) : null}
          {items.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Reason</th>
                  <th>Status</th>
                  <th>Queued</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr
                    key={item.id}
                    className={item.id === selectedId ? 'is-selected' : ''}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <td>
                      <strong>{label(item.title, 'Untitled role')}</strong>
                      <span className="sub">{label(item.company, item.url)}</span>
                    </td>
                    <td>
                      <span className="clamp">{item.reason}</span>
                    </td>
                    <td>
                      <ReviewBadge value={item.status} />
                    </td>
                    <td className="muted">{formatWhen(item.created_at)}</td>
                    <td className="actions">
                      <a
                        className="button small"
                        href={item.link || item.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(event) => event.stopPropagation()}
                      >
                        Open
                      </a>
                      {item.status === 'pending' ? (
                        <button
                          type="button"
                          className="button small"
                          disabled={busyId === item.id}
                          onClick={(event) => {
                            event.stopPropagation()
                            void mark(item, 'done')
                          }}
                        >
                          Mark done
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="ghost small"
                          disabled={busyId === item.id}
                          onClick={(event) => {
                            event.stopPropagation()
                            void mark(item, 'pending')
                          }}
                        >
                          Reopen
                        </button>
                      )}
                    </td>
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
          <ReviewDetail
            item={selected}
            job={detail}
            busy={busyId === selected.id}
            onClose={() => setSelectedId(null)}
            onMark={(status) => void mark(selected, status)}
          />
        ) : null}
      </div>
    </section>
  )
}

function ReviewDetail({
  item,
  job,
  busy,
  onClose,
  onMark,
}: {
  item: ReviewItem
  job: Job | null
  busy: boolean
  onClose: () => void
  onMark: (status: ReviewStatus) => void
}) {
  const href = item.link || item.url
  return (
    <aside className="detail">
      <header>
        <div>
          <h2>{label(item.title, 'Untitled role')}</h2>
          <p>
            {label(item.company, 'Unknown company')}
            {item.location ? ` · ${item.location}` : ''}
          </p>
        </div>
        <button type="button" className="ghost" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="detail-meta">
        <ReviewBadge value={item.status} />
        <OutcomeBadge value={item.outcome} />
        <ApplyBadge value={item.apply_status} />
        <EmploymentBadge value={item.employment_type} />
      </div>
      <section>
        <h3>Why it is here</h3>
        <pre className="reason">{item.reason}</pre>
      </section>
      {job?.raw_description ? (
        <section>
          <h3>Description</h3>
          <pre className="description">{job.raw_description}</pre>
        </section>
      ) : null}
      <footer>
        <a className="button" href={href} target="_blank" rel="noreferrer">
          Finish on LinkedIn
        </a>
        {item.status === 'pending' ? (
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => onMark('done')}
          >
            Mark done
          </button>
        ) : (
          <button
            type="button"
            className="ghost"
            disabled={busy}
            onClick={() => onMark('pending')}
          >
            Reopen
          </button>
        )}
      </footer>
    </aside>
  )
}
