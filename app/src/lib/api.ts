export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const DEFAULT_TIMEOUT = 15000

/**
 * fetch wrapper that applies a timeout, parses JSON, and turns non-2xx
 * responses into ApiError so callers can branch on `status`.
 */
export async function api<T>(path: string, init: RequestInit & { timeout?: number } = {}): Promise<T> {
  const { timeout = DEFAULT_TIMEOUT, ...rest } = init
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(path, { ...rest, signal: controller.signal })
    const text = await res.text()
    const data = text ? JSON.parse(text) : null

    if (!res.ok) {
      throw new ApiError(data?.error || `Request failed with ${res.status}`, res.status)
    }

    return data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The request timed out. The server may be unreachable.', 408)
    }
    throw new ApiError(
      error instanceof Error ? error.message : 'Network request failed',
      0
    )
  } finally {
    clearTimeout(timer)
  }
}

export const apiJson = <T>(path: string, method: string, body: unknown) =>
  api<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
