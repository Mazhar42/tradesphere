/** Companies' price list lines, and the queue of those not yet matched to one of our products.
 * A match is always made by a person. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { ProductPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { useAuth } from '@/features/auth/auth-context'

type Line = Schemas['SupplierProductRead']
type Product = Schemas['ProductRead']
const KEY = ['supplier-lines'] as const

function LinkLine({ line }: { line: Line }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [product, setProduct] = useState<Product | null>(null)
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/supplier-products/{supplier_product_id}/link', {
          params: { path: { supplier_product_id: line.id } },
          body: { product_id: product!.id },
        }),
      ),
    onSuccess: () => {
      toast.success(t('common.saved'))
      void queryClient.invalidateQueries({ queryKey: KEY })
    },
    onError: (e) => toast.error(e.message),
  })
  return (
    <div className="flex w-full gap-2 sm:w-96">
      <ProductPicker value={product} onChange={setProduct} className="flex-1" />
      <Button size="sm" disabled={!product} loading={save.isPending} onClick={() => save.mutate()}>
        {t('search.link')}
      </Button>
    </div>
  )
}

export function SupplierLinesPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [unlinked, setUnlinked] = useState(true)
  const lines = useQuery({
    queryKey: [...KEY, page, q, unlinked],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/supplier-products', {
          params: { query: { page, page_size: 25, q: q || undefined, unlinked } },
        }),
      ),
  })
  if (lines.isError) return <ErrorState error={lines.error} onRetry={() => void lines.refetch()} />

  return (
    <>
      <PageHeader title={t('search.linesTitle')} description={t('search.linesHint')} />
      <Card>
        <Toolbar>
          <SearchInput
            value={q}
            onChange={(value) => {
              setQ(value)
              setPage(1)
            }}
          />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={unlinked}
              onChange={(e) => {
                setUnlinked(e.target.checked)
                setPage(1)
              }}
            />
            {t('search.onlyUnlinked')}
          </label>
        </Toolbar>
        {lines.data?.items.length === 0 && <p className="p-4 text-sm text-muted">{t('search.noLines')}</p>}
        <ul className="divide-y divide-border">
          {lines.data?.items.map((line) => (
            <li key={line.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
              <span className="min-w-0">
                <span className="block font-medium">{line.description}</span>
                <span className="block text-xs text-muted">
                  {line.supplier_name} · {line.supplier_sku}
                  {line.price ? ` · ৳${Number(line.price)}` : ''} · {line.last_seen_at.slice(0, 10)}
                </span>
              </span>
              {line.product_id === null && can('catalog:write') ? (
                <LinkLine line={line} />
              ) : (
                line.product_id !== null && <span className="text-xs text-success">{t('search.linked')}</span>
              )}
            </li>
          ))}
        </ul>
        {lines.data && (
          <Pagination page={page} pageSize={25} total={lines.data.total} onPageChange={setPage} />
        )}
      </Card>
    </>
  )
}
