export const PAGE_SIZE = 20
export const PAGE_SIZE_OPTIONS = [10, 20, 25, 50] as const

export function paginate<T>(
  items: T[],
  page: number,
  pageSize: number = PAGE_SIZE,
) {
  const size = Math.max(1, pageSize)
  const pageCount = Math.max(1, Math.ceil(items.length / size) || 1)
  const current = Math.min(Math.max(1, page), pageCount)
  const start = (current - 1) * size
  return {
    page: current,
    pageCount,
    pageSize: size,
    slice: items.slice(start, start + size),
    from: items.length === 0 ? 0 : start + 1,
    to: Math.min(start + size, items.length),
    total: items.length,
  }
}

export function pageNumbers(
  current: number,
  pageCount: number,
): Array<number | 'gap'> {
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, index) => index + 1)
  }

  const windowStart = Math.max(2, current - 1)
  const windowEnd = Math.min(pageCount - 1, current + 1)
  const items: Array<number | 'gap'> = [1]
  if (windowStart > 2) items.push('gap')
  for (let page = windowStart; page <= windowEnd; page += 1) {
    items.push(page)
  }
  if (windowEnd < pageCount - 1) items.push('gap')
  items.push(pageCount)
  return items
}
