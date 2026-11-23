import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const PAYMENT_TONE: Record<string, Tone> = {
  paid: 'success',
  partial: 'warning',
  unpaid: 'danger',
  draft: 'neutral',
  reversed: 'neutral',
}

const DOC_TONE: Record<string, Tone> = { draft: 'neutral', posted: 'success', reversed: 'danger' }

/** Whether an invoice is paid, part paid, unpaid, a draft or reversed. */
export function PaymentBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return <Badge tone={PAYMENT_TONE[status] ?? 'neutral'}>{t(`sales.paymentStatus.${status}`, status)}</Badge>
}

/** Draft, posted or reversed. */
export function DocBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return <Badge tone={DOC_TONE[status] ?? 'neutral'}>{t(`sales.docStatus.${status}`, status)}</Badge>
}
