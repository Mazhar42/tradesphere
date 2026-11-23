import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'
import { useLang } from '@/i18n'
import { formatMoney, formatNumber } from '@/lib/format'

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const PO_TONE: Record<string, Tone> = {
  draft: 'neutral',
  confirmed: 'primary',
  partially_received: 'warning',
  received: 'success',
  closed_short: 'warning',
  cancelled: 'danger',
}

const DOC_TONE: Record<string, Tone> = { draft: 'neutral', posted: 'success', reversed: 'danger' }

export function PoStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return <Badge tone={PO_TONE[status] ?? 'neutral'}>{t(`purchases.status.${status}`, status)}</Badge>
}

export function DocStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return <Badge tone={DOC_TONE[status] ?? 'neutral'}>{t(`purchases.docStatus.${status}`, status)}</Badge>
}

/** Money cell: Taka with lakh grouping, in the current language. */
export function Taka({ value, className }: { value: string | null | undefined; className?: string }) {
  const { lang } = useLang()
  return <span className={className}>{formatMoney(value, lang)}</span>
}

export function Num({ value }: { value: string | number }) {
  const { lang } = useLang()
  return <>{formatNumber(value, lang)}</>
}
