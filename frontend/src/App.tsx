import { useCallback, useEffect, useState } from 'react'
import { fetchStats } from './api'
import { JobsPage } from './pages/JobsPage'
import { ReviewPage } from './pages/ReviewPage'
import type { Stats } from './types'

type View = 'jobs' | 'review'

function viewFromHash(): View {
  return window.location.hash === '#review' ? 'review' : 'jobs'
}

export default function App() {
  const [view, setView] = useState<View>(viewFromHash)
  const [stats, setStats] = useState<Stats | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)

  const refreshStats = useCallback(() => {
    fetchStats()
      .then((payload) => {
        setStats(payload)
        setApiError(null)
      })
      .catch((err: unknown) => {
        setApiError(err instanceof Error ? err.message : 'API unavailable')
      })
  }, [])

  useEffect(() => {
    refreshStats()
    const timer = window.setInterval(refreshStats, 10_000)
    const onHash = () => setView(viewFromHash())
    window.addEventListener('hashchange', onHash)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('hashchange', onHash)
    }
  }, [refreshStats])

  const open = (next: View) => {
    window.location.hash = next === 'review' ? 'review' : 'jobs'
    setView(next)
  }

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="mark">JS</span>
          <div>
            <strong>JobSearchAgent</strong>
            <span>local · no account</span>
          </div>
        </div>
        <nav>
          <button
            type="button"
            className={view === 'jobs' ? 'is-on' : ''}
            onClick={() => open('jobs')}
          >
            Analyzed jobs
            <span>{stats?.jobs ?? '—'}</span>
          </button>
          <button
            type="button"
            className={view === 'review' ? 'is-on' : ''}
            onClick={() => open('review')}
          >
            Manual apply
            <span>{stats?.review_pending ?? '—'}</span>
          </button>
        </nav>
        <dl className="rail-stats">
          <div>
            <dt>Accept</dt>
            <dd>{stats?.outcomes.accept ?? 0}</dd>
          </div>
          <div>
            <dt>Reject</dt>
            <dd>{stats?.outcomes.reject ?? 0}</dd>
          </div>
          <div>
            <dt>Queue</dt>
            <dd>{stats?.review_total ?? 0}</dd>
          </div>
        </dl>
        <p className="rail-note">
          Reads the local SQLite file. Start the API with{' '}
          <code>python -m jobsearchagent serve</code>.
        </p>
      </aside>
      <main className="main">
        {apiError ? <p className="banner">{apiError}</p> : null}
        {view === 'jobs' ? (
          <JobsPage onChanged={refreshStats} />
        ) : (
          <ReviewPage onChanged={refreshStats} />
        )}
      </main>
    </div>
  )
}
