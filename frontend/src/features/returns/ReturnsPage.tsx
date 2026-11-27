import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ReverseButton } from '@/components/data/ReverseButton'
import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { Taka } from '@/features/purchases/shared'
import { DocBadge } from '@/features/sales/shared'
import { useLang } from '@/i18n'
import { formatDate } from '@/lib/format'

type Tab = 'sales' | 'purchase'
const PAGE_SIZE = 25

export function ReturnsPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('sales')
  const [page, setPage] = useState(1)

  const sales = useQuery({
    queryKey: ['sales-returns', page],
    enabled: tab === 'sales',
    queryFn: () => unwrap(api.GET('/api/v1/sales-returns', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const purchase = useQuery({
    queryKey: ['purchase-returns', page],
    enabled: tab === 'purchase' && can('purchasing:read'),
    queryFn: () => unwrap(api.GET('/api/v1/purchase-returns', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const salesColumns = useMemo<Column<Schemas['SalesReturnListItem']>[]>(
    () => [
      {
        header: t('returns.return'),
        cell: (r) => (
          <div>
            <div className="font-medium">{r.doc_no ?? t('sales.draft')}</div>
            <div className="text-xs text-muted">{formatDate(r.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('sales.invoice'), cell: (r) => r.invoice_doc_no ?? '—' },
      { header: t('sales.customer'), cell: (r) => r.customer_name },
      { header: t('returns.refund'), cell: (r) => t(`returns.refunds.${r.refund_method}`, r.refund_method) },
      { header: t('common.status'), cell: (r) => <DocBadge status={r.status} /> },
      { header: t('returns.value'), align: 'right', cell: (r) => <Taka value={r.net_amount} /> },
      {
        id: 'reverse',
        header: '',
        align: 'right',
        cell: (r) => (
          <ReverseButton
            docNo={r.doc_no ?? ''}
            status={r.status}
            allowed={can('sales:reverse')}
            post={(header) => api.POST('/api/v1/sales-returns/{return_id}/reverse', { params: { path: { return_id: r.id }, header } })}
          />
        ),
      },
    ],
    [t, lang, can],
  )
  const purchaseColumns = useMemo<Column<Schemas['PurchaseReturnRead']>[]>(
    () => [
      {
        header: t('returns.return'),
        cell: (r) => (
          <div>
            <div className="font-medium">{r.doc_no ?? t('sales.draft')}</div>
            <div className="text-xs text-muted">{formatDate(r.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('claims.company'), cell: (r) => r.supplier_name },
      { header: t('common.status'), cell: (r) => <DocBadge status={r.status} /> },
      { header: t('returns.creditValue'), align: 'right', cell: (r) => <Taka value={r.total_value} /> },
      { header: t('returns.bookCost'), align: 'right', cell: (r) => <Taka value={r.total_cost} /> },
      {
        id: 'reverse',
        header: '',
        align: 'right',
        cell: (r) => (
          <ReverseButton
            docNo={r.doc_no ?? ''}
            status={r.status}
            allowed={can('purchasing:reverse')}
            post={(header) => api.POST('/api/v1/purchase-returns/{return_id}/reverse', { params: { path: { return_id: r.id }, header } })}
          />
        ),
      },
    ],
    [t, lang, can],
  )

  const current = tab === 'sales' ? sales : purchase
  const tabs = [
    { value: 'sales' as const, label: t('returns.salesReturns') },
    ...(can('purchasing:read') ? [{ value: 'purchase' as const, label: t('returns.purchaseReturns') }] : []),
  ]

  return (
    <>
      <PageHeader
        title={t('returns.title')}
        description={t('returns.subtitle')}
        actions={
          can('purchasing:write') && (
            <Button onClick={() => void navigate({ to: '/returns/purchase/new' })}>
              <Plus className="size-4" /> {t('returns.newPurchaseReturn')}
            </Button>
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
            }}
            items={tabs}
          />
          <span className="text-sm text-muted">{tab === 'sales' ? t('returns.salesHint') : ''}</span>
        </Toolbar>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : tab === 'sales' ? (
          <DataTable
            columns={salesColumns}
            data={sales.data?.items}
            loading={sales.isPending}
            onRowClick={(r) => void navigate({ to: '/sales/$invoiceId', params: { invoiceId: String(r.invoice_id) } })}
          />
        ) : (
          <DataTable columns={purchaseColumns} data={purchase.data?.items} loading={purchase.isPending} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={current.data?.total ?? 0} onPageChange={setPage} />
      </Card>
    </>
  )
}
