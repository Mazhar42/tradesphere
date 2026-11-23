import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { AlertTriangle, Banknote, Boxes, CheckCircle2, Circle, HandCoins, TrendingUp, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api, unwrap } from '@/api/client'
import { PageHeader } from '@/components/layout/page'
import { Card, CardHeader } from '@/components/ui/card'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { formatMoney, formatNumber } from '@/lib/format'

function Kpi({ label, icon: Icon, value, hint }: { label: string; icon: LucideIcon; value?: string | null; hint?: string }) {
  const { t } = useTranslation()
  const live = value !== undefined && value !== null
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-muted">{label}</p>
        <span className="rounded-lg bg-primary-soft p-2 text-primary">
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
      <p className={`num mt-3 text-2xl font-semibold ${live ? '' : 'text-muted/60'}`}>{live ? value : '৳ —'}</p>
      <p className="mt-1 text-xs text-muted">{live ? hint : t('dashboard.availableLater')}</p>
    </Card>
  )
}

/** Total count from a paginated list endpoint, or null when not permitted. */
function useTotal(
  key: string,
  enabled: boolean,
  fetcher: () => Promise<{ total: number }>,
): number | null | undefined {
  const query = useQuery({ queryKey: [key, 'total'], queryFn: fetcher, enabled, staleTime: 30_000 })
  if (!enabled) return null
  return query.data?.total
}

export function DashboardPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { user, can } = useAuth()

  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: () => unwrap(api.GET('/api/v1/settings')),
    enabled: can('settings:read'),
  })
  const products = useTotal('products', can('catalog:read'), () =>
    unwrap(api.GET('/api/v1/products', { params: { query: { page_size: 1 } } })),
  )
  const suppliers = useTotal('suppliers', can('suppliers:read'), () =>
    unwrap(api.GET('/api/v1/suppliers', { params: { query: { page_size: 1 } } })),
  )
  // The walk-in customer is created automatically, so subtract it.
  const customersRaw = useTotal('customers', can('customers:read'), () =>
    unwrap(api.GET('/api/v1/customers', { params: { query: { page_size: 1 } } })),
  )
  const customers = typeof customersRaw === 'number' ? Math.max(0, customersRaw - 1) : customersRaw
  const users = useTotal('users', can('users:read'), () =>
    unwrap(api.GET('/api/v1/users', { params: { query: { page_size: 1 } } })),
  )

  const stockTotals = useQuery({
    queryKey: ['stock', 'totals'],
    queryFn: () => unwrap(api.GET('/api/v1/inventory/stock/totals')),
    enabled: can('inventory:read'),
  })
  const stockValue = stockTotals.data?.total_value
  const cashAccounts = useQuery({
    queryKey: ['payment-accounts'],
    queryFn: () => unwrap(api.GET('/api/v1/payment-accounts')),
    enabled: can('purchasing:pay'),
  })
  const cashTotal = cashAccounts.data?.reduce((sum, a) => sum + Number(a.balance), 0)

  const profileDone = settings.data ? settings.data.business_profile.name !== 'My Dealership' : null
  const steps = [
    { to: '/settings', label: 'dashboard.stepProfile', hint: 'dashboard.stepProfileHint', done: profileDone, count: null },
    { to: '/products', label: 'dashboard.stepProducts', hint: 'dashboard.stepProductsHint', count: products },
    { to: '/suppliers', label: 'dashboard.stepSuppliers', hint: 'dashboard.stepSuppliersHint', count: suppliers },
    { to: '/customers', label: 'dashboard.stepCustomers', hint: 'dashboard.stepCustomersHint', count: customers },
    {
      to: '/users',
      label: 'dashboard.stepUsers',
      hint: 'dashboard.stepUsersHint',
      count: typeof users === 'number' ? users - 1 : users,
    },
  ].filter((s) => s.count !== null || s.done !== undefined)

  return (
    <>
      <PageHeader
        title={t('dashboard.greeting', { name: user?.full_name.split(' ')[0] ?? '' })}
        description={t('dashboard.subtitle')}
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t('dashboard.todaySales')} icon={Banknote} />
        <Kpi label={t('dashboard.grossProfit')} icon={TrendingUp} />
        <Kpi label={t('dashboard.customerDues')} icon={HandCoins} />
        <Kpi
          label={t('dashboard.stockValue')}
          icon={Boxes}
          value={stockValue === undefined || stockValue === null ? null : formatMoney(stockValue, lang)}
          hint={t('dashboard.stockValueHint')}
        />
      </div>

      {(cashTotal !== undefined || stockTotals.data) && (
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {cashTotal !== undefined && (
            <Card className="p-5">
              <p className="text-sm font-medium text-muted">{t('dashboard.cashAndBank')}</p>
              <p className="num mt-2 text-xl font-semibold">{formatMoney(cashTotal.toFixed(2), lang)}</p>
              <Link to="/accounts" className="mt-1 inline-block text-xs text-primary hover:underline">{t('dashboard.viewAccounts')}</Link>
            </Card>
          )}
          {stockTotals.data && (
            <>
              <Card className="p-5">
                <p className="flex items-center gap-1.5 text-sm font-medium text-muted"><AlertTriangle className="size-4 text-warning" aria-hidden />{t('stock.lowStock')}</p>
                <p className="num mt-2 text-xl font-semibold">{formatNumber(stockTotals.data.low_stock_count, lang)}</p>
                <Link to="/stock" className="mt-1 inline-block text-xs text-primary hover:underline">{t('dashboard.viewStock')}</Link>
              </Card>
              <Card className="p-5">
                <p className="flex items-center gap-1.5 text-sm font-medium text-muted"><AlertTriangle className="size-4 text-warning" aria-hidden />{t('stock.expiringSoon')}</p>
                <p className="num mt-2 text-xl font-semibold">{formatNumber(stockTotals.data.expiring_soon_count, lang)}</p>
                <p className="mt-1 text-xs text-muted">{t('stock.withinDays', { days: formatNumber(stockTotals.data.near_expiry_days, lang) })}</p>
              </Card>
            </>
          )}
        </div>
      )}

      <Card className="mt-6">
        <CardHeader title={t('dashboard.setupTitle')} description={t('dashboard.setupSubtitle')} />
        <ul className="divide-y divide-border">
          {steps.map((step) => {
            const done = step.done ?? (typeof step.count === 'number' && step.count > 0)
            return (
              <li key={step.to}>
                <Link to={step.to} className="flex items-center gap-4 px-5 py-4 hover:bg-surface-muted/60">
                  {done ? (
                    <CheckCircle2 className="size-5 shrink-0 text-success" aria-hidden />
                  ) : (
                    <Circle className="size-5 shrink-0 text-muted" aria-hidden />
                  )}
                  <div className="flex-1">
                    <p className="font-medium">{t(step.label)}</p>
                    <p className="text-sm text-muted">{t(step.hint)}</p>
                  </div>
                  {typeof step.count === 'number' && (
                    <span className="text-sm text-muted">
                      {t('dashboard.count', { formatted: formatNumber(step.count, lang) })}
                    </span>
                  )}
                </Link>
              </li>
            )
          })}
        </ul>
      </Card>
    </>
  )
}
