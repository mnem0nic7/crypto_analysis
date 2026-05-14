import { useCallback, useEffect, useRef, useState } from 'react'

export interface AutoRefreshState {
  lastRefreshed: Date | null
  isLoading: boolean
  triggerRefresh: () => void
}

export function useAutoRefresh(
  fetchFn: () => Promise<void>,
  intervalMs: number = 30_000
): AutoRefreshState {
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const fetchRef = useRef(fetchFn)
  fetchRef.current = fetchFn

  const run = useCallback(async () => {
    setIsLoading(true)
    try {
      await fetchRef.current()
      setLastRefreshed(new Date())
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    run()
    const id = setInterval(run, intervalMs)
    return () => clearInterval(id)
  }, [run, intervalMs])

  return { lastRefreshed, isLoading, triggerRefresh: run }
}
