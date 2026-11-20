import { Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  delay?: number
}

/** Debounced search box: updates the parent after the user pauses typing. */
export function SearchInput({ value, onChange, placeholder, className, delay = 300 }: SearchInputProps) {
  const { t } = useTranslation()
  const [text, setText] = useState(value)

  useEffect(() => {
    if (text === value) return
    const timer = window.setTimeout(() => onChange(text), delay)
    return () => window.clearTimeout(timer)
  }, [text, value, delay, onChange])

  return (
    <div className={cn('relative w-full sm:w-72', className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <Input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder ?? t('common.searchPlaceholder')}
        aria-label={t('common.search')}
        className="pl-9"
      />
    </div>
  )
}
