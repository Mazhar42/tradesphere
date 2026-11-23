import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'
import { MONEY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

import { purchaseKeys, useAllocatableOrders } from './hooks'
import { DocStatusBadge, Num, Taka } from './shared'

function useRefresh() {
  const queryClient = useQueryClient()
  return () => {
    for (const key of [
      purchaseKeys.orders,
      purchaseKeys.receipts,
      purchaseKeys.payments,
      ['purchase-order'],
      ['stock'],
      ['supplier-summary'],
      ['supplier-ledger'],
      ['payment-accounts'],
      ['goods-receipt'],
      ['supplier-payment'],
    ]) {
      void queryClient.invalidateQueries({ queryKey: key })
    }
  }
}

export function GrnViewDialog({ grnId, onClose }: { grnId: number | null; onClose: () => void }) {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const { can } = useAuth()
  const refresh = useRefresh()
  const idem = useIdempotencyKey()
  const [confirming, setConfirming] = useState(false)

  const query = useQuery({
    queryKey: ['goods-receipt', grnId],
    enabled: grnId !== null,
    queryFn: () => unwrap(api.GET('/api/v1/goods-receipts/{grn_id}', { params: { path: { grn_id: grnId! } } })),
  })
  const grn = query.data

  return (
    <>
      <Dialog
        open={grnId !== null}
        onOpenChange={(o) => !o && onClose()}
        title={grn ? `${grn.doc_no ?? t('purchases.docStatus.draft')} · ${grn.supplier_name}` : t('common.loading')}
        description={grn ? `${t('receive.against')} ${grn.po_doc_no ?? ''}` : undefined}
        size="lg"
        footer={
          <>
            {grn?.status === 'posted' && can('purchasing:reverse') && (
              <Button variant="danger" onClick={() => setConfirming(true)}>
                {t('purchases.reverse')}
              </Button>
            )}
            <Button variant="outline" onClick={onClose}>
              {t('common.close')}
            </Button>
          </>
        }
      >
        {grn && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div><dt className="text-muted">{t('receive.date')}</dt><dd>{formatDate(grn.business_date, lang)}</dd></div>
              <div><dt className="text-muted">{t('receive.challan')}</dt><dd>{grn.challan_no ?? '—'}</dd></div>
              <div><dt className="text-muted">{t('common.status')}</dt><dd><DocStatusBadge status={grn.status} /></dd></div>
              <div><dt className="text-muted">{t('purchases.value')}</dt><dd><Taka value={grn.total_value} className="font-medium" /></dd></div>
            </dl>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-surface-muted/60 text-xs text-muted uppercase">
                  <tr>
                    <th className="px-3 py-2 text-left">{t('purchases.product')}</th>
                    <th className="px-3 py-2 text-left">{t('receive.batchNo')}</th>
                    <th className="px-3 py-2 text-right">{t('receive.received')}</th>
                    <th className="px-3 py-2 text-right">{t('receive.free')}</th>
                    <th className="px-3 py-2 text-right">{t('receive.damaged')}</th>
                    <th className="px-3 py-2 text-right">{t('purchases.value')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {grn.items.map((i) => (
                    <tr key={i.id}>
                      <td className="px-3 py-2">{pick(i.name_en, i.name_bn)}</td>
                      <td className="px-3 py-2">{i.batch_no ? `${i.batch_no}${i.expiry_date ? ` · ${formatDate(i.expiry_date, lang)}` : ''}` : '—'}</td>
                      <td className="num px-3 py-2 text-right"><Num value={i.received_qty_base} /></td>
                      <td className="num px-3 py-2 text-right"><Num value={i.free_qty_base} /></td>
                      <td className="num px-3 py-2 text-right"><Num value={i.damaged_qty_base} /></td>
                      <td className="num px-3 py-2 text-right"><Taka value={i.value} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted">{t('receive.baseUnitsNote')}</p>
          </div>
        )}
      </Dialog>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('purchases.reverseReceiptTitle')}
        message={t('purchases.reverseReceiptMessage')}
        confirmLabel={t('purchases.reverse')}
        danger
        onConfirm={async () => {
          try {
            await unwrap(
              api.POST('/api/v1/goods-receipts/{grn_id}/reverse', {
                params: { path: { grn_id: grnId! }, header: { 'Idempotency-Key': idem.current() } },
              }),
            )
            idem.settle()
          } catch (e) {
            idem.settle(e)
            throw e
          }
          toast.success(t('purchases.reversed'))
          refresh()
          onClose()
        }}
      />
    </>
  )
}

export function PaymentViewDialog({ paymentId, onClose }: { paymentId: number | null; onClose: () => void }) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const refresh = useRefresh()
  const idem = useIdempotencyKey()
  const [confirming, setConfirming] = useState(false)
  const [poId, setPoId] = useState('')
  const [amount, setAmount] = useState('')

  const query = useQuery({
    queryKey: ['supplier-payment', paymentId],
    enabled: paymentId !== null,
    queryFn: () => unwrap(api.GET('/api/v1/supplier-payments/{payment_id}', { params: { path: { payment_id: paymentId! } } })),
  })
  const payment = query.data
  const orders = useAllocatableOrders(payment?.status === 'posted' ? payment.supplier_id : null)

  const allocate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/supplier-payments/{payment_id}/allocate', {
          params: { path: { payment_id: paymentId! } },
          body: { allocations: [{ po_id: Number(poId), amount }] },
        }),
      ),
    onSuccess: () => {
      toast.success(t('common.saved'))
      setPoId('')
      setAmount('')
      refresh()
    },
    onError: (e) => toast.error(e.message),
  })

  const canAllocate = payment?.status === 'posted' && cmpMoney(payment.unallocated_amount, '0') > 0

  return (
    <>
      <Dialog
        open={paymentId !== null}
        onOpenChange={(o) => !o && onClose()}
        title={payment ? `${payment.doc_no} · ${payment.supplier_name}` : t('common.loading')}
        size="lg"
        footer={
          <>
            {payment?.status === 'posted' && can('purchasing:reverse') && (
              <Button variant="danger" onClick={() => setConfirming(true)}>
                {t('purchases.reverse')}
              </Button>
            )}
            <Button variant="outline" onClick={onClose}>
              {t('common.close')}
            </Button>
          </>
        }
      >
        {payment && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div><dt className="text-muted">{t('payment.date')}</dt><dd>{formatDate(payment.business_date, lang)}</dd></div>
              <div><dt className="text-muted">{t('payment.paidFrom')}</dt><dd>{payment.payment_account_name}</dd></div>
              <div><dt className="text-muted">{t('common.status')}</dt><dd><DocStatusBadge status={payment.status} /></dd></div>
              <div><dt className="text-muted">{t('payment.amount')}</dt><dd><Taka value={payment.amount} className="font-medium" /></dd></div>
            </dl>
            {payment.reference && <p className="text-sm text-muted">{t('payment.reference')}: {payment.reference}</p>}
            <div className="rounded-lg border border-border">
              <div className="flex justify-between border-b border-border px-4 py-2 text-sm">
                <span className="font-medium">{t('payment.allocations')}</span>
                <span className="text-muted">{t('payment.unallocated')}: <Taka value={payment.unallocated_amount} /></span>
              </div>
              {payment.allocations.length === 0 && <p className="px-4 py-3 text-sm text-muted">{t('payment.noAllocations')}</p>}
              <ul className="divide-y divide-border">
                {payment.allocations.map((a) => (
                  <li key={a.id} className="flex justify-between px-4 py-2 text-sm">
                    <span>{a.po_doc_no}</span>
                    <Taka value={a.amount} className="num" />
                  </li>
                ))}
              </ul>
            </div>
            {canAllocate && (
              <div className="rounded-lg border border-border p-4">
                <p className="mb-3 text-sm font-medium">{t('payment.allocateMore')}</p>
                <div className="flex flex-wrap items-end gap-2">
                  <select
                    aria-label={t('purchases.orders')}
                    className="h-10 rounded-lg border border-border bg-surface px-3 text-sm"
                    value={poId}
                    onChange={(e) => {
                      setPoId(e.target.value)
                      const order = orders.data?.find((o) => String(o.id) === e.target.value)
                      if (order) setAmount(cmpMoney(order.room, payment.unallocated_amount) < 0 ? order.room : payment.unallocated_amount)
                    }}
                  >
                    <option value="">—</option>
                    {orders.data?.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.doc_no}
                      </option>
                    ))}
                  </select>
                  <Input inputMode="decimal" className="num w-36" value={amount} onChange={(e) => setAmount(e.target.value)} aria-label={t('payment.amount')} />
                  <Button
                    loading={allocate.isPending}
                    disabled={!poId || !MONEY_PATTERN.test(amount)}
                    onClick={() => allocate.mutate()}
                  >
                    {t('payment.allocate')}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </Dialog>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('purchases.reversePaymentTitle')}
        message={t('purchases.reversePaymentMessage')}
        confirmLabel={t('purchases.reverse')}
        danger
        onConfirm={async () => {
          try {
            await unwrap(
              api.POST('/api/v1/supplier-payments/{payment_id}/reverse', {
                params: { path: { payment_id: paymentId! }, header: { 'Idempotency-Key': idem.current() } },
              }),
            )
            idem.settle()
          } catch (e) {
            idem.settle(e)
            throw e
          }
          toast.success(t('purchases.reversed'))
          refresh()
          onClose()
        }}
      />
    </>
  )
}
