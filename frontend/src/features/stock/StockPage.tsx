import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Checkbox, Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { useCategories } from '@/features/catalog/queries'
import { useLang } from '@/i18n'
import { formatDate, formatMoney, formatNumber, type Lang } from '@/lib/format'
import { formatQty, type UnitFactor } from '@/lib/units'

type Row = Schemas['StockRow']
type BatchRow = Schemas['BatchStockRow']
const PAGE_SIZE = 25

const unitsOf = (uoms: Schemas['UnitInfo'][]): UnitFactor[] => uoms.map((u) => ({ code: u.uom_code, factor: u.factor_to_base }))
const qty = (base: string, uoms: Schemas['UnitInfo'][], baseCode: string, lang: Lang) =>
  Number(base) === 0 ? <span className="text-muted">0</span> : formatQty(base, unitsOf(uoms), baseCode, lang)

function MovementsDialog({ product, onClose }: { product: Row | null; onClose: () => void }) {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const query = useInfiniteQuery({
    queryKey: ['stock', 'movements', product?.product_id],
    enabled: product !== null,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/v1/inventory/products/{product_id}/movements', {
          params: { path: { product_id: product!.product_id }, query: { after: pageParam, limit: 30 } },
        }),
      ),
    getNextPageParam: (last) => last.next_after ?? undefined,
  })
  const items = query.data?.pages.flatMap((p) => p.items) ?? []
  const showCost = items.some((m) => m.value !== null)

  return (
    <Dialog
      open={product !== null}
      onOpenChange={(o) => !o && onClose()}
      title={product ? pick(product.name_en, product.name_bn) : ''}
      description={product ? `${product.sku} · ${t('stock.ledger')}` : undefined}
      size="xl"
      footer={<Button variant="outline" onClick={onClose}>{t('common.close')}</Button>}
    >
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted uppercase">
            <tr>
              <th className="px-3 py-2 text-left">{t('stock.date')}</th>
              <th className="px-3 py-2 text-left">{t('stock.document')}</th>
              <th className="px-3 py-2 text-left">{t('stock.type')}</th>
              <th className="px-3 py-2 text-left">{t('stock.batch')}</th>
              <th className="px-3 py-2 text-left">{t('stock.location')}</th>
              <th className="px-3 py-2 text-right">{t('stock.qty')}</th>
              {showCost && <th className="px-3 py-2 text-right">{t('stock.value')}</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {items.map((m) => (
              <tr key={m.id}>
                <td className="px-3 py-2 whitespace-nowrap">{formatDate(m.business_date, lang)}</td>
                <td className="px-3 py-2">{m.source_doc_no ?? `${m.source_doc_type}#${m.source_doc_id}`}</td>
                <td className="px-3 py-2">
                  <Badge tone={m.reversal_of_id ? 'danger' : Number(m.qty_base) > 0 ? 'success' : 'warning'}>
                    {t(`stock.types.${m.movement_type}`, m.movement_type)}
                  </Badge>
                </td>
                <td className="px-3 py-2">{m.batch_no ?? '—'}</td>
                <td className="px-3 py-2">{t(`stock.locations.${m.location_code}`, m.location_code)}</td>
                <td className={`num px-3 py-2 text-right font-medium ${Number(m.qty_base) < 0 ? 'text-danger' : ''}`}>
                  {Number(m.qty_base) > 0 ? '+' : ''}
                  {formatNumber(m.qty_base, lang)}
                </td>
                {showCost && <td className="num px-3 py-2 text-right">{formatMoney(m.value, lang)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {!query.isPending && items.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">{t('stock.noMovements')}</p>}
      </div>
      {query.hasNextPage && (
        <div className="mt-3 text-center">
          <Button variant="outline" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {t('stock.loadMore')}
          </Button>
        </div>
      )}
    </Dialog>
  )
}

export function StockPage() {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const showCost = can('reports:profit')
  const categories = useCategories()
  const [tab, setTab] = useState<'product' | 'batch'>('product')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [lowOnly, setLowOnly] = useState(false)
  const [inStock, setInStock] = useState(true)
  const [categoryId, setCategoryId] = useState('')
  const [expiring, setExpiring] = useState('')
  const [detail, setDetail] = useState<Row | null>(null)

  const totals = useQuery({ queryKey: ['stock', 'totals'], queryFn: () => unwrap(api.GET('/api/v1/inventory/stock/totals')) })

  const byProduct = useQuery({
    queryKey: ['stock', 'product', { q, page, lowOnly, inStock, categoryId }],
    enabled: tab === 'product',
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/inventory/stock', {
          params: { query: { q: q || undefined, page, page_size: PAGE_SIZE, low_only: lowOnly, in_stock_only: inStock, category_id: categoryId ? Number(categoryId) : undefined } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
  const byBatch = useQuery({
    queryKey: ['stock', 'batch', { q, page, expiring }],
    enabled: tab === 'batch',
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/inventory/stock/batches', {
          params: { query: { q: q || undefined, page, page_size: PAGE_SIZE, expiring_within_days: expiring ? Number(expiring) : undefined } },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const productColumns = useMemo<Column<Row>[]>(
    () => [
      {
        header: t('purchases.product'),
        cell: (r) => (
          <div className="min-w-44">
            <div className="flex items-center gap-2 font-medium">
              {pick(r.name_en, r.name_bn)}
              {r.is_low && <Badge tone="warning">{t('stock.low')}</Badge>}
            </div>
            <div className="text-xs text-muted">{r.sku}</div>
          </div>
        ),
      },
      { header: t('stock.locations.SELLABLE'), align: 'right', cell: (r) => qty(r.qty_sellable, r.uoms, r.base_uom_code, lang) },
      { header: t('stock.locations.DAMAGED'), align: 'right', cell: (r) => qty(r.qty_damaged, r.uoms, r.base_uom_code, lang) },
      { header: t('stock.locations.EXPIRED'), align: 'right', cell: (r) => qty(r.qty_expired, r.uoms, r.base_uom_code, lang) },
      { header: t('stock.total'), align: 'right', cell: (r) => <span className="font-medium">{qty(r.qty_total, r.uoms, r.base_uom_code, lang)}</span> },
      ...(showCost
        ? ([
            { header: t('stock.avgCost'), align: 'right', cell: (r) => formatMoney(r.avg_cost, lang) },
            { header: t('stock.value'), align: 'right', cell: (r) => formatMoney(r.value, lang) },
          ] as Column<Row>[])
        : []),
    ],
    [t, lang, pick, showCost],
  )

  const batchColumns = useMemo<Column<BatchRow>[]>(
    () => [
      { header: t('purchases.product'), cell: (r) => <div><div className="font-medium">{pick(r.name_en, r.name_bn)}</div><div className="text-xs text-muted">{r.sku}</div></div> },
      { header: t('stock.batch'), cell: (r) => r.batch_no ?? '—' },
      {
        header: t('stock.expiry'),
        cell: (r) =>
          r.expiry_date ? (
            <div className="flex items-center gap-2">
              {formatDate(r.expiry_date, lang)}
              <Badge tone={(r.days_to_expiry ?? 0) < 0 ? 'danger' : (r.days_to_expiry ?? 999) <= 30 ? 'warning' : 'neutral'}>
                {(r.days_to_expiry ?? 0) < 0 ? t('stock.expired') : t('stock.daysLeft', { days: formatNumber(r.days_to_expiry ?? 0, lang) })}
              </Badge>
            </div>
          ) : (
            '—'
          ),
      },
      { header: t('stock.location'), cell: (r) => t(`stock.locations.${r.location_code}`, r.location_code) },
      { header: t('stock.qty'), align: 'right', cell: (r) => qty(r.qty_base, r.uoms, r.base_uom_code, lang) },
    ],
    [t, lang, pick],
  )

  const current = tab === 'product' ? byProduct : byBatch

  return (
    <>
      <PageHeader
        title={t('stock.title')}
        description={t('stock.subtitle')}
        actions={
          can('inventory:adjust') && (
            <Button variant="outline" onClick={() => void navigate({ to: '/stock/documents' })}>
              {t('stockDocs.title')}
            </Button>
          )
        }
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {showCost && (
          <Card className="p-4">
            <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('dashboard.stockValue')}</p>
            <p className="num mt-1.5 text-xl font-semibold">{formatMoney(totals.data?.total_value, lang)}</p>
          </Card>
        )}
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('stock.productsInStock')}</p>
          <p className="num mt-1.5 text-xl font-semibold">{formatNumber(totals.data?.products_in_stock ?? 0, lang)}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('stock.lowStock')}</p>
          <p className="num mt-1.5 text-xl font-semibold text-warning">{formatNumber(totals.data?.low_stock_count ?? 0, lang)}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">{t('stock.expiringSoon')}</p>
          <p className="num mt-1.5 text-xl font-semibold text-warning">{formatNumber(totals.data?.expiring_soon_count ?? 0, lang)}</p>
          <p className="mt-1 text-xs text-muted">{t('stock.withinDays', { days: formatNumber(totals.data?.near_expiry_days ?? 30, lang) })}</p>
        </Card>
      </div>

      <Card>
        <Toolbar>
          <Tabs value={tab} onChange={(v) => { setTab(v); setPage(1) }} items={[{ value: 'product', label: t('stock.byProduct') }, { value: 'batch', label: t('stock.byBatch') }]} />
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1) }} />
          {tab === 'product' ? (
            <>
              <Select className="w-auto" aria-label={t('products.category')} value={categoryId} onChange={(e) => { setCategoryId(e.target.value); setPage(1) }}>
                <option value="">{t('products.allCategories')}</option>
                {categories.data?.map((c) => <option key={c.id} value={c.id}>{pick(c.name_en, c.name_bn)}</option>)}
              </Select>
              <Checkbox label={t('stock.inStockOnly')} checked={inStock} onChange={(e) => { setInStock(e.target.checked); setPage(1) }} />
              <Checkbox label={t('stock.lowOnly')} checked={lowOnly} onChange={(e) => { setLowOnly(e.target.checked); setPage(1) }} />
            </>
          ) : (
            <Select className="w-auto" aria-label={t('stock.expiry')} value={expiring} onChange={(e) => { setExpiring(e.target.value); setPage(1) }}>
              <option value="">{t('stock.anyExpiry')}</option>
              {[30, 60, 90, 180].map((d) => <option key={d} value={d}>{t('stock.expiringWithin', { days: formatNumber(d, lang) })}</option>)}
            </Select>
          )}
        </Toolbar>
        {current.isError ? (
          <div className="p-4"><ErrorState error={current.error} onRetry={() => void current.refetch()} /></div>
        ) : tab === 'product' ? (
          <DataTable columns={productColumns} data={byProduct.data?.items} loading={byProduct.isPending} onRowClick={setDetail} />
        ) : (
          <DataTable columns={batchColumns} data={byBatch.data?.items} loading={byBatch.isPending} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={current.data?.total ?? 0} onPageChange={setPage} />
      </Card>
      <MovementsDialog product={detail} onClose={() => setDetail(null)} />
    </>
  )
}
