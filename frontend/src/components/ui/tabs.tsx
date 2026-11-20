import { cn } from '@/lib/utils'

interface TabsProps<T extends string> {
  value: T
  onChange: (value: T) => void
  items: { value: T; label: string }[]
}

export function Tabs<T extends string>({ value, onChange, items }: TabsProps<T>) {
  return (
    <div role="tablist" className="inline-flex rounded-lg bg-surface-muted p-1">
      {items.map((item) => (
        <button
          key={item.value}
          role="tab"
          type="button"
          aria-selected={value === item.value}
          onClick={() => onChange(item.value)}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            value === item.value
              ? 'bg-surface text-foreground shadow-xs'
              : 'text-muted hover:text-foreground',
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}
