import type { InputHTMLAttributes, Ref, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

const control =
  'w-full rounded-lg border border-border bg-surface px-3 text-sm text-foreground shadow-xs ' +
  'placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring/40 focus:border-ring ' +
  'disabled:opacity-60 disabled:bg-surface-muted aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger/20'

export function Input({
  className,
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input ref={ref} className={cn(control, 'h-10', className)} {...props} />
}

export function Textarea({
  className,
  ref,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> }) {
  return <textarea ref={ref} rows={3} className={cn(control, 'py-2', className)} {...props} />
}

export function Select({
  className,
  ref,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { ref?: Ref<HTMLSelectElement> }) {
  return (
    <select ref={ref} className={cn(control, 'h-10 pr-8', className)} {...props}>
      {children}
    </select>
  )
}

export function Checkbox({
  className,
  label,
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; ref?: Ref<HTMLInputElement> }) {
  return (
    <label className={cn('inline-flex cursor-pointer items-center gap-2 text-sm', className)}>
      <input
        ref={ref}
        type="checkbox"
        className="size-4 rounded border-border accent-[var(--color-primary)]"
        {...props}
      />
      <span>{label}</span>
    </label>
  )
}
