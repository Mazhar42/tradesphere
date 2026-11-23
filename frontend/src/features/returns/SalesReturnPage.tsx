import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { Taka } from '@/features/purchases/shared'
import { invalidateSales, salesKeys, useSalesPaymentAccounts } from '@/features/sales/hooks'
import { useLang } from '@/i18n'
import { formatDate, formatNumber } from '@/lib/format'
import { QTY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

type Condition = 'good' | 'damaged' | 'expired'
const CONDITIONS: Condition[] = ['good', 'damaged', 'expired']
const rowKey = (itemId: number, batchId: number | null) => `${itemId}:${batchId ?? 'none'}`

/** Take goods back against a posted invoice: pick quantities per batch, say what condition they are in. */
export function SalesReturnPage() {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const accounts = useSalesPaymentAccounts()
  const { invoiceId } = useParams({ strict: false }) as { invoiceId: string }
  const id = Number(invoiceId)

  const [qty, setQty] = useState<Record<string, string>>({})
  const [condition, setCondition] = useState<Record<string, Condition>>({})
  const [refund, setRefund] = useState<'credit' | 'cash'>('credit')
  const [accountId, setAccountId] = useState('')
  const [notes, setNotes] = useState('')
  const [savedId, setSavedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invoice = useQuery({
    queryKey: salesKeys.invoice(id),
    queryFn: () => unwrap(api.GET('/api/v1/sales-invoices/{invoice_id}', { params: { path: { invoice_id: id } } })),
  })
  const returnable = useQuery({
    queryKey: ['returnable', id],
    queryFn: () => unwrap(api.GET('/api/v1/sales-invoices/{invoice_id}/returnable', { params: { path: { invoice_id: id } } })),
  })

  const effectiveAccount = accountId || String((accounts.data ?? []).find((a) => a.kind === 'cash')?.id ?? '')

  const save = useMutation({
    mutationFn: async () => {
      const rows = returnable.data ?? []
      const items = rows
        .map((r) => ({ r, q: qty[rowKey(r.invoice_item_id, r.batch_id)] ?? '' }))
        .filter(({ q }) => QTY_PATTERN.test(q) && Number(q) > 0)
        .map(({ r, q }) => ({
          invoice_item_id: r.invoice_item_id,
          batch_id: r.batch_id,
          condition: condition[rowKey(r.invoice_item_id, r.batch_id)] ?? 'good',
          qty_base: q,
        }))
      if (items.length === 0) throw new Error(t('returns.enterQty'))
      if (refund === 'cash' && !effectiveAccount) throw new Error(t('sales.chooseAccount'))
      const body = {
        invoice_id: id,
        refund_method: refund,
        payment_account_id: refund === 'cash' ? Number(effectiveAccount) : null,
        notes: notes.trim() || null,
        items,
      }
      // A draft left by a failed post is reused on retry, never duplicated.
      const draftId = savedId ?? (await unwrap(api.POST('/api/v1/sales-returns', { body }))).id
      setSavedId(draftId)
      return unwrap(
        api.POST('/api/v1/sales-returns/{return_id}/post', {
          params: { path: { return_id: draftId }, header: { 'Idempotency-Key': idem.current() } },
        }),
      )
    },
    onSuccess: (ret) => {
      idem.settle()
      toast.success(t('returns.posted', { no: ret.doc_no }))
      invalidateSales(queryClient, id)
      void queryClient.invalidateQueries({ queryKey: ['sales-returns'] })
      void queryClient.invalidateQueries({ queryKey: ['returnable', id] })
      void navigate({ to: '/sales/$invoiceId', params: { invoiceId } })
    },
    onError: (e) => {
      idem.settle(e)
      setError(e.message)
    },
  })

  if (invoice.isError) return <ErrorState error={invoice.error} onRetry={() => void invoice.refetch()} />
  if (returnable.isError) return <ErrorState error={returnable.error} onRetry={() => void returnable.refetch()} />
  if (!invoice.data || !returnable.data) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />
  const inv = invoice.data
  const rows = returnable.data

  return (
    <>
      <PageHeader
        title={t('returns.salesReturnTitle', { no: inv.doc_no ?? '' })}
        description={`${inv.customer_name} · ${formatDate(inv.business_date, lang)}`}
        actions={
          <Button variant="outline" onClick={() => void navigate({ to: '/sales/$invoiceId', params: { invoiceId } })}>
            {t('common.cancel')}
          </Button>
        }
      />
      <Card className="mb-4">
        <CardHeader title={t('returns.whatToReturn')} description={t('returns.whatToReturnHint')} />
        <div className="space-y-3 p-4">
          {rows.map((r) => {
            const key = rowKey(r.invoice_item_id, r.batch_id)
            const left = Number(r.returnable_qty_base)
            return (
              <div key={key} className="grid items-end gap-3 rounded-lg border border-border p-3 md:grid-cols-12">
                <div className="md:col-span-4">
                  <div className="font-medium">{pick(r.name_en, r.name_bn)}</div>
                  <div className="text-xs text-muted">
                    {r.sku}
                    {r.batch_no && ` · ${r.batch_no}`}
                    {r.expiry_date && ` · ${formatDate(r.expiry_date, lang)}`}
                  </div>
                </div>
                <div className="text-sm md:col-span-3">
                  <div className="text-xs text-muted">{t('returns.sold')} / {t('returns.returned')} / {t('returns.left')}</div>
                  <div className="num">
                    {formatNumber(r.sold_qty_base, lang)} / {formatNumber(r.returned_qty_base, lang)} /{' '}
                    <span className="font-medium">{formatNumber(r.returnable_qty_base, lang)}</span> {r.base_uom_code}
                  </div>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('returns.returnQty')} htmlFor={`q-${key}`}>
                    <Input
                      id={`q-${key}`}
                      inputMode="decimal"
                      className="num"
                      disabled={left <= 0}
                      value={qty[key] ?? ''}
                      onChange={(e) => setQty((s) => ({ ...s, [key]: e.target.value }))}
                    />
                  </Field>
                </div>
                <div className="md:col-span-3">
                  <Field label={t('returns.condition')} htmlFor={`c-${key}`}>
                    <Select
                      id={`c-${key}`}
                      disabled={left <= 0}
                      value={condition[key] ?? 'good'}
                      onChange={(e) => setCondition((s) => ({ ...s, [key]: e.target.value as Condition }))}
                    >
                      {CONDITIONS.map((c) => (
                        <option key={c} value={c}>
                          {t(`returns.conditions.${c}`)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      <Card className="p-5">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label={t('returns.refund')} htmlFor="r-refund" hint={refund === 'credit' ? t('returns.creditHint') : t('returns.cashHint')}>
            <Select id="r-refund" value={refund} onChange={(e) => setRefund(e.target.value as 'credit' | 'cash')}>
              <option value="credit">{t('returns.refunds.credit')}</option>
              <option value="cash">{t('returns.refunds.cash')}</option>
            </Select>
          </Field>
          {refund === 'cash' && (
            <Field label={t('returns.paidFrom')} htmlFor="r-account">
              <Select id="r-account" value={effectiveAccount} onChange={(e) => setAccountId(e.target.value)}>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={t('sales.notes')} htmlFor="r-notes" optional optionalLabel={t('common.optional')}>
            <Input id="r-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
        <p className="mt-3 text-sm text-muted">
          {t('returns.invoiceTotal')}: <Taka value={inv.net_amount} />
        </p>
      </Card>

      {error && (
        <div role="alert" className="mt-4 rounded-lg bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
      <div className="mt-6 flex justify-end">
        <Button loading={save.isPending} onClick={() => save.mutate()}>
          {t('returns.postReturn')}
        </Button>
      </div>
    </>
  )
}
