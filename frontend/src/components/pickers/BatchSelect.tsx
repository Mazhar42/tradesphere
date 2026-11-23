import { useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/api/client'
import { Select } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDate } from '@/lib/format'

interface BatchSelectProps {
  productId: number | null
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  id?: string
  'aria-label'?: string
}

/** The batches of one product, newest expiry first. */
export function BatchSelect({ productId, value, onChange, disabled, id, 'aria-label': ariaLabel }: BatchSelectProps) {
  const { lang } = useLang()
  const batches = useQuery({
    queryKey: ['batches', productId],
    enabled: productId !== null,
    queryFn: () => unwrap(api.GET('/api/v1/inventory/batches', { params: { query: { product_id: productId! } } })),
    staleTime: 30_000,
  })
  return (
    <Select id={id} aria-label={ariaLabel} value={value} disabled={disabled || productId === null} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {batches.data?.map((b) => (
        <option key={b.id} value={b.id}>
          {b.batch_no}
          {b.expiry_date ? ` · ${formatDate(b.expiry_date, lang)}` : ''}
        </option>
      ))}
    </Select>
  )
}
