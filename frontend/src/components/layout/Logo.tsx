import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

export function Logo({ inverted = false, compact = false }: { inverted?: boolean; compact?: boolean }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--color-primary)" />
        <path
          d="M9 11h14M16 11v12M11 17l5-6 5 6"
          stroke="#fff"
          strokeWidth="2.5"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {!compact && (
        <div className="leading-tight">
          <div className={cn('font-semibold', inverted ? 'text-white' : 'text-foreground')}>
            {t('app.name')}
          </div>
          <div className={cn('text-xs', inverted ? 'text-sidebar-muted' : 'text-muted')}>
            {t('app.tagline')}
          </div>
        </div>
      )}
    </div>
  )
}
