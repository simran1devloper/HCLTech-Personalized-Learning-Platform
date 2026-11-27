import type { ApiErrorBody } from '@/types/api'

// Empty means same origin: Vercel rewrites (vercel.json) or the Vite proxy (vite.config.ts) forward API paths to the backend.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
export const TOKEN_KEY = 'learnai_token' // same keys as the legacy frontend so existing sessions keep working
export const USER_KEY = 'learnai_user'

export class ApiError extends Error {
  constructor(message: string, public status: number, public code = 'error', public details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
  }
  get isNetwork() { return this.status === 0 }
  get isAuth() { return this.status === 401 }
}

let onUnauthorized: (() => void) | null = null
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }
export const getToken = () => localStorage.getItem(TOKEN_KEY)

/** Parses both error shapes the backend emits: v1 `{error:{code,message}}` and legacy `{detail}`. */
export function parseError(status: number, data: unknown): ApiError {
  const d = data as { error?: ApiErrorBody; detail?: unknown } | null
  if (d?.error) return new ApiError(d.error.message, status, d.error.code, d.error.details)
  if (typeof d?.detail === 'string') return new ApiError(d.detail, status)
  if (Array.isArray(d?.detail)) return new ApiError('Please check the highlighted fields.', status, 'validation_failed')
  return new ApiError(status >= 500 ? 'The server had a problem. Please try again.' : 'Request failed.', status)
}

export interface RequestOptions { method?: string; body?: unknown; signal?: AbortSignal; headers?: Record<string, string>; auth?: boolean }

export async function http<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...opts.headers }
  const token = getToken()
  if (token && opts.auth !== false) headers.Authorization = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(API_BASE + path, { method: opts.method ?? 'GET', headers, signal: opts.signal, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e // cancellation is not an error to display
    throw new ApiError('Unable to reach the server. Check your connection.', 0, 'network')
  }
  let data: unknown = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && opts.auth !== false && onUnauthorized) onUnauthorized()
    throw parseError(res.status, data)
  }
  return data as T
}
