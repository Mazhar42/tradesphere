import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface FieldProps {
  label: string
  htmlFor?: string
  error?: string
  hint?: string
  optional?: boolean
  optionalLabel?: string
  className?: string
  children: ReactNode
}

export function Field({
  label,
  htmlFor,
  error,
  hint,
  optional,
  optionalLabel = 'optional',
  className,
  children,
}: FieldProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
        {label}
        {optional && <span className="ml-1 text-xs font-normal text-muted">({optionalLabel})</span>}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : (
        hint && <p className="text-xs text-muted">{hint}</p>
      )}
    </div>
  )
}
