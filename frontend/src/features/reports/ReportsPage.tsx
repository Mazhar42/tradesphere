import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { Num, PoStatusBadge, Taka } from '@/features/purchases/shared'
import { useSalesPaymentAccounts } from '@/features/sales/hooks'
import { useLang } from '@/i18n'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

type ReportTab = 'pl' | 'cashbook' | 'margin' | 'valuation' | 'expiry' | 'claims' | 'po' | 'sales'
type MarginBy = 'product' | 'customer' | 'category' | 'brand'
type SalesGroup = 'day' | 'month'

/** Each report needs one permission; the cost-bearing ones need reports:profit. */
const TAB_PERMISSION: Record<ReportTab, string> = {
  pl: 'reports:profit',
  cashbook: 'finance:read',
  margin: 'reports:profit',
  valuation: 'reports:profit',
  expiry: 'reports:read',
  claims: 'claims:read',
  po: 'purchasing:read',
  sales: 'reports:read',
}
const TAB_ORDER: ReportTab[] = ['pl', 'cashbook', 'margin', 'valuation', 'expiry', 'claims', 'po', 'sales']
const DATE_RANGE_TABS: ReportTab[] = ['pl', 'cashbook', 'margin', 'sales']

function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

function Summary({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item) => (
        <Card key={item.label} className="p-5">
          <p className="text-sm font-medium text-muted">{item.label}</p>
          <p className="num mt-2 text-xl font-semibold">{item.value}</p>
        </Card>
      ))}
    </div>
  )
}

// --- Profit and loss -------------------------------------------------------------------------------
function ProfitLossTab({ from, to }: { from: string; to: string }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'pl', from, to],
    queryFn: () => unwrap(api.GET('/api/v1/reports/profit-loss', { params: { query: { date_from: from, date_to: to } } })),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const r = query.data
  if (!r) return null
  const lines: { label: string; value: ReactNode; strong?: boolean }[] = [
    { label: t('reports.pl.grossSales'), value: <Taka value={r.gross_sales} /> },
    { label: t('reports.pl.salesReturns'), value: <Taka value={r.sales_returns} /> },
    { label: t('reports.pl.dealerDiscounts'), value: <Taka value={r.dealer_discounts} /> },
    { label: t('reports.pl.netSales'), value: <Taka value={r.net_sales} />, strong: true },
    { label: t('reports.pl.cogs'), value: <Taka value={r.cogs} /> },
    { label: t('reports.pl.grossProfit'), value: <Taka value={r.gross_profit} />, strong: true },
    { label: t('reports.pl.grossMargin'), value: <Num value={`${r.gross_margin_pct}`} /> },
    { label: t('reports.pl.schemeIncome'), value: <Taka value={r.scheme_income} /> },
    { label: t('reports.pl.otherIncome'), value: <Taka value={r.other_income} /> },
    { label: t('reports.pl.writeOffs'), value: <Taka value={r.write_offs} /> },
    { label: t('reports.pl.dealerCommission'), value: <Taka value={r.dealer_commission} /> },
    { label: t('reports.pl.operatingExpenses'), value: <Taka value={r.operating_expenses} /> },
    ...r.expense_lines.map((line) => ({ label: `· ${line.label}`, value: <Taka value={line.amount} /> })),
    { label: t('reports.pl.netProfit'), value: <Taka value={r.net_profit} />, strong: true },
  ]
  return (
    <Card className="overflow-hidden">
      <table className="w-full text-sm">
        <tbody>
          {lines.map((line, index) => (
            <tr key={index} className="border-b border-border last:border-0">
              <td className={cn('px-5 py-2.5', line.strong && 'font-semibold')}>{line.label}</td>
              <td className={cn('px-5 py-2.5 text-right', line.strong && 'font-semibold')}>{line.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

// --- Cashbook --------------------------------------------------------------------------------------
function CashbookTab({ from, to, accountId }: { from: string; to: string; accountId: string }) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const query = useQuery({
    queryKey: ['reports', 'cashbook', from, to, accountId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/reports/cashbook', {
          params: {
            query: { date_from: from, date_to: to, payment_account_id: accountId ? Number(accountId) : undefined },
          },
        }),
      ),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const r = query.data
  const columns: Column<Schemas['CashDay']>[] = [
    { header: t('reports.columns.date'), cell: (d) => formatDate(d.business_date, lang) },
    { header: t('reports.cashbook.opening'), align: 'right', cell: (d) => <Taka value={d.opening} /> },
    { header: t('reports.cashbook.inflow'), align: 'right', cell: (d) => <Taka value={d.inflow} /> },
    { header: t('reports.cashbook.outflow'), align: 'right', cell: (d) => <Taka value={d.outflow} /> },
    { header: t('reports.cashbook.closing'), align: 'right', cell: (d) => <Taka value={d.closing} /> },
  ]
  return (
    <>
      {r && (
        <Summary
          items={[
            { label: t('reports.cashbook.opening'), value: <Taka value={r.opening} /> },
            { label: t('reports.cashbook.inflow'), value: <Taka value={r.inflow} /> },
            { label: t('reports.cashbook.outflow'), value: <Taka value={r.outflow} /> },
            { label: t('reports.cashbook.closing'), value: <Taka value={r.closing} /> },
          ]}
        />
      )}
      <Card className="overflow-hidden">
        <DataTable columns={columns} data={r?.days} loading={query.isPending} />
      </Card>
    </>
  )
}

// --- Margins ---------------------------------------------------------------------------------------
function MarginTab({ from, to, by }: { from: string; to: string; by: MarginBy }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'margin', from, to, by],
    queryFn: () => unwrap(api.GET('/api/v1/reports/margin', { params: { query: { date_from: from, date_to: to, by } } })),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['MarginRow']>[] = [
    { header: t('reports.columns.label'), cell: (r) => r.label },
    { header: t('reports.columns.qty'), align: 'right', cell: (r) => <Num value={r.qty_base} /> },
    { header: t('reports.columns.gross'), align: 'right', cell: (r) => <Taka value={r.gross_sales} /> },
    { header: t('reports.columns.returns'), align: 'right', cell: (r) => <Taka value={r.returns} /> },
    { header: t('reports.columns.dealer'), align: 'right', cell: (r) => <Taka value={r.dealer_discounts} /> },
    { header: t('reports.columns.netSales'), align: 'right', cell: (r) => <Taka value={r.net_sales} /> },
    { header: t('reports.columns.cogs'), align: 'right', cell: (r) => <Taka value={r.cogs} /> },
    { header: t('reports.columns.grossProfit'), align: 'right', cell: (r) => <Taka value={r.gross_profit} /> },
    { header: t('reports.columns.marginPct'), align: 'right', cell: (r) => <Num value={`${r.margin_pct}`} /> },
  ]
  return (
    <Card className="overflow-hidden">
      <DataTable columns={columns} data={query.data?.rows} loading={query.isPending} />
    </Card>
  )
}

// --- Stock value and near-expiry -------------------------------------------------------------------
function ValuationTab() {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'valuation'],
    queryFn: () => unwrap(api.GET('/api/v1/reports/stock-valuation')),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['ValuationRow']>[] = [
    { header: t('reports.columns.sku'), cell: (r) => <span className="num">{r.sku}</span> },
    { header: t('reports.columns.product'), cell: (r) => r.name },
    { header: t('reports.columns.qty'), align: 'right', cell: (r) => <Num value={r.qty_base} /> },
    { header: t('reports.columns.avgCost'), align: 'right', cell: (r) => <Taka value={r.avg_cost} /> },
    { header: t('reports.columns.value'), align: 'right', cell: (r) => <Taka value={r.value} /> },
  ]
  return (
    <>
      {query.data && <Summary items={[{ label: t('reports.valuation.total'), value: <Taka value={query.data.total_value} /> }]} />}
      <Card className="overflow-hidden">
        <DataTable columns={columns} data={query.data?.rows} loading={query.isPending} />
      </Card>
    </>
  )
}

function NearExpiryTab({ days }: { days: number }) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const query = useQuery({
    queryKey: ['reports', 'near-expiry', days],
    queryFn: () => unwrap(api.GET('/api/v1/reports/near-expiry', { params: { query: { days } } })),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['NearExpiryRow']>[] = [
    { header: t('reports.columns.sku'), cell: (r) => <span className="num">{r.sku}</span> },
    { header: t('reports.columns.product'), cell: (r) => r.name },
    { header: t('reports.columns.batch'), cell: (r) => r.batch_no },
    { header: t('reports.columns.expiry'), cell: (r) => formatDate(r.expiry_date, lang) },
    { header: t('reports.columns.daysLeft'), align: 'right', cell: (r) => <Num value={r.days_left} /> },
    { header: t('reports.columns.qty'), align: 'right', cell: (r) => <Num value={r.qty_base} /> },
    { header: t('reports.columns.value'), align: 'right', cell: (r) => <Taka value={r.value} /> },
  ]
  return (
    <Card className="overflow-hidden">
      <DataTable columns={columns} data={query.data?.rows} loading={query.isPending} />
    </Card>
  )
}

// --- Claims aging and purchase orders --------------------------------------------------------------
function ClaimsAgingTab({ asOf }: { asOf: string }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'claims-aging', asOf],
    queryFn: () => unwrap(api.GET('/api/v1/reports/claims-aging', { params: { query: { as_of: asOf } } })),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['ClaimAgingRow']>[] = [
    { header: t('reports.columns.company'), cell: (r) => r.supplier },
    { header: t('reports.columns.bucket0_30'), align: 'right', cell: (r) => <Taka value={r.current} /> },
    { header: t('reports.columns.bucket31_60'), align: 'right', cell: (r) => <Taka value={r.days_31_60} /> },
    { header: t('reports.columns.bucket61_90'), align: 'right', cell: (r) => <Taka value={r.days_61_90} /> },
    { header: t('reports.columns.bucketOver90'), align: 'right', cell: (r) => <Taka value={r.days_over_90} /> },
    { header: t('reports.columns.total'), align: 'right', cell: (r) => <Taka value={r.total} /> },
  ]
  return (
    <>
      {query.data && <Summary items={[{ label: t('reports.columns.total'), value: <Taka value={query.data.total} /> }]} />}
      <Card className="overflow-hidden">
        <DataTable columns={columns} data={query.data?.rows} loading={query.isPending} />
      </Card>
    </>
  )
}

function PoStatusTab() {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'po-status'],
    queryFn: () => unwrap(api.GET('/api/v1/reports/po-status')),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['POStatusRow']>[] = [
    { header: t('reports.columns.po'), cell: (r) => <span className="num">{r.doc_no}</span> },
    { header: t('reports.columns.company'), cell: (r) => r.supplier },
    { header: t('reports.columns.status'), cell: (r) => <PoStatusBadge status={r.status} /> },
    { header: t('reports.columns.ordered'), align: 'right', cell: (r) => <Taka value={r.ordered} /> },
    { header: t('reports.columns.received'), align: 'right', cell: (r) => <Taka value={r.received} /> },
    { header: t('reports.columns.undelivered'), align: 'right', cell: (r) => <Taka value={r.undelivered} /> },
    { header: t('reports.columns.paid'), align: 'right', cell: (r) => <Taka value={r.paid} /> },
    { header: t('reports.columns.balanceDue'), align: 'right', cell: (r) => <Taka value={r.balance_due} /> },
    { header: t('reports.columns.advance'), align: 'right', cell: (r) => <Taka value={r.advance} /> },
  ]
  return (
    <Card className="overflow-hidden">
      <DataTable columns={columns} data={query.data?.rows} loading={query.isPending} />
    </Card>
  )
}

// --- Sales summary ---------------------------------------------------------------------------------
function SalesSummaryTab({ from, to, group }: { from: string; to: string; group: SalesGroup }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['reports', 'sales-summary', from, to, group],
    queryFn: () =>
      unwrap(api.GET('/api/v1/reports/sales-summary', { params: { query: { date_from: from, date_to: to, group } } })),
  })
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const columns: Column<Schemas['SalesSummaryRow']>[] = [
    { header: t('reports.columns.period'), cell: (r) => <span className="num">{r.period}</span> },
    { header: t('reports.columns.gross'), align: 'right', cell: (r) => <Taka value={r.gross_amount} /> },
    { header: t('reports.columns.returns'), align: 'right', cell: (r) => <Taka value={r.returns_amount} /> },
    { header: t('reports.columns.dealer'), align: 'right', cell: (r) => <Taka value={r.dealer_discount} /> },
    { header: t('reports.columns.netSales'), align: 'right', cell: (r) => <Taka value={r.net_amount} /> },
    { header: t('reports.columns.cogs'), align: 'right', cell: (r) => <Taka value={r.cogs} /> },
    { header: t('reports.columns.grossProfit'), align: 'right', cell: (r) => <Taka value={r.gross_profit} /> },
  ]
  return (
    <Card className="overflow-hidden">
      <DataTable columns={columns} data={query.data} loading={query.isPending} />
    </Card>
  )
}

// --- Page ------------------------------------------------------------------------------------------
export function ReportsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const accounts = useSalesPaymentAccounts()

  const now = new Date()
  const [from, setFrom] = useState(isoDate(new Date(now.getFullYear(), now.getMonth(), 1)))
  const [to, setTo] = useState(isoDate(now))
  const [asOf, setAsOf] = useState(isoDate(now))
  const [accountId, setAccountId] = useState('')
  const [by, setBy] = useState<MarginBy>('product')
  const [group, setGroup] = useState<SalesGroup>('day')
  const [days, setDays] = useState('30')
  const [tab, setTab] = useState<ReportTab>('pl')

  const visible = TAB_ORDER.filter((item) => can(TAB_PERMISSION[item]))
  const current = visible.includes(tab) ? tab : visible[0]
  const showsRange = current !== undefined && DATE_RANGE_TABS.includes(current)
  const dayCount = Math.min(365, Math.max(1, Number(days) || 30))

  async function download() {
    if (!current) return
    const name = `${current}-${to}.xlsx`
    try {
      let blob: Blob
      switch (current) {
        case 'pl':
          blob = (await unwrap(api.GET('/api/v1/reports/profit-loss', { params: { query: { date_from: from, date_to: to, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'cashbook':
          blob = (await unwrap(api.GET('/api/v1/reports/cashbook', { params: { query: { date_from: from, date_to: to, payment_account_id: accountId ? Number(accountId) : undefined, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'margin':
          blob = (await unwrap(api.GET('/api/v1/reports/margin', { params: { query: { date_from: from, date_to: to, by, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'valuation':
          blob = (await unwrap(api.GET('/api/v1/reports/stock-valuation', { params: { query: { format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'expiry':
          blob = (await unwrap(api.GET('/api/v1/reports/near-expiry', { params: { query: { days: dayCount, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'claims':
          blob = (await unwrap(api.GET('/api/v1/reports/claims-aging', { params: { query: { as_of: asOf, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'po':
          blob = (await unwrap(api.GET('/api/v1/reports/po-status', { params: { query: { format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
        case 'sales':
          blob = (await unwrap(api.GET('/api/v1/reports/sales-summary', { params: { query: { date_from: from, date_to: to, group, format: 'xlsx' } }, parseAs: 'blob' }))) as unknown as Blob
          break
      }
      saveBlob(blob, name)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <PageHeader
        title={t('reports.title')}
        description={t('reports.subtitle')}
        actions={
          current ? (
            <Button variant="outline" onClick={() => void download()}>
              <Download className="size-4" aria-hidden />
              {t('reports.export')}
            </Button>
          ) : undefined
        }
      />

      {!current ? (
        <Card className="px-6 py-12 text-center text-sm text-muted">{t('reports.noAccess')}</Card>
      ) : (
        <>
          <div className="mb-4">
            <Tabs<ReportTab>
              value={current}
              onChange={setTab}
              items={visible.map((item) => ({ value: item, label: t(`reports.tabs.${item}`) }))}
            />
          </div>

          <Card className="mb-4 p-4">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {showsRange && (
                <>
                  <Field label={t('reports.from')} htmlFor="r-from">
                    <Input id="r-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
                  </Field>
                  <Field label={t('reports.to')} htmlFor="r-to">
                    <Input id="r-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
                  </Field>
                </>
              )}
              {current === 'cashbook' && (
                <Field label={t('reports.account')} htmlFor="r-account">
                  <Select id="r-account" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                    <option value="">{t('reports.allAccounts')}</option>
                    {accounts.data?.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              {current === 'margin' && (
                <Field label={t('reports.groupBy')} htmlFor="r-by">
                  <Select id="r-by" value={by} onChange={(e) => setBy(e.target.value as MarginBy)}>
                    <option value="product">{t('reports.by.product')}</option>
                    <option value="customer">{t('reports.by.customer')}</option>
                    <option value="category">{t('reports.by.category')}</option>
                    <option value="brand">{t('reports.by.brand')}</option>
                  </Select>
                </Field>
              )}
              {current === 'sales' && (
                <Field label={t('reports.groupBy')} htmlFor="r-group">
                  <Select id="r-group" value={group} onChange={(e) => setGroup(e.target.value as SalesGroup)}>
                    <option value="day">{t('reports.group.day')}</option>
                    <option value="month">{t('reports.group.month')}</option>
                  </Select>
                </Field>
              )}
              {current === 'expiry' && (
                <Field label={t('reports.withinDays')} htmlFor="r-days">
                  <Input id="r-days" inputMode="numeric" className="num" value={days} onChange={(e) => setDays(e.target.value)} />
                </Field>
              )}
              {current === 'claims' && (
                <Field label={t('reports.asOf')} htmlFor="r-asof">
                  <Input id="r-asof" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                </Field>
              )}
            </div>
          </Card>

          {current === 'pl' && <ProfitLossTab from={from} to={to} />}
          {current === 'cashbook' && <CashbookTab from={from} to={to} accountId={accountId} />}
          {current === 'margin' && <MarginTab from={from} to={to} by={by} />}
          {current === 'valuation' && <ValuationTab />}
          {current === 'expiry' && <NearExpiryTab days={dayCount} />}
          {current === 'claims' && <ClaimsAgingTab asOf={asOf} />}
          {current === 'po' && <PoStatusTab />}
          {current === 'sales' && <SalesSummaryTab from={from} to={to} group={group} />}

          {current === 'pl' && <p className="mt-4 text-xs text-muted">{t('reports.plNote')}</p>}
        </>
      )}
    </>
  )
}
