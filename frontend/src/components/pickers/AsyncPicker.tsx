import { useQuery } from '@tanstack/react-query'
import { ChevronsUpDown, X } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

interface AsyncPickerProps<T> {
  value: T | null
  onChange: (value: T | null) => void
  /** Query key prefix; the search text is appended. */
  queryKey: string
  fetcher: (search: string) => Promise<T[]>
  getKey: (item: T) => string | number
  /** Text shown in the closed input. */
  label: (item: T) => string
  /** Row content in the dropdown. */
  option?: (item: T) => ReactNode
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
  id?: string
  className?: string
}

/** Searchable single-select that loads options from the server as the user types. */
export function AsyncPicker<T>({
  value,
  onChange,
  queryKey,
  fetcher,
  getKey,
  label,
  option,
  placeholder,
  disabled,
  invalid,
  id,
  className,
}: AsyncPickerProps<T>) {
  const { t } = useTranslation()
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [debounced, setDebounced] = useState('')
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(text.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [text])

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const query = useQuery({
    queryKey: [queryKey, 'picker', debounced],
    queryFn: () => fetcher(debounced),
    enabled: open,
    staleTime: 15_000,
  })
  const items = query.data ?? []

  const choose = (item: T) => {
    onChange(item)
    setOpen(false)
    setText('')
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open && (event.key === 'ArrowDown' || event.key === 'Enter')) {
      setOpen(true)
      event.preventDefault()
      return
    }
    if (event.key === 'ArrowDown') {
      setActive((i) => Math.min(i + 1, items.length - 1))
      event.preventDefault()
    } else if (event.key === 'ArrowUp') {
      setActive((i) => Math.max(i - 1, 0))
      event.preventDefault()
    } else if (event.key === 'Enter' && items[active]) {
      choose(items[active])
      event.preventDefault()
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={root} className={cn('relative', className)}>
      <div
        className={cn(
          'flex h-10 items-center rounded-lg border bg-surface px-3 text-sm shadow-xs',
          'focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/40',
          invalid ? 'border-danger' : 'border-border',
          disabled && 'bg-surface-muted opacity-60',
        )}
      >
        <input
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-invalid={invalid}
          disabled={disabled}
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted"
          placeholder={value && !open ? label(value) : (placeholder ?? t('common.searchPlaceholder'))}
          value={open ? text : value ? label(value) : ''}
          onFocus={() => {
            setOpen(true)
            setActive(0)
          }}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
        />
        {value && !disabled ? (
          <button
            type="button"
            aria-label={t('common.clear')}
            className="text-muted hover:text-foreground"
            onClick={() => {
              onChange(null)
              setText('')
            }}
          >
            <X className="size-4" />
          </button>
        ) : (
          <ChevronsUpDown className="size-4 text-muted" aria-hidden />
        )}
      </div>
      {open && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-border bg-surface p-1 shadow-lg"
        >
          {query.isPending && <li className="px-3 py-2 text-sm text-muted">{t('common.loading')}</li>}
          {!query.isPending && items.length === 0 && (
            <li className="px-3 py-2 text-sm text-muted">{t('common.noResults')}</li>
          )}
          {items.map((item, index) => (
            <li
              key={getKey(item)}
              role="option"
              aria-selected={index === active}
              onMouseEnter={() => setActive(index)}
              onMouseDown={(e) => {
                e.preventDefault()
                choose(item)
              }}
              className={cn(
                'cursor-pointer rounded-md px-3 py-2 text-sm',
                index === active && 'bg-primary-soft',
              )}
            >
              {option ? option(item) : label(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
