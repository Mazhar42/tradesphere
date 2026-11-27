import { Link } from '@tanstack/react-router'
import { AlertTriangle, SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

/** Shown by the router for an address that matches no route. */
export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <Card className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-3 px-6 py-12 text-center">
      <SearchX className="size-9 text-muted" aria-hidden />
      <h1 className="text-lg font-semibold">{t('notFound.title')}</h1>
      <p className="text-sm text-muted">{t('notFound.body')}</p>
      <Link to="/" className="text-sm font-medium text-primary underline">
        {t('notFound.home')}
      </Link>
    </Card>
  )
}

/**
 * Shown when a screen throws while rendering or its code cannot be downloaded (for example
 * after a new release). It uses nothing from the router or the session, and does not show the
 * error text: that is for developers, and the browser console already has it.
 */
export function CrashPage() {
  const { t } = useTranslation()
  return (
    <Card className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-3 px-6 py-12 text-center">
      <div role="alert" className="flex flex-col items-center gap-3">
        <AlertTriangle className="size-9 text-danger" aria-hidden />
        <h1 className="text-lg font-semibold">{t('crash.title')}</h1>
        <p className="text-sm text-muted">{t('crash.body')}</p>
      </div>
      <Button variant="outline" onClick={() => window.location.reload()}>
        {t('crash.reload')}
      </Button>
    </Card>
  )
}
