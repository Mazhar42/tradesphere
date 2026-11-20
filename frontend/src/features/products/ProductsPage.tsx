import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
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
import { Select } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { catalogKeys, useBrands, useCategories } from '@/features/catalog/queries'
import { useLang } from '@/i18n'
import { formatMoney, formatNumber } from '@/lib/format'

import { ProductFormDialog } from './ProductFormDialog'

type Product = Schemas['ProductRead']
const PAGE_SIZE = 25

export function ProductsPage() {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const { can } = useAuth()
  const canWrite = can('catalog:write')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [categoryId, setCategoryId] = useState('')
  const [brandId, setBrandId] = useState('')
  const [status, setStatus] = useState<'' | 'true' | 'false'>('true')
  const [editing, setEditing] = useState<Product | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const categories = useCategories()
  const brands = useBrands()

  const query = useQuery({
    queryKey: [...catalogKeys.products, { q, page, categoryId, brandId, status }],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/products', {
          params: {
            query: {
              q: q || undefined,
              page,
              page_size: PAGE_SIZE,
              category_id: categoryId ? Number(categoryId) : undefined,
              brand_id: brandId ? Number(brandId) : undefined,
              is_active: status === '' ? undefined : status === 'true',
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const openDialog = (product: Product | null) => {
    setEditing(product)
    setDialogOpen(true)
  }

  const columns = useMemo<Column<Product>[]>(
    () => [
      {
        header: t('products.name'),
        cell: (p) => (
          <div className="min-w-48">
            <div className="font-medium">{pick(p.name_en, p.name_bn)}</div>
            <div className="text-xs text-muted">
              {p.sku}
              {p.brand_name && ` · ${p.brand_name}`}
            </div>
          </div>
        ),
      },
      {
        header: t('products.category'),
        cell: (row) => row.category_name ?? <span className="text-muted">—</span>,
      },
      {
        header: t('products.units'),
        cell: (p) => (
          <div className="flex flex-wrap gap-1">
            <Badge>{p.base_uom_code}</Badge>
            {p.uoms.map((u) => (
              <Badge key={u.id} tone="primary">
                1 {u.uom_code} = {formatNumber(u.factor_to_base, lang)} {p.base_uom_code}
              </Badge>
            ))}
          </div>
        ),
      },
      {
        header: t('products.dp'),
        align: 'right',
        cell: (row) => formatMoney(row.dp, lang),
      },
      {
        header: t('products.tp'),
        align: 'right',
        cell: (row) => formatMoney(row.tp, lang),
      },
      {
        header: t('products.mrp'),
        align: 'right',
        cell: (row) => formatMoney(row.mrp, lang),
      },
      {
        header: t('common.status'),
        cell: (row) =>
          row.is_active ? (
            <Badge tone="success">{t('common.active')}</Badge>
          ) : (
            <Badge>{t('common.inactive')}</Badge>
          ),
      },
    ],
    [t, lang, pick],
  )

  return (
    <>
      <PageHeader
        title={t('products.title')}
        description={t('products.subtitle')}
        actions={
          canWrite && (
            <Button onClick={() => openDialog(null)}>
              <Plus className="size-4" /> {t('products.new')}
            </Button>
          )
        }
      />
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <Card>
          <Toolbar>
            <SearchInput
              value={q}
              onChange={(v) => {
                setQ(v)
                setPage(1)
              }}
            />
            <Select
              className="w-auto"
              value={categoryId}
              aria-label={t('products.category')}
              onChange={(e) => {
                setCategoryId(e.target.value)
                setPage(1)
              }}
            >
              <option value="">{t('products.allCategories')}</option>
              {categories.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {pick(c.name_en, c.name_bn)}
                </option>
              ))}
            </Select>
            <Select
              className="w-auto"
              value={brandId}
              aria-label={t('products.brand')}
              onChange={(e) => {
                setBrandId(e.target.value)
                setPage(1)
              }}
            >
              <option value="">{t('products.allBrands')}</option>
              {brands.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {pick(b.name_en, b.name_bn)}
                </option>
              ))}
            </Select>
            <Select
              className="w-auto"
              value={status}
              aria-label={t('common.status')}
              onChange={(e) => {
                setStatus(e.target.value as '' | 'true' | 'false')
                setPage(1)
              }}
            >
              <option value="true">{t('common.active')}</option>
              <option value="false">{t('common.inactive')}</option>
              <option value="">{t('common.all')}</option>
            </Select>
          </Toolbar>
          <DataTable
            columns={columns}
            data={query.data?.items}
            loading={query.isPending}
            onRowClick={canWrite ? openDialog : undefined}
            emptyAction={
              canWrite && (
                <Button variant="outline" onClick={() => openDialog(null)}>
                  <Plus className="size-4" /> {t('products.new')}
                </Button>
              )
            }
          />
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={query.data?.total ?? 0}
            onPageChange={setPage}
          />
        </Card>
      )}
      <ProductFormDialog open={dialogOpen} onOpenChange={setDialogOpen} product={editing} />
    </>
  )
}
