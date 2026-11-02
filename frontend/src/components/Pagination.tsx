import { PAGE_SIZE_OPTIONS, pageNumbers, paginate } from '../pagination'

type PaginationProps = {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  onPageSize: (pageSize: number) => void
  noun: string
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  noun,
}: PaginationProps) {
  if (total === 0) return null
  const paging = paginate(Array.from({ length: total }), page, pageSize)
  const atFirst = paging.page <= 1
  const atLast = paging.page >= paging.pageCount

  return (
    <nav className="pager" aria-label="Pagination">
      <p>
        {paging.from}–{paging.to} of {paging.total} {noun}
      </p>
      <div className="pager-pages" role="group" aria-label="Pages">
        <button
          type="button"
          className="ghost small pager-arrow"
          disabled={atFirst}
          aria-label="Previous page"
          onClick={() => onPage(paging.page - 1)}
        >
          ‹
        </button>
        {pageNumbers(paging.page, paging.pageCount).map((item, index) =>
          item === 'gap' ? (
            <span key={`gap-${index}`} className="pager-gap">
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              className={item === paging.page ? 'chip is-on small' : 'ghost small'}
              aria-current={item === paging.page ? 'page' : undefined}
              onClick={() => onPage(item)}
            >
              {item}
            </button>
          ),
        )}
        <button
          type="button"
          className="ghost small pager-arrow"
          disabled={atLast}
          aria-label="Next page"
          onClick={() => onPage(paging.page + 1)}
        >
          ›
        </button>
      </div>
      <label className="pager-size">
        Per page
        <select
          value={pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
    </nav>
  )
}
