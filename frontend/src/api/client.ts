/**
 * Typed API client (generated from FastAPI's OpenAPI: `pnpm gen:api`).
 *
 * - The access token lives in memory only (never localStorage).
 * - The refresh token is an httpOnly cookie scoped to /api/v1/auth.
 * - On a 401 the request is retried once after a single-flight refresh.
 */

import createClient from 'openapi-fetch'

import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type Me = Schemas['MeRead']

let accessToken: string | null = null
let refreshInFlight: Promise<Schemas['TokenResponse'] | null> | null = null
let onSessionExpired: (() => void) | null = null

// The dealership this tab is showing. sessionStorage is per tab, which is the point: another
// tab switching dealership must not move this one into different books.
const TENANT_KEY = 'ts.tenant'

export function tabTenant(): number | null {
  try {
    return Number(window.sessionStorage.getItem(TENANT_KEY)) || null
  } catch {
    return null
  }
}

export function setTabTenant(id: number | null): void {
  try {
    if (id === null) window.sessionStorage.removeItem(TENANT_KEY)
    else window.sessionStorage.setItem(TENANT_KEY, String(id))
  } catch {
    // Storage unavailable: the server falls back to the first dealership.
  }
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler
}

function postRefresh(tenantId: number | null): Promise<Response> {
  return fetch('/api/v1/auth/refresh', {
    method: 'POST',
    credentials: 'same-origin',
    ...(tenantId
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenant_id: tenantId }) }
      : {}),
  })
}

/** Exchange the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<Schemas['TokenResponse'] | null> {
  refreshInFlight ??= postRefresh(tabTenant())
    .then(async (first) => {
      // Access to this tab's dealership may have been removed: fall back to another one.
      const response = first.status === 403 && tabTenant() ? await postRefresh(null) : first
      if (!response.ok) {
        accessToken = null
        return null
      }
      const body = (await response.json()) as Schemas['TokenResponse']
      accessToken = body.access_token
      setTabTenant(body.user.tenant?.id ?? null)
      return body
    })
    .catch(() => null)
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

function withAuth(request: Request): Request {
  if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`)
  return request
}

async function problemCode(response: Response): Promise<string | undefined> {
  try {
    return ((await response.json()) as { code?: string }).code
  } catch {
    return undefined
  }
}

async function authFetch(input: Request): Promise<Response> {
  const retry = input.clone()
  const response = await fetch(withAuth(input))
  const expired =
    response.status === 401 ||
    (response.status === 403 && (await problemCode(response.clone())) === 'membership_inactive')
  if (!expired || new URL(input.url).pathname.startsWith('/api/v1/auth/')) {
    return response
  }
  const refreshed = await refreshSession()
  if (!refreshed) {
    onSessionExpired?.()
    return response
  }
  return fetch(withAuth(retry))
}

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'same-origin',
  fetch: authFetch,
})

// --- Errors ---------------------------------------------------------------------
export interface FieldError {
  field: string
  message: string
}

interface Problem {
  title?: string
  detail?: string
  code?: string
  status?: number
  errors?: FieldError[]
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: FieldError[]

  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title ?? `Request failed (${status})`)
    this.status = status
    this.code = problem.code ?? 'error'
    this.fieldErrors = problem.errors ?? []
  }
}

/** Turn an openapi-fetch result into data-or-throw, for TanStack Query. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response }
  try {
    result = await promise
  } catch {
    throw new ApiError(0, { detail: 'Network error. Check your connection.', code: 'network' })
  }
  if (!result.response.ok) {
    throw new ApiError(result.response.status, (result.error ?? {}) as Problem)
  }
  return result.data as T
}
