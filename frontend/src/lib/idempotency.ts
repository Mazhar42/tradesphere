import { useCallback, useRef } from 'react'

import { ApiError } from '@/api/client'

export function newKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`
}

/**
 * One Idempotency-Key per submit attempt.
 *
 * The key is kept while the outcome is unknown (network error, 5xx), so a retry of the
 * same request cannot post twice. Once the server gave a definitive answer (success or a
 * 4xx that rolled everything back) the next attempt gets a fresh key, so the user can edit
 * the form and submit again.
 */
export function useIdempotencyKey() {
  const ref = useRef(newKey())
  const current = useCallback(() => ref.current, [])
  const settle = useCallback((error?: unknown) => {
    const unknownOutcome = error instanceof ApiError ? error.status === 0 || error.status >= 500 : error !== undefined
    if (!unknownOutcome) ref.current = newKey()
  }, [])
  return { current, settle }
}
