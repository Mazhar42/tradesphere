import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'
import { formatNumber } from '@/lib/format'

interface PaginationProps {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}

export function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const { t } = useTranslation()
  const { lang } = useLang()
  if (total === 0) return null
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)

  return (
    <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm">
      <span className="text-muted">
        {t('common.showing', {
          from: formatNumber(from, lang),
          to: formatNumber(to, lang),
          total: formatNumber(total, lang),
        })}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          aria-label={t('common.previous')}
        >
          <ChevronLeft className="size-4" />
        </Button>
        <span className="px-2 text-muted">
          {t('common.pageOf', { page: formatNumber(page, lang), pages: formatNumber(pages, lang) })}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
          aria-label={t('common.next')}
        >
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </div>
  )
}
