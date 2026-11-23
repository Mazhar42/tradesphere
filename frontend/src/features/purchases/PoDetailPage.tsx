import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { Banknote, PackageCheck, Pencil, Trash2 } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

import { PaymentDialog } from './PaymentDialog'
import { ReceiveDialog } from './ReceiveDialog'
import { purchaseKeys, useQtyFormatter } from './hooks'
import { DocStatusBadge, PoStatusBadge, Taka } from './shared'
import { GrnViewDialog, PaymentViewDialog } from './ViewDialogs'

function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: 'warning' | 'success' | 'danger'; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p className={cn('num mt-1.5 text-xl font-semibold', tone === 'warning' && 'text-warning', tone === 'success' && 'text-success', tone === 'danger' && 'text-danger')}>
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  )
}

export function PoDetailPage() {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { poId } = useParams({ strict: false }) as { poId: string }
  const id = Number(poId)
  const fmtQty = useQtyFormatter()

  const [receiveOpen, setReceiveOpen] = useState(false)
  const [payOpen, setPayOpen] = useState(false)
  const [grnId, setGrnId] = useState<number | null>(null)
  const [paymentId, setPaymentId] = useState<number | null>(null)
  const [confirm, setConfirm] = useState<'close' | 'cancel' | 'delete' | 'confirm' | null>(null)

  const query = useQuery({
    queryKey: ['purchase-order', id],
    queryFn: () => unwrap(api.GET('/api/v1/purchase-orders/{po_id}', { params: { path: { po_id: id } } })),
  })
  const po = query.data
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (!po) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />

  const s = po.summary
  const open = po.status === 'confirmed' || po.status === 'partially_received'
  const received = Number(s.received_value) > 0
  const canWrite = can('purchasing:write')
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['purchase-order', id] })
    void queryClient.invalidateQueries({ queryKey: purchaseKeys.orders })
  }
  const act = (path: '/api/v1/purchase-orders/{po_id}/confirm' | '/api/v1/purchase-orders/{po_id}/close-short' | '/api/v1/purchase-orders/{po_id}/cancel', message: string) => async () => {
    await unwrap(api.POST(path, { params: { path: { po_id: id } } }))
    toast.success(message)
    await refresh()
  }

  return (
    <>
      <PageHeader
        title={po.doc_no ?? t('purchases.draftOrder')}
        description={`${po.supplier_name} · ${formatDate(po.business_date, lang)}`}
        actions={
          <>
            <PoStatusBadge status={po.status} />
            {po.status === 'draft' && canWrite && (
              <>
                <Button variant="outline" onClick={() => void navigate({ to: '/purchases/$poId/edit', params: { poId } })}>
                  <Pencil className="size-4" /> {t('common.edit')}
                </Button>
                <Button variant="outline" onClick={() => setConfirm('delete')}>
                  <Trash2 className="size-4" /> {t('common.delete')}
                </Button>
                <Button onClick={() => setConfirm('confirm')}>{t('purchases.confirmOrder')}</Button>
              </>
            )}
            {open && canWrite && (
              <Button onClick={() => setReceiveOpen(true)}>
                <PackageCheck className="size-4" /> {t('purchases.receive')}
              </Button>
            )}
            {po.status !== 'draft' && po.status !== 'cancelled' && can('purchasing:pay') && (
              <Button variant="outline" onClick={() => setPayOpen(true)}>
                <Banknote className="size-4" /> {t('purchases.recordPayment')}
              </Button>
            )}
            {po.status === 'partially_received' && canWrite && (
              <Button variant="outline" onClick={() => setConfirm('close')}>
                {t('purchases.closeShort')}
              </Button>
            )}
            {po.status === 'confirmed' && !received && canWrite && (
              <Button variant="outline" onClick={() => setConfirm('cancel')}>
                {t('purchases.cancelOrder')}
              </Button>
            )}
          </>
        }
      />

      {po.status !== 'draft' && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <Stat label={t('purchases.ordered')} value={<Taka value={s.ordered_value} />} />
          <Stat label={t('purchases.received')} value={<Taka value={s.received_value} />} tone="success" />
          <Stat
            label={t('purchases.undelivered')}
            value={<Taka value={s.remaining_value} />}
            tone={cmpMoney(s.remaining_value, '0') > 0 ? 'warning' : undefined}
            hint={po.status === 'closed_short' ? t('purchases.cancelledValue', { value: s.cancelled_value }) : t('purchases.undeliveredHint')}
          />
          <Stat label={t('purchases.paid')} value={<Taka value={s.paid_value} />} />
          {cmpMoney(s.balance_due, '0') > 0 ? (
            <Stat label={t('purchases.balanceDue')} value={<Taka value={s.balance_due} />} tone="danger" hint={t('purchases.balanceDueHint')} />
          ) : (
            <Stat label={t('purchases.advance')} value={<Taka value={s.unapplied_advance} />} tone={cmpMoney(s.unapplied_advance, '0') > 0 ? 'warning' : undefined} hint={t('purchases.advanceHint')} />
          )}
        </div>
      )}

      <Card className="mb-6">
        <CardHeader title={t('purchases.items')} description={po.notes ?? undefined} />
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-muted/60 text-xs text-muted uppercase">
              <tr>
                <th className="px-4 py-2.5 text-left">{t('purchases.product')}</th>
                <th className="px-4 py-2.5 text-right">{t('purchases.ordered')}</th>
                <th className="px-4 py-2.5 text-right">{t('purchases.received')}</th>
                <th className="px-4 py-2.5 text-right">{t('purchases.remaining')}</th>
                <th className="px-4 py-2.5 text-right">{t('purchases.unitPrice')}</th>
                <th className="px-4 py-2.5 text-right">{t('purchases.value')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {po.items.map((item) => {
                const pct = Math.min(100, (Number(item.received_qty_base) / Number(item.ordered_qty_base)) * 100)
                return (
                  <tr key={item.id}>
                    <td className="px-4 py-3">
                      <div className="font-medium">{pick(item.name_en, item.name_bn)}</div>
                      <div className="text-xs text-muted">
                        {item.sku}
                        {Number(item.free_qty_base) > 0 &&
                          ` · ${t('purchases.freeExpected', { qty: fmtQty(item.free_qty_base, item.uom_code, item.factor, item.base_uom_code) })}`}
                      </div>
                    </td>
                    <td className="num px-4 py-3 text-right">{fmtQty(item.ordered_qty_base, item.uom_code, item.factor, item.base_uom_code)}</td>
                    <td className="num px-4 py-3 text-right">
                      {fmtQty(item.received_qty_base, item.uom_code, item.factor, item.base_uom_code)}
                      <div className="mt-1 ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-surface-muted">
                        <div className="h-full rounded-full bg-success" style={{ width: `${pct}%` }} />
                      </div>
                    </td>
                    <td className="num px-4 py-3 text-right">
                      {Number(item.cancelled_qty_base) > 0 ? (
                        <span className="text-muted line-through">{fmtQty(item.cancelled_qty_base, item.uom_code, item.factor, item.base_uom_code)}</span>
                      ) : (
                        fmtQty(item.remaining_qty_base, item.uom_code, item.factor, item.base_uom_code)
                      )}
                    </td>
                    <td className="num px-4 py-3 text-right">
                      <Taka value={item.unit_price} />
                      <div className="text-xs text-muted">/ {item.uom_code}</div>
                    </td>
                    <td className="num px-4 py-3 text-right"><Taka value={item.line_value} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title={t('purchases.receipts')} />
          {po.receipts.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted">{t('purchases.noReceipts')}</p>
          ) : (
            <ul className="divide-y divide-border">
              {po.receipts.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => setGrnId(r.id)} className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left hover:bg-surface-muted/60">
                    <span>
                      <span className="font-medium">{r.doc_no ?? t('purchases.docStatus.draft')}</span>
                      <span className="ml-2 text-xs text-muted">{formatDate(r.business_date, lang)}{r.challan_no ? ` · ${r.challan_no}` : ''}</span>
                    </span>
                    <span className="flex items-center gap-3">
                      <DocStatusBadge status={r.status} />
                      <Taka value={r.total_value} className="num" />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title={t('purchases.payments')} description={t('purchases.paymentsHint')} />
          {po.payments.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted">{t('purchases.noPayments')}</p>
          ) : (
            <ul className="divide-y divide-border">
              {po.payments.map((p) => (
                <li key={p.payment_id}>
                  <button type="button" onClick={() => setPaymentId(p.payment_id)} className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left hover:bg-surface-muted/60">
                    <span>
                      <span className="font-medium">{p.doc_no}</span>
                      <span className="ml-2 text-xs text-muted">{formatDate(p.business_date, lang)}</span>
                    </span>
                    <span className="flex items-center gap-3">
                      <DocStatusBadge status={p.status} />
                      <Taka value={p.amount} className="num" />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="border-t border-border px-5 py-3 text-sm">
            <Link to="/suppliers/$supplierId" params={{ supplierId: String(po.supplier_id) }} className="text-primary hover:underline">
              {t('purchases.viewSupplierAccount')}
            </Link>
          </div>
        </Card>
      </div>

      <ReceiveDialog po={po} open={receiveOpen} onOpenChange={setReceiveOpen} />
      <PaymentDialog open={payOpen} onOpenChange={setPayOpen} supplierId={po.supplier_id} poId={po.id} />
      <GrnViewDialog grnId={grnId} onClose={() => setGrnId(null)} />
      <PaymentViewDialog paymentId={paymentId} onClose={() => setPaymentId(null)} />

      <ConfirmDialog
        open={confirm === 'confirm'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('purchases.confirmOrder')}
        message={t('purchases.confirmOrderMessage')}
        confirmLabel={t('purchases.confirmOrder')}
        onConfirm={act('/api/v1/purchase-orders/{po_id}/confirm', t('common.saved'))}
      />
      <ConfirmDialog
        open={confirm === 'close'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('purchases.closeShort')}
        message={t('purchases.closeShortMessage', { value: s.remaining_value })}
        confirmLabel={t('purchases.closeShort')}
        danger
        onConfirm={act('/api/v1/purchase-orders/{po_id}/close-short', t('common.saved'))}
      />
      <ConfirmDialog
        open={confirm === 'cancel'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('purchases.cancelOrder')}
        message={t('purchases.cancelOrderMessage')}
        confirmLabel={t('purchases.cancelOrder')}
        danger
        onConfirm={act('/api/v1/purchase-orders/{po_id}/cancel', t('common.saved'))}
      />
      <ConfirmDialog
        open={confirm === 'delete'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('common.delete')}
        message={t('purchases.deleteDraftMessage')}
        confirmLabel={t('common.delete')}
        danger
        onConfirm={async () => {
          await unwrap(api.DELETE('/api/v1/purchase-orders/{po_id}', { params: { path: { po_id: id } } }))
          void queryClient.invalidateQueries({ queryKey: purchaseKeys.orders })
          void navigate({ to: '/purchases' })
        }}
      />
    </>
  )
}
