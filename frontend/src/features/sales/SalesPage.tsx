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
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'

import { ReceiptDialog } from './ReceiptDialog'
import { salesKeys } from './hooks'
import { DocBadge, PaymentBadge } from './shared'

type Tab = 'invoices' | 'receipts'
const PAGE_SIZE = 25
const INVOICE_STATUSES = ['draft', 'posted', 'reversed']

export function SalesPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('invoices')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [receiptOpen, setReceiptOpen] = useState(false)
  const canWrite = can('sales:write')

  const common = { q: q || undefined, status: status || undefined, page, page_size: PAGE_SIZE }

  const invoices = useQuery({
    queryKey: [...salesKeys.invoices, common],
    enabled: tab === 'invoices',
    queryFn: () => unwrap(api.GET('/api/v1/sales-invoices', { params: { query: common } })),
    placeholderData: keepPreviousData,
  })
  const receipts = useQuery({
    queryKey: [...salesKeys.receipts, { page, page_size: PAGE_SIZE }],
    enabled: tab === 'receipts',
    queryFn: () =>
      unwrap(api.GET('/api/v1/customer-receipts', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const invoiceColumns = useMemo<Column<Schemas['InvoiceListItem']>[]>(
    () => [
      {
        header: t('sales.invoice'),
        cell: (inv) => (
          <div>
            <div className="font-medium">{inv.doc_no ?? t('sales.draft')}</div>
            <div className="text-xs text-muted">{formatDate(inv.business_date, lang)}</div>
          </div>
        ),
      },
      {
        header: t('sales.customer'),
        cell: (inv) => (
          <div>
            <div>{inv.customer_name}</div>
            <div className="text-xs text-muted">{inv.customer_code}</div>
          </div>
        ),
      },
      { header: t('common.status'), cell: (inv) => <DocBadge status={inv.status} /> },
      { header: t('sales.net'), align: 'right', cell: (inv) => <Taka value={inv.net_amount} /> },
      { header: t('sales.received'), align: 'right', cell: (inv) => <Taka value={inv.received_amount} /> },
      {
        header: t('sales.due'),
        align: 'right',
        cell: (inv) =>
          cmpMoney(inv.due_amount, '0') > 0 ? (
            <Taka value={inv.due_amount} className="text-danger" />
          ) : (
            <span className="text-muted">—</span>
          ),
      },
      { header: t('sales.payment'), cell: (inv) => <PaymentBadge status={inv.payment_status} /> },
    ],
    [t, lang],
  )

  const receiptColumns = useMemo<Column<Schemas['ReceiptListItem']>[]>(
    () => [
      {
        header: t('sales.receipt'),
        cell: (r) => (
          <div>
            <div className="font-medium">{r.doc_no}</div>
            <div className="text-xs text-muted">{formatDate(r.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('sales.customer'), cell: (r) => r.customer_name },
      { header: t('sales.receivedInto'), cell: (r) => r.payment_account_name },
      { header: t('common.status'), cell: (r) => <DocBadge status={r.status} /> },
      { header: t('sales.amount'), align: 'right', cell: (r) => <Taka value={r.amount} /> },
      { header: t('sales.allocated'), align: 'right', cell: (r) => <Taka value={r.allocated} /> },
    ],
    [t, lang],
  )

  const total = (tab === 'invoices' ? invoices.data?.total : receipts.data?.total) ?? 0
  const current = tab === 'invoices' ? invoices : receipts

  return (
    <>
      <PageHeader
        title={t('sales.title')}
        description={t('sales.subtitle')}
        actions={
          canWrite && (
            <>
              <Button variant="outline" onClick={() => setReceiptOpen(true)}>
                <Banknote className="size-4" /> {t('sales.receivePayment')}
              </Button>
              <Button onClick={() => void navigate({ to: '/sales/new' })}>
                <Plus className="size-4" /> {t('sales.newInvoice')}
              </Button>
            </>
          )
        }
      />
      <Card>
        <Toolbar>
          <Tabs
            value={tab}
            onChange={(v) => {
              setTab(v)
              setPage(1)
              setStatus('')
            }}
            items={[
              { value: 'invoices' as const, label: t('sales.invoices') },
              { value: 'receipts' as const, label: t('sales.receipts') },
            ]}
          />
          {tab === 'invoices' && (
            <>
              <SearchInput
                value={q}
                placeholder={t('sales.searchPlaceholder')}
                onChange={(v) => {
                  setQ(v)
                  setPage(1)
                }}
              />
              <Select
                className="w-auto"
                aria-label={t('common.status')}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value)
                  setPage(1)
                }}
              >
                <option value="">{t('common.all')}</option>
                {INVOICE_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t(`sales.docStatus.${s}`)}
                  </option>
                ))}
              </Select>
            </>
          )}
        </Toolbar>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : tab === 'invoices' ? (
          <DataTable
            columns={invoiceColumns}
            data={invoices.data?.items}
            loading={invoices.isPending}
            onRowClick={(inv) => void navigate({ to: '/sales/$invoiceId', params: { invoiceId: String(inv.id) } })}
          />
        ) : (
          <DataTable columns={receiptColumns} data={receipts.data?.items} loading={receipts.isPending} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </Card>
      <ReceiptDialog open={receiptOpen} onOpenChange={setReceiptOpen} />
    </>
  )
}
