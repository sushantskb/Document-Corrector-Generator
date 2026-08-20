'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '@/lib/api'

export interface ResourceState<T> {
  data: T | null
  loading: boolean
  error: ApiError | null
  notFound: boolean
  refresh: () => Promise<void>
}

/**
 * Fetches `path` on mount and whenever it changes.
 * `pollMs` re-fetches on an interval; pass 0 or null to disable.
 */
export function useResource<T>(path: string | null, pollMs: number | null = null): ResourceState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(Boolean(path))
  const [error, setError] = useState<ApiError | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(
    async (showSpinner: boolean) => {
      if (!path) return
      if (showSpinner) setLoading(true)

      try {
        const result = await api<T>(path)
        if (!mounted.current) return
        setData(result)
        setError(null)
      } catch (err) {
        if (!mounted.current) return
        setError(err instanceof ApiError ? err : new ApiError('Request failed', 0))
      } finally {
        if (mounted.current) setLoading(false)
      }
    },
    [path]
  )

  useEffect(() => {
    load(true)
  }, [load])

  useEffect(() => {
    if (!pollMs || !path) return
    const timer = setInterval(() => load(false), pollMs)
    return () => clearInterval(timer)
  }, [pollMs, path, load])

  return {
    data,
    loading,
    error,
    notFound: error?.status === 404,
    refresh: useCallback(() => load(false), [load]),
  }
}
