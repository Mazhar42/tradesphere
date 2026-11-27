/** Operators: open invoices with what dealerships say they paid, and recording money. */
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Schemas } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

type Invoice = Schemas['PlatformInvoiceRead']
type Plan = Schemas['PlanRead']
export type PlatformCall = <T>(path: string, init?: RequestInit) => Promise<T>

function Row({ invoice, call, reload }: { invoice: Invoice; call: PlatformCall; reload: () => Promise<void> }) {
  const { t } = useTranslation()
  const [reference, setReference] = useState(invoice.claims.at(-1)?.reference ?? '')
  const owed = (Number(invoice.amount) - Number(invoice.paid)).toFixed(2)
  const record = (event: FormEvent) => {
    event.preventDefault()
    void call(`/invoices/${invoice.id}/payments`, {
      method: 'POST',
      body: JSON.stringify({
        amount: owed,
        method: invoice.claims.at(-1)?.method ?? 'bank',
        reference,
        received_on: new Date().toISOString().slice(0, 10),
      }),
    }).then(reload)
  }
  return (
    <li className="space-y-1 py-2">
      <div>
        {invoice.number} · #{invoice.tenant_id} · {invoice.amount} <Badge>{t(`billing.invoiceStatus.${invoice.status}`)}</Badge>
      </div>
      {invoice.claims.map((claim) => (
        <div key={claim.reference} className="text-muted">
          {t('platform.claim', { method: claim.method, reference: claim.reference, amount: claim.amount })}
        </div>
      ))}
      {invoice.status === 'open' && (
        <form onSubmit={record} className="flex flex-wrap gap-2">
          <Input
            required
            minLength={3}
            aria-label={t('billing.reference')}
            placeholder={t('billing.reference')}
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
          <Button size="sm" type="submit">
            {t('platform.recordPayment', { amount: owed })}
          </Button>
        </form>
      )}
    </li>
  )
}

export function PlatformBilling({ call, tenantIds }: { call: PlatformCall; tenantIds: number[] }) {
  const { t } = useTranslation()
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setInvoices(await call<Invoice[]>('/invoices'))
    setPlans(await call<Plan[]>('/plans'))
  }, [call])
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- state is set after the request
    void reload().catch((e: Error) => setError(e.message))
  }, [reload])

  const act = (path: string, body?: unknown) =>
    void call(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
      .then(reload)
      .catch((e: Error) => setError(e.message))

  return (
    <Card className="space-y-3 p-4 text-sm">
      <h2 className="font-semibold">{t('billing.title')}</h2>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      {plans.length === 0 && <p className="text-muted">{t('platform.noPlans')}</p>}
      {plans.map((plan) => (
        <div key={plan.id} className="flex flex-wrap items-center gap-2">
          {plan.name} v{plan.version} · {plan.price} {plan.currency}
          {tenantIds.map((id) => (
            <span key={id} className="inline-flex gap-1">
              <Button size="sm" variant="outline" onClick={() => act(`/tenants/${id}/subscription`, { plan_id: plan.id })}>
                {t('platform.assign', { id })}
              </Button>
              <Button size="sm" variant="outline" onClick={() => act(`/tenants/${id}/invoices`)}>
                {t('platform.invoice', { id })}
              </Button>
            </span>
          ))}
        </div>
      ))}
      <ul className="divide-y divide-border">
        {invoices.map((invoice) => (
          <Row key={invoice.id} invoice={invoice} call={call} reload={reload} />
        ))}
      </ul>
    </Card>
  )
}
