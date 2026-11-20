import { AlertTriangle, ShieldOff } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Toolbar({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
      {children}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <Card className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <AlertTriangle className="size-8 text-danger" aria-hidden />
      <p className="font-medium">{t('common.somethingWrong')}</p>
      <p className="max-w-md text-sm text-muted">
        {error instanceof Error ? error.message : String(error)}
      </p>
      {onRetry && (
        <Button variant="outline" onClick={onRetry}>
          {t('common.retry')}
        </Button>
      )}
    </Card>
  )
}

export function Forbidden() {
  const { t } = useTranslation()
  return (
    <Card className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-3 px-6 py-12 text-center">
      <ShieldOff className="size-9 text-muted" aria-hidden />
      <h1 className="text-lg font-semibold">{t('forbidden.title')}</h1>
      <p className="text-sm text-muted">{t('forbidden.body')}</p>
    </Card>
  )
}
