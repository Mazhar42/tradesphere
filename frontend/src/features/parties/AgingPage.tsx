import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'

type Row = Schemas['AgingRow']

/** Who owes what, by how long ago the invoice was raised. */
export function AgingPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const navigate = useNavigate()
  const [asOf, setAsOf] = useState('')

  const query = useQuery({
    queryKey: ['customer-aging', asOf],
    queryFn: () =>
      unwrap(api.GET('/api/v1/reports/customer-aging', { params: { query: asOf ? { as_of: asOf } : {} } })),
  })

  const columns = useMemo<Column<Row>[]>(() => {
    const money = (key: keyof Row) => (row: Row) => (
      cmpMoney(String(row[key]), '0') === 0 ? <span className="text-muted">—</span> : <Taka value={String(row[key])} />
    )
    return [
      {
        header: t('customers.name'),
        cell: (row) => (
          <div>
            <div className="font-medium">{row.name}</div>
            <div className="text-xs text-muted">{row.code}</div>
          </div>
        ),
      },
      { header: t('aging.current'), align: 'right', cell: money('current') },
      { header: t('aging.days31'), align: 'right', cell: money('days_31_60') },
      { header: t('aging.days61'), align: 'right', cell: money('days_61_90') },
      { header: t('aging.over90'), align: 'right', cell: money('days_over_90') },
      { header: t('aging.invoiced'), align: 'right', cell: money('invoiced_due') },
      { header: t('aging.onAccount'), align: 'right', cell: money('on_account') },
      { header: t('accounts.total'), align: 'right', cell: (row) => <Taka value={row.total} className="font-medium" /> },
    ]
  }, [t])

  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const report = query.data

  return (
    <>
      <PageHeader
        title={t('aging.title')}
        description={t('aging.subtitle')}
        actions={
          <Input
            aria-label={t('aging.asOf')}
            type="date"
            className="w-44"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
          />
        }
      />
      <Card>
        <Toolbar>
          <span className="text-sm text-muted">
            {report && t('aging.asOfDate', { date: formatDate(report.as_of, lang) })}
          </span>
        </Toolbar>
        <DataTable
          columns={columns}
          data={report?.rows}
          loading={query.isPending}
          onRowClick={(row) => void navigate({ to: '/customers/$customerId', params: { customerId: String(row.customer_id) } })}
        />
        {report && report.rows.length > 0 && (
          <div className="grid grid-cols-2 gap-2 border-t border-border bg-surface-muted/40 px-4 py-3 text-sm sm:grid-cols-4 lg:grid-cols-8">
            <span className="font-medium">{t('accounts.total')}</span>
            <Taka value={report.totals.current} />
            <Taka value={report.totals.days_31_60} />
            <Taka value={report.totals.days_61_90} />
            <Taka value={report.totals.days_over_90} />
            <Taka value={report.totals.invoiced_due} />
            <Taka value={report.totals.on_account} />
            <Taka value={report.totals.total} className="font-semibold" />
          </div>
        )}
      </Card>
    </>
  )
}
