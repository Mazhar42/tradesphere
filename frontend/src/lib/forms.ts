import type { FieldValues, Path, UseFormSetError } from 'react-hook-form'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'

/**
 * Map a problem+json error onto form fields when possible; otherwise show a toast.
 * Returns true when at least one field error was applied.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  knownFields: readonly string[],
): boolean {
  if (!(error instanceof ApiError)) {
    toast.error(error instanceof Error ? error.message : String(error))
    return false
  }
  let applied = false
  for (const fieldError of error.fieldErrors) {
    const root = fieldError.field.split('.')[0] ?? ''
    if (knownFields.includes(root)) {
      setError(root as Path<T>, { type: 'server', message: fieldError.message })
      applied = true
    }
  }
  if (!applied) toast.error(error.message)
  return applied
}

/** "" -> null, otherwise trimmed text. */
export function blankToNull(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim()
  return trimmed === '' ? null : trimmed
}

export const MONEY_PATTERN = /^\d{1,16}(\.\d{1,2})?$/
export const QTY_PATTERN = /^\d{1,15}(\.\d{1,3})?$/
