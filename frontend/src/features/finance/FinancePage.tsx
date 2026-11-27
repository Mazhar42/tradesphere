import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ReverseButton } from '@/components/data/ReverseButton'
import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { formatDate } from '@/lib/format'

import { ExpenseDialog, OwnerDialog, TransferDialog } from './FinanceDialogs'
import { financeKeys } from './hooks'

type Tab = 'expenses' | 'transfers' | 'owner'
type Expense = Schemas['ExpenseRead']
type Transfer = Schemas['FundTransferRead']
type Owner = Schemas['OwnerTransactionRead']

const PAGE_SIZE = 25

export function FinancePage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const canWrite = can('finance:write')

  const [tab, setTab] = useState<Tab>('expenses')
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState<Tab | null>(null)

  const expenses = useQuery({
    queryKey: [...financeKeys.expenses, page],
    enabled: tab === 'expenses',
    queryFn: () => unwrap(api.GET('/api/v1/expenses', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const transfers = useQuery({
    queryKey: [...financeKeys.transfers, page],
    enabled: tab === 'transfers',
    queryFn: () => unwrap(api.GET('/api/v1/fund-transfers', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const owner = useQuery({
    queryKey: [...financeKeys.owner, page],
    enabled: tab === 'owner',
    queryFn: () => unwrap(api.GET('/api/v1/owner-transactions', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const expenseColumns: Column<Expense>[] = [
    { header: t('finance.columns.date'), cell: (e) => formatDate(e.business_date, lang) },
    { header: t('finance.columns.doc'), cell: (e) => <span className="num">{e.doc_no}</span> },
    { header: t('finance.columns.account'), cell: (e) => `${e.expense_account_code} ${e.expense_account_name}` },
    { header: t('finance.columns.payee'), cell: (e) => e.payee ?? '—' },
    { header: t('finance.columns.paidFrom'), cell: (e) => e.payment_account_name },
    { header: t('sales.amount'), align: 'right', cell: (e) => <Taka value={e.amount} /> },
    {
      id: 'reverse',
      header: '',
      align: 'right',
      cell: (e) => (
        <ReverseButton
          docNo={e.doc_no ?? ''}
          status={e.status}
          allowed={can('finance:reverse')}
          post={(header) => api.POST('/api/v1/expenses/{expense_id}/reverse', { params: { path: { expense_id: e.id }, header } })}
        />
      ),
    },
  ]
  const transferColumns: Column<Transfer>[] = [
    { header: t('finance.columns.date'), cell: (e) => formatDate(e.business_date, lang) },
    { header: t('finance.columns.doc'), cell: (e) => <span className="num">{e.doc_no}</span> },
    { header: t('finance.columns.from'), cell: (e) => e.from_account },
    { header: t('finance.columns.to'), cell: (e) => e.to_account },
    { header: t('sales.amount'), align: 'right', cell: (e) => <Taka value={e.amount} /> },
    { header: t('finance.columns.charges'), align: 'right', cell: (e) => <Taka value={e.charges} /> },
    { header: t('finance.columns.reference'), cell: (e) => e.reference ?? '—' },
    {
      id: 'reverse',
      header: '',
      align: 'right',
      cell: (e) => (
        <ReverseButton
          docNo={e.doc_no ?? ''}
          status={e.status}
          allowed={can('finance:reverse')}
          post={(header) => api.POST('/api/v1/fund-transfers/{fund_transfer_id}/reverse', { params: { path: { fund_transfer_id: e.id }, header } })}
        />
      ),
    },
  ]
  const ownerColumns: Column<Owner>[] = [
    { header: t('finance.columns.date'), cell: (e) => formatDate(e.business_date, lang) },
    { header: t('finance.columns.doc'), cell: (e) => <span className="num">{e.doc_no}</span> },
    { header: t('finance.columns.kind'), cell: (e) => t(`finance.kinds.${e.kind}`, e.kind) },
    { header: t('finance.owner.account'), cell: (e) => e.payment_account_name },
    { header: t('sales.amount'), align: 'right', cell: (e) => <Taka value={e.amount} /> },
    { header: t('finance.columns.notes'), cell: (e) => e.notes ?? '—' },
    {
      id: 'reverse',
      header: '',
      align: 'right',
      cell: (e) => (
        <ReverseButton
          docNo={e.doc_no ?? ''}
          status={e.status}
          allowed={can('finance:reverse')}
          post={(header) => api.POST('/api/v1/owner-transactions/{owner_transaction_id}/reverse', { params: { path: { owner_transaction_id: e.id }, header } })}
        />
      ),
    },
  ]

  const active = tab === 'expenses' ? expenses : tab === 'transfers' ? transfers : owner
  const total = active.data?.total

  return (
    <>
      <PageHeader
        title={t('finance.title')}
        description={t('finance.subtitle')}
        actions={
          canWrite ? (
            <Button onClick={() => setDialog(tab)}>
              <Plus className="size-4" aria-hidden />
              {t(`finance.actions.${tab}`)}
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4">
        <Tabs<Tab>
          value={tab}
          onChange={(next) => {
            setTab(next)
            setPage(1)
          }}
          items={[
            { value: 'expenses', label: t('finance.tabs.expenses') },
            { value: 'transfers', label: t('finance.tabs.transfers') },
            { value: 'owner', label: t('finance.tabs.owner') },
          ]}
        />
      </div>

      {active.isError ? (
        <ErrorState error={active.error} onRetry={() => void active.refetch()} />
      ) : (
        <Card className="overflow-hidden">
          {tab === 'expenses' && (
            <DataTable columns={expenseColumns} data={expenses.data?.items} loading={expenses.isPending} />
          )}
          {tab === 'transfers' && (
            <DataTable columns={transferColumns} data={transfers.data?.items} loading={transfers.isPending} />
          )}
          {tab === 'owner' && (
            <DataTable columns={ownerColumns} data={owner.data?.items} loading={owner.isPending} />
          )}
          {total !== undefined && <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />}
        </Card>
      )}

      {canWrite && (
        <>
          <ExpenseDialog open={dialog === 'expenses'} onOpenChange={(o) => setDialog(o ? 'expenses' : null)} />
          <TransferDialog open={dialog === 'transfers'} onOpenChange={(o) => setDialog(o ? 'transfers' : null)} />
          <OwnerDialog open={dialog === 'owner'} onOpenChange={(o) => setDialog(o ? 'owner' : null)} />
        </>
      )}
    </>
  )
}
