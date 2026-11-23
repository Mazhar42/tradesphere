import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Banknote, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'

import { PaymentDialog } from './PaymentDialog'
import { purchaseKeys, useActiveSuppliers } from './hooks'
import { DocStatusBadge, PoStatusBadge, Taka } from './shared'
import { GrnViewDialog, PaymentViewDialog } from './ViewDialogs'

type Tab = 'orders' | 'receipts' | 'payments'
const PAGE_SIZE = 25
const PO_STATUSES = ['draft', 'confirmed', 'partially_received', 'received', 'closed_short', 'cancelled']

function useListState() {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [supplierId, setSupplierId] = useState('')
  const [status, setStatus] = useState('')
  return { q, setQ, page, setPage, supplierId, setSupplierId, status, setStatus }
}

export function PurchasesPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const suppliers = useActiveSuppliers()
  const [tab, setTab] = useState<Tab>('orders')
  const [payOpen, setPayOpen] = useState(false)
  const [grnId, setGrnId] = useState<number | null>(null)
  const [paymentId, setPaymentId] = useState<number | null>(null)
  const s = useListState()
  const canPay = can('purchasing:pay')

  const common = { q: s.q || undefined, supplier_id: s.supplierId ? Number(s.supplierId) : undefined, status: s.status || undefined, page: s.page, page_size: PAGE_SIZE }

  const orders = useQuery({
    queryKey: [...purchaseKeys.orders, common],
    enabled: tab === 'orders',
    queryFn: () => unwrap(api.GET('/api/v1/purchase-orders', { params: { query: common } })),
    placeholderData: keepPreviousData,
  })
  const receipts = useQuery({
    queryKey: [...purchaseKeys.receipts, common],
    enabled: tab === 'receipts',
    queryFn: () => unwrap(api.GET('/api/v1/goods-receipts', { params: { query: common } })),
    placeholderData: keepPreviousData,
  })
  const payments = useQuery({
    queryKey: [...purchaseKeys.payments, common],
    enabled: tab === 'payments' && canPay,
    queryFn: () => unwrap(api.GET('/api/v1/supplier-payments', { params: { query: common } })),
    placeholderData: keepPreviousData,
  })

  const orderColumns = useMemo<Column<Schemas['POListItem']>[]>(
    () => [
      {
        header: t('purchases.order'),
        cell: (po) => (
          <div>
            <div className="font-medium">{po.doc_no ?? t('purchases.draftOrder')}</div>
            <div className="text-xs text-muted">{formatDate(po.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('purchases.supplier'), cell: (po) => po.supplier_name },
      { header: t('common.status'), cell: (po) => <PoStatusBadge status={po.status} /> },
      { header: t('purchases.ordered'), align: 'right', cell: (po) => <Taka value={po.summary.ordered_value} /> },
      { header: t('purchases.received'), align: 'right', cell: (po) => <Taka value={po.summary.received_value} /> },
      {
        header: t('purchases.undelivered'),
        align: 'right',
        cell: (po) => (
          <Taka value={po.summary.remaining_value} className={cmpMoney(po.summary.remaining_value, '0') > 0 ? 'text-warning' : 'text-muted'} />
        ),
      },
      { header: t('purchases.paid'), align: 'right', cell: (po) => <Taka value={po.summary.paid_value} /> },
      {
        header: t('purchases.advanceOrDue'),
        align: 'right',
        cell: (po) =>
          cmpMoney(po.summary.balance_due, '0') > 0 ? (
            <Taka value={po.summary.balance_due} className="text-danger" />
          ) : cmpMoney(po.summary.unapplied_advance, '0') > 0 ? (
            <Taka value={po.summary.unapplied_advance} className="text-warning" />
          ) : (
            <span className="text-muted">—</span>
          ),
      },
    ],
    [t, lang],
  )

  const receiptColumns = useMemo<Column<Schemas['GrnListItem']>[]>(
    () => [
      { header: t('purchases.receipt'), cell: (g) => <div><div className="font-medium">{g.doc_no ?? t('purchases.docStatus.draft')}</div><div className="text-xs text-muted">{formatDate(g.business_date, lang)}</div></div> },
      { header: t('purchases.order'), cell: (g) => g.po_doc_no ?? '—' },
      { header: t('purchases.supplier'), cell: (g) => g.supplier_name },
      { header: t('receive.challan'), cell: (g) => g.challan_no ?? '—' },
      { header: t('common.status'), cell: (g) => <DocStatusBadge status={g.status} /> },
      { header: t('purchases.value'), align: 'right', cell: (g) => <Taka value={g.total_value} /> },
    ],
    [t, lang],
  )

  const paymentColumns = useMemo<Column<Schemas['SupplierPaymentRead']>[]>(
    () => [
      { header: t('payment.title'), cell: (p) => <div><div className="font-medium">{p.doc_no}</div><div className="text-xs text-muted">{formatDate(p.business_date, lang)}</div></div> },
      { header: t('purchases.supplier'), cell: (p) => p.supplier_name },
      { header: t('payment.paidFrom'), cell: (p) => p.payment_account_name },
      { header: t('payment.reference'), cell: (p) => p.reference ?? '—' },
      { header: t('common.status'), cell: (p) => <DocStatusBadge status={p.status} /> },
      { header: t('payment.amount'), align: 'right', cell: (p) => <Taka value={p.amount} /> },
      {
        header: t('payment.unallocated'),
        align: 'right',
        cell: (p) => (cmpMoney(p.unallocated_amount, '0') > 0 && p.status === 'posted' ? <Taka value={p.unallocated_amount} className="text-warning" /> : <span className="text-muted">—</span>),
      },
    ],
    [t, lang],
  )

  const current = tab === 'orders' ? orders : tab === 'receipts' ? receipts : payments
  const total = current.data?.total ?? 0
  const tabs = [
    { value: 'orders' as const, label: t('purchases.orders') },
    { value: 'receipts' as const, label: t('purchases.receipts') },
    ...(canPay ? [{ value: 'payments' as const, label: t('purchases.payments') }] : []),
  ]

  return (
    <>
      <PageHeader
        title={t('purchases.title')}
        description={t('purchases.subtitle')}
        actions={
          <>
            {canPay && (
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
      <Card>
        <Toolbar>
          <Tabs
            value={tab}
            onChange={(v) => {
              setTab(v)
              s.setPage(1)
              s.setStatus('')
            }}
            items={tabs}
          />
          <SearchInput value={s.q} onChange={(v) => { s.setQ(v); s.setPage(1) }} />
          <Select className="w-auto" aria-label={t('purchases.supplier')} value={s.supplierId} onChange={(e) => { s.setSupplierId(e.target.value); s.setPage(1) }}>
            <option value="">{t('purchases.allSuppliers')}</option>
            {suppliers.data?.map((sp) => (
              <option key={sp.id} value={sp.id}>{sp.name}</option>
            ))}
          </Select>
          <Select className="w-auto" aria-label={t('common.status')} value={s.status} onChange={(e) => { s.setStatus(e.target.value); s.setPage(1) }}>
            <option value="">{t('common.all')}</option>
            {(tab === 'orders' ? PO_STATUSES : tab === 'receipts' ? ['draft', 'posted', 'reversed'] : ['posted', 'reversed']).map((st) => (
              <option key={st} value={st}>
                {tab === 'orders' ? t(`purchases.status.${st}`) : t(`purchases.docStatus.${st}`)}
              </option>
            ))}
          </Select>
        </Toolbar>
        {current.isError ? (
          <div className="p-4"><ErrorState error={current.error} onRetry={() => void current.refetch()} /></div>
        ) : tab === 'orders' ? (
          <DataTable columns={orderColumns} data={orders.data?.items} loading={orders.isPending}
            onRowClick={(po) => void navigate({ to: '/purchases/$poId', params: { poId: String(po.id) } })} />
        ) : tab === 'receipts' ? (
          <DataTable columns={receiptColumns} data={receipts.data?.items} loading={receipts.isPending} onRowClick={(g) => setGrnId(g.id)} />
        ) : (
          <DataTable columns={paymentColumns} data={payments.data?.items} loading={payments.isPending} onRowClick={(p) => setPaymentId(p.id)} />
        )}
        <Pagination page={s.page} pageSize={PAGE_SIZE} total={total} onPageChange={s.setPage} />
      </Card>
      <PaymentDialog open={payOpen} onOpenChange={setPayOpen} />
      <GrnViewDialog grnId={grnId} onClose={() => setGrnId(null)} />
      <PaymentViewDialog paymentId={paymentId} onClose={() => setPaymentId(null)} />
    </>
  )
}
