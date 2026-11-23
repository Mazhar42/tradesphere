import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Banknote, Pencil, Printer, RotateCcw, Trash2, Undo2 } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useAuth } from '@/features/auth/auth-context'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate, formatNumber } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'
import { cn } from '@/lib/utils'

import { ReceiptDialog } from './ReceiptDialog'
import { invalidateSales, openInvoicePrint, salesKeys } from './hooks'
import { DocBadge, PaymentBadge } from './shared'

type Item = Schemas['InvoiceItemRead']
type Receipt = Schemas['ReceiptListItem']

function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: 'danger' | 'success'; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p className={cn('num mt-1.5 text-xl font-semibold', tone === 'danger' && 'text-danger', tone === 'success' && 'text-success')}>
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  )
}

export function InvoiceDetailPage() {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { invoiceId } = useParams({ strict: false }) as { invoiceId: string }
  const id = Number(invoiceId)
  const postKey = useIdempotencyKey()
  const reverseKey = useIdempotencyKey()

  const [confirm, setConfirm] = useState<'post' | 'reverse' | 'delete' | null>(null)
  const [receiptOpen, setReceiptOpen] = useState(false)
  const [printing, setPrinting] = useState(false)

  const query = useQuery({
    queryKey: salesKeys.invoice(id),
    queryFn: () => unwrap(api.GET('/api/v1/sales-invoices/{invoice_id}', { params: { path: { invoice_id: id } } })),
  })

  const post = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/sales-invoices/{invoice_id}/post', {
          params: { path: { invoice_id: id }, header: { 'Idempotency-Key': postKey.current() } },
        }),
      ),
    onSuccess: (inv) => {
      postKey.settle()
      toast.success(t('sales.posted', { no: inv.doc_no }))
      invalidateSales(queryClient, id)
    },
    // The confirm dialog shows the server's reason, so no toast here.
    onError: (e) => postKey.settle(e),
  })

  const reverse = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/sales-invoices/{invoice_id}/reverse', {
          params: { path: { invoice_id: id }, header: { 'Idempotency-Key': reverseKey.current() } },
        }),
      ),
    onSuccess: (inv) => {
      reverseKey.settle()
      toast.success(t('sales.reversed', { no: inv.doc_no }))
      invalidateSales(queryClient, id)
    },
    onError: (e) => reverseKey.settle(e),
  })

  const remove = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE('/api/v1/sales-invoices/{invoice_id}', { params: { path: { invoice_id: id } } })),
    onSuccess: () => {
      toast.success(t('sales.draftDeleted'))
      invalidateSales(queryClient)
      void navigate({ to: '/sales' })
    },
  })

  const itemColumns: Column<Item>[] = [
    {
      header: t('sales.product'),
      cell: (item) => (
        <div>
          <div className="font-medium">{pick(item.name_en, item.name_bn)}</div>
          <div className="text-xs text-muted">{item.sku}</div>
        </div>
      ),
    },
    {
      header: t('sales.qty'),
      align: 'right',
      cell: (item) => (
        <div className="num">
          <div>
            {formatNumber(item.qty_entry, lang)} {item.uom_code}
            {item.free_qty_entry && Number(item.free_qty_entry) > 0 && (
              <span className="ml-1 text-xs text-success">+{formatNumber(item.free_qty_entry, lang)} {t('sales.freeShort')}</span>
            )}
          </div>
          {item.uom_code !== item.base_uom_code && (
            <div className="text-xs text-muted">= {formatNumber(item.qty_base, lang)} {item.base_uom_code}</div>
          )}
        </div>
      ),
    },
    { header: t('sales.unitPrice'), align: 'right', cell: (item) => <Taka value={item.unit_price} className="text-sm" /> },
    {
      header: t('sales.discount'),
      align: 'right',
      cell: (item) => (
        <Taka value={String(Number(item.dealer_discount) + Number(item.company_discount))} />
      ),
    },
    { header: t('sales.net'), align: 'right', cell: (item) => <Taka value={item.line_net} /> },
    {
      header: t('sales.batches'),
      cell: (item) => (
        <div className="space-y-0.5 text-xs">
          {item.batches.map((b, i) => (
            <div key={i} className={b.is_free ? 'text-success' : undefined}>
              {b.batch_no ? `${b.batch_no}` : t('sales.noBatch')}
              {b.expiry_date && ` · ${formatDate(b.expiry_date, lang)}`}
              {' · '}
              {formatNumber(b.qty_base, lang)} {item.base_uom_code}
              {b.is_free && ` (${t('sales.freeShort')})`}
            </div>
          ))}
        </div>
      ),
    },
  ]

  const receiptColumns: Column<Receipt>[] = [
    { header: t('sales.receipt'), cell: (r) => r.doc_no },
    { header: t('sales.date'), cell: (r) => formatDate(r.business_date, lang) },
    { header: t('sales.receivedInto'), cell: (r) => r.payment_account_name },
    { header: t('common.status'), cell: (r) => <DocBadge status={r.status} /> },
    { header: t('sales.amount'), align: 'right', cell: (r) => <Taka value={r.amount} /> },
  ]

  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const inv = query.data
  if (!inv) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />

  const canWrite = can('sales:write')
  const isDraft = inv.status === 'draft'
  const isPosted = inv.status === 'posted'
  const due = inv.due_amount
  const discount = String(Number(inv.dealer_discount) + Number(inv.company_discount))

  const print = async (paper: 'a4' | 'thermal80') => {
    setPrinting(true)
    try {
      await openInvoicePrint(id, paper)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('sales.printFailed'))
    } finally {
      setPrinting(false)
    }
  }

  return (
    <>
      <PageHeader
        title={inv.doc_no ?? t('sales.draft')}
        description={`${inv.customer_name}${inv.customer_shop_name ? ` · ${inv.customer_shop_name}` : ''} · ${formatDate(inv.business_date, lang)}`}
        actions={
          <>
            <DocBadge status={inv.status} />
            <PaymentBadge status={inv.payment_status} />
            {isDraft && canWrite && (
              <>
                <Button variant="outline" onClick={() => void navigate({ to: '/sales/$invoiceId/edit', params: { invoiceId } })}>
                  <Pencil className="size-4" /> {t('common.edit')}
                </Button>
                <Button variant="outline" onClick={() => setConfirm('delete')}>
                  <Trash2 className="size-4" /> {t('common.delete')}
                </Button>
                <Button onClick={() => setConfirm('post')}>{t('sales.postInvoice')}</Button>
              </>
            )}
            {isPosted && canWrite && (
              <Button variant="outline" onClick={() => setReceiptOpen(true)}>
                <Banknote className="size-4" /> {t('sales.receivePayment')}
              </Button>
            )}
            {!isDraft && (
              <>
                <Button variant="outline" loading={printing} onClick={() => void print('a4')}>
                  <Printer className="size-4" /> {t('sales.printA4')}
                </Button>
                <Button variant="outline" loading={printing} onClick={() => void print('thermal80')}>
                  {t('sales.printThermal')}
                </Button>
              </>
            )}
            {isPosted && can('sales:return') && (
              <Button variant="outline" onClick={() => void navigate({ to: '/sales/$invoiceId/return', params: { invoiceId } })}>
                <Undo2 className="size-4" /> {t('returns.returnGoods')}
              </Button>
            )}
            {isPosted && can('sales:reverse') && (
              <Button variant="outline" onClick={() => setConfirm('reverse')}>
                <RotateCcw className="size-4" /> {t('sales.reverseInvoice')}
              </Button>
            )}
          </>
        }
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={t('sales.net')} value={<Taka value={inv.net_amount} />} />
        <Stat label={t('sales.received')} value={<Taka value={inv.received_amount} />} tone="success" />
        <Stat
          label={t('sales.due')}
          value={<Taka value={due} />}
          tone={cmpMoney(due, '0') > 0 ? 'danger' : undefined}
          hint={inv.status === 'reversed' ? t('sales.reversedHint') : undefined}
        />
        <Stat label={t('sales.discount')} value={<Taka value={discount} />} hint={t('sales.discountHint')} />
      </div>

      <Card className="mb-6">
        <CardHeader title={t('sales.items')} />
        <DataTable columns={itemColumns} data={inv.items} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title={t('sales.receipts')} description={t('sales.receiptsHint')} />
          <DataTable columns={receiptColumns} data={inv.receipts} />
          {inv.receipts.length === 0 && (
            <p className="px-4 pb-4 text-sm text-muted">{t('sales.noReceipts')}</p>
          )}
        </Card>
        <Card className="p-5 text-sm">
          <dl className="space-y-2">
            <Line label={t('sales.gross')} value={<Taka value={inv.gross_amount} />} />
            <Line label={t('sales.dealerDiscount')} value={<Taka value={inv.dealer_discount} />} />
            <Line label={t('sales.companyDiscount')} value={<Taka value={inv.company_discount} />} hint={t('sales.companyDiscountHint')} />
            <Line label={t('sales.net')} value={<Taka value={inv.net_amount} />} strong />
            {Number(inv.returned_amount) > 0 && (
              <Line label={t('returns.returnedValue')} value={<Taka value={inv.returned_amount} />} />
            )}
            {inv.payment_account_name && (
              <Line label={t('sales.paidInto')} value={<span>{inv.payment_account_name}</span>} />
            )}
            {inv.notes && <Line label={t('sales.notes')} value={<span className="text-right">{inv.notes}</span>} />}
          </dl>
        </Card>
      </div>

      <ConfirmDialog
        open={confirm === 'post'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('sales.postInvoice')}
        message={t('sales.postMessage')}
        confirmLabel={t('sales.postInvoice')}
        onConfirm={() => post.mutateAsync()}
      />
      <ConfirmDialog
        open={confirm === 'reverse'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('sales.reverseInvoice')}
        message={t('sales.reverseMessage')}
        confirmLabel={t('sales.reverseInvoice')}
        danger
        onConfirm={() => reverse.mutateAsync()}
      />
      <ConfirmDialog
        open={confirm === 'delete'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('common.delete')}
        message={t('sales.deleteDraftMessage')}
        confirmLabel={t('common.delete')}
        danger
        onConfirm={() => remove.mutateAsync()}
      />
      <ReceiptDialog
        open={receiptOpen}
        onOpenChange={setReceiptOpen}
        customer={{ id: inv.customer_id, name: inv.customer_name }}
      />
    </>
  )
}

function Line({ label, value, hint, strong }: { label: string; value: ReactNode; hint?: string; strong?: boolean }) {
  return (
    <div className={strong ? 'flex items-center justify-between border-t border-border pt-2' : 'flex items-center justify-between gap-4'}>
      <div>
        <dt className="text-muted">{label}</dt>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      <dd className="num text-right">{value}</dd>
    </div>
  )
}
