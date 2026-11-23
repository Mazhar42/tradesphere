import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Banknote, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { PaymentDialog } from '@/features/purchases/PaymentDialog'
import { DocStatusBadge, PoStatusBadge, Taka } from '@/features/purchases/shared'
import { PaymentViewDialog } from '@/features/purchases/ViewDialogs'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate, formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

type Tab = 'ledger' | 'orders' | 'payments'
const PAGE_SIZE = 20

export function SupplierAccountPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const { supplierId } = useParams({ strict: false }) as { supplierId: string }
  const id = Number(supplierId)
  const [tab, setTab] = useState<Tab>('ledger')
  const [page, setPage] = useState(1)
  const [payOpen, setPayOpen] = useState(false)
  const [paymentId, setPaymentId] = useState<number | null>(null)

  const supplier = useQuery({
    queryKey: ['supplier', id],
    queryFn: () => unwrap(api.GET('/api/v1/suppliers/{supplier_id}', { params: { path: { supplier_id: id } } })),
  })
  const summary = useQuery({
    queryKey: ['supplier-summary', id],
    queryFn: () => unwrap(api.GET('/api/v1/suppliers/{supplier_id}/summary', { params: { path: { supplier_id: id } } })),
  })
  const ledger = useQuery({
    queryKey: ['supplier-ledger', id, page],
    enabled: tab === 'ledger',
    queryFn: () => unwrap(api.GET('/api/v1/suppliers/{supplier_id}/ledger', { params: { path: { supplier_id: id }, query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const orders = useQuery({
    queryKey: ['purchase-orders', 'supplier', id, page],
    enabled: tab === 'orders',
    queryFn: () => unwrap(api.GET('/api/v1/purchase-orders', { params: { query: { supplier_id: id, page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const payments = useQuery({
    queryKey: ['supplier-payments', 'supplier', id, page],
    enabled: tab === 'payments',
    queryFn: () => unwrap(api.GET('/api/v1/supplier-payments', { params: { query: { supplier_id: id, page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const ledgerColumns = useMemo<Column<Schemas['LedgerEntryRead']>[]>(
    () => [
      { header: t('stock.date'), cell: (e) => formatDate(e.business_date, lang) },
      { header: t('stock.document'), cell: (e) => <span className="font-medium">{e.source_doc_no ?? e.entry_no}</span> },
      { header: t('ledger.description'), cell: (e) => <span className="text-muted">{e.memo}</span> },
      { header: t('ledger.paid'), align: 'right', cell: (e) => (cmpMoney(e.debit, '0') > 0 ? <Taka value={e.debit} /> : '') },
      { header: t('ledger.goodsReceived'), align: 'right', cell: (e) => (cmpMoney(e.credit, '0') > 0 ? <Taka value={e.credit} /> : '') },
      {
        header: t('ledger.balance'),
        align: 'right',
        cell: (e) => <Taka value={e.balance} className={cn('font-medium', cmpMoney(e.balance, '0') < 0 ? 'text-danger' : 'text-warning')} />,
      },
    ],
    [t, lang],
  )
  const orderColumns = useMemo<Column<Schemas['POListItem']>[]>(
    () => [
      { header: t('purchases.order'), cell: (po) => <div><div className="font-medium">{po.doc_no ?? t('purchases.draftOrder')}</div><div className="text-xs text-muted">{formatDate(po.business_date, lang)}</div></div> },
      { header: t('common.status'), cell: (po) => <PoStatusBadge status={po.status} /> },
      { header: t('purchases.ordered'), align: 'right', cell: (po) => <Taka value={po.summary.ordered_value} /> },
      { header: t('purchases.received'), align: 'right', cell: (po) => <Taka value={po.summary.received_value} /> },
      { header: t('purchases.undelivered'), align: 'right', cell: (po) => <Taka value={po.summary.remaining_value} /> },
      { header: t('purchases.paid'), align: 'right', cell: (po) => <Taka value={po.summary.paid_value} /> },
    ],
    [t, lang],
  )
  const paymentColumns = useMemo<Column<Schemas['SupplierPaymentRead']>[]>(
    () => [
      { header: t('payment.title'), cell: (p) => <div><div className="font-medium">{p.doc_no}</div><div className="text-xs text-muted">{formatDate(p.business_date, lang)}</div></div> },
      { header: t('payment.paidFrom'), cell: (p) => p.payment_account_name },
      { header: t('common.status'), cell: (p) => <DocStatusBadge status={p.status} /> },
      { header: t('payment.amount'), align: 'right', cell: (p) => <Taka value={p.amount} /> },
      { header: t('payment.unallocated'), align: 'right', cell: (p) => (p.status === 'posted' && cmpMoney(p.unallocated_amount, '0') > 0 ? <Taka value={p.unallocated_amount} className="text-warning" /> : '—') },
    ],
    [t, lang],
  )

  if (supplier.isError) return <ErrorState error={supplier.error} onRetry={() => void supplier.refetch()} />
  const s = summary.data
  const current = tab === 'ledger' ? ledger : tab === 'orders' ? orders : payments

  return (
    <>
      <PageHeader
        title={supplier.data?.name ?? '…'}
        description={supplier.data ? `${supplier.data.code}${supplier.data.phone ? ` · ${supplier.data.phone}` : ''}` : undefined}
        actions={
          <>
            {can('purchasing:pay') && (
              <Button variant="outline" onClick={() => setPayOpen(true)}>
                <Banknote className="size-4" /> {t('purchases.recordPayment')}
              </Button>
            )}
            {can('purchasing:write') && (
              <Button onClick={() => void navigate({ to: '/purchases/new' })}>
                <Plus className="size-4" /> {t('purchases.newOrder')}
              </Button>
            )}
          </>
        }
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('supplierAccount.position')}</p>
          <p className={cn('num mt-1.5 text-xl font-semibold', s?.position === 'payable' && 'text-danger', s?.position === 'advance' && 'text-warning')}>
            <Taka value={s?.position === 'payable' ? s.payable : s?.advance} />
          </p>
          <div className="mt-1.5"><Badge tone={s?.position === 'payable' ? 'danger' : s?.position === 'advance' ? 'warning' : 'success'}>{t(`supplierAccount.${s?.position ?? 'settled'}`)}</Badge></div>
          <p className="mt-2 text-xs text-muted">{t(`supplierAccount.${s?.position ?? 'settled'}Hint`)}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('supplierAccount.outstandingGoods')}</p>
          <p className="num mt-1.5 text-xl font-semibold"><Taka value={s?.outstanding_po_value} /></p>
          <p className="mt-2 text-xs text-muted">{t('supplierAccount.outstandingGoodsHint')}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('supplierAccount.openOrders')}</p>
          <p className="num mt-1.5 text-xl font-semibold">{formatNumber(s?.open_po_count ?? 0, lang)}</p>
        </Card>
      </div>

      <Card>
        <Toolbar>
          <Tabs value={tab} onChange={(v) => { setTab(v); setPage(1) }} items={[{ value: 'ledger', label: t('supplierAccount.ledger') }, { value: 'orders', label: t('purchases.orders') }, { value: 'payments', label: t('purchases.payments') }]} />
        </Toolbar>
        {current.isError ? (
          <div className="p-4"><ErrorState error={current.error} onRetry={() => void current.refetch()} /></div>
        ) : tab === 'ledger' ? (
          <DataTable columns={ledgerColumns} data={ledger.data?.items} loading={ledger.isPending} />
        ) : tab === 'orders' ? (
          <DataTable columns={orderColumns} data={orders.data?.items} loading={orders.isPending} onRowClick={(po) => void navigate({ to: '/purchases/$poId', params: { poId: String(po.id) } })} />
        ) : (
          <DataTable columns={paymentColumns} data={payments.data?.items} loading={payments.isPending} onRowClick={(p) => setPaymentId(p.id)} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={current.data?.total ?? 0} onPageChange={setPage} />
        {tab === 'ledger' && <p className="border-t border-border px-4 py-3 text-xs text-muted">{t('supplierAccount.ledgerNote')}</p>}
      </Card>
      <PaymentDialog open={payOpen} onOpenChange={setPayOpen} supplierId={id} />
      <PaymentViewDialog paymentId={paymentId} onClose={() => setPaymentId(null)} />
    </>
  )
}
