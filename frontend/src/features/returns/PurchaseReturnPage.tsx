import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { PageHeader } from '@/components/layout/page'
import { BatchSelect } from '@/components/pickers/BatchSelect'
import { ProductPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { useActiveSuppliers } from '@/features/purchases/hooks'
import { Taka } from '@/features/purchases/shared'
import { lineValue, sumMoney } from '@/lib/decimal'
import { QTY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

type Product = Schemas['ProductRead']
type Location = 'SELLABLE' | 'DAMAGED' | 'EXPIRED'
const LOCATIONS: Location[] = ['SELLABLE', 'DAMAGED', 'EXPIRED']
const PRICE_PATTERN = /^\d{1,14}(\.\d{1,4})?$/

interface Line {
  key: number
  product: Product | null
  batchId: string
  location: Location
  qty: string
  unitValue: string
  settlement: 'supplier_credit' | 'claim'
}

let lineKey = 0
const emptyLine = (): Line => ({
  key: ++lineKey,
  product: null,
  batchId: '',
  location: 'SELLABLE',
  qty: '',
  unitValue: '',
  settlement: 'supplier_credit',
})

/** Send goods back to the company. The agreed value is credited to the account or claimed. */
export function PurchaseReturnPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const suppliers = useActiveSuppliers()

  const [supplierId, setSupplierId] = useState('')
  const [lines, setLines] = useState<Line[]>(() => [emptyLine()])
  const [savedId, setSavedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const update = (key: number, patch: Partial<Line>) => setLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const total = sumMoney(
    lines.map((l) => (QTY_PATTERN.test(l.qty) && PRICE_PATTERN.test(l.unitValue) ? lineValue(l.qty, l.unitValue) : '0.00')),
  )

  const save = useMutation({
    mutationFn: async () => {
      if (!supplierId) throw new Error(t('purchases.chooseSupplier'))
      const items = lines.filter((l) => l.product || l.qty)
      if (items.length === 0) throw new Error(t('sales.addItem'))
      for (const l of items) {
        if (!l.product) throw new Error(t('sales.chooseProduct'))
        if (!QTY_PATTERN.test(l.qty) || Number(l.qty) <= 0) throw new Error(t('sales.enterQty'))
        if (!PRICE_PATTERN.test(l.unitValue)) throw new Error(t('returns.enterValue'))
        if (l.product.track_batch && !l.batchId) throw new Error(t('returns.chooseBatch'))
      }
      const body = {
        supplier_id: Number(supplierId),
        items: items.map((l) => ({
          product_id: l.product!.id,
          batch_id: l.batchId ? Number(l.batchId) : null,
          location: l.location,
          qty_base: l.qty,
          unit_value: l.unitValue,
          settlement: l.settlement,
        })),
      }
      const draftId = savedId ?? (await unwrap(api.POST('/api/v1/purchase-returns', { body }))).id
      setSavedId(draftId)
      return unwrap(
        api.POST('/api/v1/purchase-returns/{return_id}/post', {
          params: { path: { return_id: draftId }, header: { 'Idempotency-Key': idem.current() } },
        }),
      )
    },
    onSuccess: (ret) => {
      idem.settle()
      toast.success(t('returns.posted', { no: ret.doc_no }))
      for (const key of [['purchase-returns'], ['stock'], ['claimable-items'], ['supplier-summary'], ['supplier-ledger']]) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
      void navigate({ to: '/returns' })
    },
    onError: (e) => {
      idem.settle(e)
      setError(e.message)
    },
  })

  return (
    <>
      <PageHeader
        title={t('returns.newPurchaseReturn')}
        description={t('returns.purchaseHint')}
        actions={
          <Button variant="outline" onClick={() => void navigate({ to: '/returns' })}>
            {t('common.cancel')}
          </Button>
        }
      />
      <Card className="mb-4 p-5">
        <div className="max-w-sm">
          <Field label={t('claims.company')} htmlFor="pr-supplier">
            <Select id="pr-supplier" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">—</option>
              {suppliers.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Card>

      <Card className="mb-4">
        <CardHeader title={t('sales.items')} description={t('returns.purchaseItemsHint')} />
        <div className="space-y-3 p-4">
          {lines.map((line, index) => (
            <div key={line.key} className="grid gap-2 rounded-lg border border-border p-3 md:grid-cols-12">
              <div className="md:col-span-4">
                <Field label={`${t('sales.product')} ${index + 1}`}>
                  <ProductPicker
                    value={line.product}
                    onChange={(p) => update(line.key, { product: p, batchId: '', unitValue: p && Number(p.dp) > 0 ? String(Number(p.dp)) : '' })}
                  />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label={t('returns.batch')}>
                  <BatchSelect
                    productId={line.product?.id ?? null}
                    value={line.batchId}
                    disabled={!line.product?.track_batch}
                    onChange={(v) => update(line.key, { batchId: v })}
                  />
                </Field>
              </div>
              <div className="md:col-span-2">
                <Field label={t('stock.location')} htmlFor={`l-${line.key}`}>
                  <Select id={`l-${line.key}`} value={line.location} onChange={(e) => update(line.key, { location: e.target.value as Location })}>
                    {LOCATIONS.map((l) => (
                      <option key={l} value={l}>
                        {t(`stock.locations.${l}`)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label={`${t('sales.qty')}${line.product ? ` (${line.product.base_uom_code})` : ''}`} htmlFor={`q-${line.key}`}>
                  <Input id={`q-${line.key}`} inputMode="decimal" className="num" value={line.qty} onChange={(e) => update(line.key, { qty: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label={t('returns.unitValue')} htmlFor={`v-${line.key}`} hint={t('returns.unitValueHint')}>
                  <Input id={`v-${line.key}`} inputMode="decimal" className="num" value={line.unitValue} onChange={(e) => update(line.key, { unitValue: e.target.value })} />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label={t('returns.settlement')} htmlFor={`s-${line.key}`}>
                  <Select id={`s-${line.key}`} value={line.settlement} onChange={(e) => update(line.key, { settlement: e.target.value as Line['settlement'] })}>
                    <option value="supplier_credit">{t('returns.settlements.supplier_credit')}</option>
                    <option value="claim">{t('returns.settlements.claim')}</option>
                  </Select>
                </Field>
              </div>
              <div className="flex items-end justify-end md:col-span-6">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('common.remove')}
                  disabled={lines.length === 1}
                  onClick={() => setLines((rows) => rows.filter((r) => r.key !== line.key))}
                >
                  <Trash2 className="size-4 text-danger" />
                </Button>
              </div>
            </div>
          ))}
          <Button variant="outline" onClick={() => setLines((rows) => [...rows, emptyLine()])}>
            <Plus className="size-4" /> {t('sales.addItem')}
          </Button>
        </div>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">
          {t('returns.creditValue')}: <Taka value={total} className="font-semibold" />
        </p>
        <Button loading={save.isPending} onClick={() => save.mutate()}>
          {t('returns.postReturn')}
        </Button>
      </div>
      {error && (
        <div role="alert" className="mt-4 rounded-lg bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
    </>
  )
}
