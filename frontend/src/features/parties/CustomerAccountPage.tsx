import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { Banknote, Plus } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { DocBadge, PaymentBadge } from '@/features/sales/shared'
import { ReceiptDialog } from '@/features/sales/ReceiptDialog'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

type Tab = 'statement' | 'invoices'
type LedgerRow = Schemas['LedgerEntryRead']
type InvoiceRow = Schemas['InvoiceListItem']
const PAGE_SIZE = 25

function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: 'danger' | 'success'; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p className={cn('num mt-1.5 text-xl font-semibold', tone === 'danger' && 'text-danger', tone === 'success' && 'text-success')}>{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  )
}

/** One customer: position, statement of account, and their invoices. */
export function CustomerAccountPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const { customerId } = useParams({ strict: false }) as { customerId: string }
  const id = Number(customerId)
  const [tab, setTab] = useState<Tab>('statement')
  const [page, setPage] = useState(1)
  const [receiptOpen, setReceiptOpen] = useState(false)

  const customer = useQuery({
    queryKey: ['customer', id],
    queryFn: () => unwrap(api.GET('/api/v1/customers/{customer_id}', { params: { path: { customer_id: id } } })),
  })
  const summary = useQuery({
    queryKey: ['customer-summary', id],
    queryFn: () => unwrap(api.GET('/api/v1/customers/{customer_id}/summary', { params: { path: { customer_id: id } } })),
  })
  const ledger = useQuery({
    queryKey: ['customer-ledger', id, page],
    enabled: tab === 'statement',
    queryFn: () =>
      unwrap(api.GET('/api/v1/customers/{customer_id}/ledger', { params: { path: { customer_id: id }, query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const invoices = useQuery({
    queryKey: ['sales-invoices', 'customer', id, page],
    enabled: tab === 'invoices',
    queryFn: () =>
      unwrap(api.GET('/api/v1/sales-invoices', { params: { query: { customer_id: id, page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const ledgerColumns = useMemo<Column<LedgerRow>[]>(
    () => [
      { header: t('sales.date'), cell: (e) => formatDate(e.business_date, lang) },
      {
        header: t('sales.document'),
        cell: (e) => (
          <div>
            <div className="font-medium">{e.source_doc_no ?? e.entry_no}</div>
            <div className="text-xs text-muted">{e.memo}</div>
          </div>
        ),
      },
      { header: t('ledger.debit'), align: 'right', cell: (e) => (cmpMoney(e.debit, '0') > 0 ? <Taka value={e.debit} /> : <span className="text-muted">—</span>) },
      { header: t('ledger.credit'), align: 'right', cell: (e) => (cmpMoney(e.credit, '0') > 0 ? <Taka value={e.credit} /> : <span className="text-muted">—</span>) },
      {
        header: t('ledger.balance'),
        align: 'right',
        cell: (e) => <Taka value={e.balance} className={cmpMoney(e.balance, '0') < 0 ? 'text-success' : 'font-medium'} />,
      },
    ],
    [t, lang],
  )

  const invoiceColumns = useMemo<Column<InvoiceRow>[]>(
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
      { header: t('common.status'), cell: (inv) => <DocBadge status={inv.status} /> },
      { header: t('sales.net'), align: 'right', cell: (inv) => <Taka value={inv.net_amount} /> },
      { header: t('sales.due'), align: 'right', cell: (inv) => <Taka value={inv.due_amount} /> },
      { header: t('sales.payment'), cell: (inv) => <PaymentBadge status={inv.payment_status} /> },
    ],
    [t, lang],
  )

  if (customer.isError) return <ErrorState error={customer.error} onRetry={() => void customer.refetch()} />
  if (!customer.data || !summary.data) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />

  const c = customer.data
  const s = summary.data
  const current = tab === 'statement' ? ledger : invoices
  const total = current.data?.total ?? 0
  const canSell = can('sales:write')

  return (
    <>
      <PageHeader
        title={c.name}
        description={[c.code, c.shop_name, c.area].filter(Boolean).join(' · ')}
        actions={
          canSell && (
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

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label={t('customerAccount.owes')}
          value={<Taka value={s.balance} />}
          tone={cmpMoney(s.balance, '0') > 0 ? 'danger' : cmpMoney(s.balance, '0') < 0 ? 'success' : undefined}
          hint={cmpMoney(s.balance, '0') < 0 ? t('customerAccount.creditHeld') : undefined}
        />
        <Stat label={t('customers.creditLimit')} value={Number(s.credit_limit) > 0 ? <Taka value={s.credit_limit} /> : t('customers.cashOnly')} />
        <Stat label={t('customerAccount.availableCredit')} value={<Taka value={s.available_credit} />} />
        <Stat label={t('customerAccount.openInvoices')} value={<span>{s.open_invoices}</span>} />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <Tabs
            value={tab}
            onChange={(v) => {
              setTab(v)
              setPage(1)
            }}
            items={[
              { value: 'statement' as const, label: t('customerAccount.statement') },
              { value: 'invoices' as const, label: t('sales.invoices') },
            ]}
          />
        </div>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : tab === 'statement' ? (
          <DataTable columns={ledgerColumns} data={ledger.data?.items} loading={ledger.isPending} />
        ) : (
          <DataTable
            columns={invoiceColumns}
            data={invoices.data?.items}
            loading={invoices.isPending}
            onRowClick={(inv) => void navigate({ to: '/sales/$invoiceId', params: { invoiceId: String(inv.id) } })}
          />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </Card>

      <p className="mt-4 text-sm text-muted">
        <Link to="/customers" className="text-primary hover:underline">
          {t('customerAccount.backToCustomers')}
        </Link>
      </p>
      <ReceiptDialog open={receiptOpen} onOpenChange={setReceiptOpen} customer={{ id: c.id, name: c.name }} />
    </>
  )
}
