/** The dealership's subscription, invoices and "I have paid". */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDate, formatMoney } from '@/lib/format'

type Invoice = Schemas['InvoiceRow']
const KEY = ['billing'] as const
const METHODS = ['bank', 'bkash', 'nagad', 'other'] as const

function PayForm({ invoice, onDone }: { invoice: Invoice; onDone: () => void }) {
  const { t } = useTranslation()
  const [method, setMethod] = useState<(typeof METHODS)[number]>('bank')
  const [reference, setReference] = useState('')
  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/billing/invoices/{billing_invoice_id}/payment-claims', {
          params: { path: { billing_invoice_id: invoice.id } },
          body: { method, reference, amount: String(Number(invoice.amount) - Number(invoice.paid)) },
        }),
      ),
    onSuccess: () => {
      toast.success(t('billing.claimSent'))
      onDone()
    },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        submit.mutate()
      }}
      className="flex flex-wrap items-end gap-2"
    >
      <Field label={t('billing.method')} htmlFor={`method-${invoice.id}`}>
        <select
          id={`method-${invoice.id}`}
          className="h-10 rounded-lg border border-border bg-surface px-2 text-sm"
          value={method}
          onChange={(e) => setMethod(e.target.value as (typeof METHODS)[number])}
        >
          {METHODS.map((m) => (
            <option key={m} value={m}>
              {t(`billing.methods.${m}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t('billing.reference')} htmlFor={`ref-${invoice.id}`}>
        <Input id={`ref-${invoice.id}`} required minLength={3} value={reference} onChange={(e) => setReference(e.target.value)} />
      </Field>
      <Button type="submit" loading={submit.isPending}>
        {t('billing.iPaid')}
      </Button>
    </form>
  )
}

export function BillingPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const billing = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/v1/billing')) })
  const cancel = useMutation({
    mutationFn: (stop: boolean) => unwrap(api.POST(stop ? '/api/v1/billing/cancel' : '/api/v1/billing/resume')),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
    onError: (e) => toast.error(e.message),
  })
  if (billing.isError) return <ErrorState error={billing.error} onRetry={() => void billing.refetch()} />
  const data = billing.data

  return (
    <>
      <PageHeader title={t('billing.title')} />
      {data && !data.status && <Card className="p-4 text-sm">{t('billing.none')}</Card>}
      {data?.status && (
        <Card className="space-y-2 p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{data.plan_name}</span>
            <Badge>{t(`billing.status.${data.status}`)}</Badge>
            {data.price && <span>{formatMoney(data.price, lang)}</span>}
          </div>
          {data.trial_ends_on && data.status === 'trialing' && (
            <p>{t('billing.trialEnds', { date: formatDate(data.trial_ends_on, lang) })}</p>
          )}
          {data.period_end && <p>{t('billing.paidUntil', { date: formatDate(data.period_end, lang) })}</p>}
          <p className="text-muted">
            {t('billing.users', { used: data.active_users, max: data.max_users ?? '∞' })}
          </p>
          {['trialing', 'active', 'past_due'].includes(data.status) && (
            <Button variant="outline" size="sm" onClick={() => cancel.mutate(!data.cancel_at_period_end)}>
              {data.cancel_at_period_end ? t('billing.keep') : t('billing.cancel')}
            </Button>
          )}
        </Card>
      )}
      {data && data.invoices.length > 0 && (
        <Card className="mt-4 divide-y divide-border">
          <p className="p-4 text-sm text-muted">{data.payment_instructions}</p>
          {data.invoices.map((invoice) => (
            <div key={invoice.id} className="space-y-2 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium">{invoice.number}</span>
                <span>
                  {formatDate(invoice.period_start, lang)} – {formatDate(invoice.period_end, lang)}
                </span>
                <span>{formatMoney(invoice.amount, lang)}</span>
                <Badge>{t(`billing.invoiceStatus.${invoice.status}`)}</Badge>
              </div>
              {invoice.status === 'open' && (
                <PayForm invoice={invoice} onDone={() => void queryClient.invalidateQueries({ queryKey: KEY })} />
              )}
            </div>
          ))}
        </Card>
      )}
    </>
  )
}
