import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { ProductPicker } from '@/components/pickers/pickers'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input, Select, Textarea } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { lineValue, pricePerUnit, sumMoney } from '@/lib/decimal'
import { blankToNull, QTY_PATTERN } from '@/lib/forms'

import { purchaseKeys, useActiveSuppliers } from './hooks'
import { Taka } from './shared'

type Product = Schemas['ProductRead']

interface Line {
  key: number
  product: Product | null
  uomId: string // '' = base unit
  qty: string
  freeQty: string
  unitPrice: string
}

let lineKey = 0
const emptyLine = (): Line => ({ key: ++lineKey, product: null, uomId: '', qty: '', freeQty: '0', unitPrice: '' })

const priceRe = /^\d{1,14}(\.\d{1,4})?$/

function unitOptions(product: Product) {
  return [
    { id: '', code: product.base_uom_code, factor: '1' },
    ...product.uoms.map((u) => ({ id: String(u.uom_id), code: u.uom_code, factor: u.factor_to_base })),
  ]
}

export function PoFormPage() {
  const { t } = useTranslation()
  const { pick } = useLang()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { poId } = useParams({ strict: false }) as { poId?: string }
  const editingId = poId ? Number(poId) : null
  const suppliers = useActiveSuppliers()

  const [supplierId, setSupplierId] = useState('')
  const [date, setDate] = useState('')
  const [expected, setExpected] = useState('')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<Line[]>(() => [emptyLine()])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [ready, setReady] = useState(editingId === null)

  const existing = useQuery({
    queryKey: ['purchase-order', editingId],
    queryFn: async () => {
      const po = await unwrap(
        api.GET('/api/v1/purchase-orders/{po_id}', { params: { path: { po_id: editingId! } } }),
      )
      const products = await Promise.all(
        po.items.map((i) =>
          unwrap(api.GET('/api/v1/products/{product_id}', { params: { path: { product_id: i.product_id } } })),
        ),
      )
      return { po, products }
    },
    enabled: editingId !== null,
  })

  // Load the existing PO into the form once its data arrives (compared during render, not in an effect).
  const [hydratedFrom, setHydratedFrom] = useState<typeof existing.data>(undefined)
  if (existing.data && existing.data !== hydratedFrom) {
    const { po, products } = existing.data
    setHydratedFrom(existing.data)
    setSupplierId(String(po.supplier_id))
    setDate(po.business_date)
    setExpected(po.expected_date ?? '')
    setNotes(po.notes ?? '')
    setLines(
      po.items.map((item, index) => ({
        key: ++lineKey,
        product: products[index] ?? null,
        uomId: item.uom_id === products[index]?.base_uom_id ? '' : String(item.uom_id),
        qty: String(Number(item.ordered_qty_base) / Number(item.factor)),
        freeQty: String(Number(item.free_qty_base) / Number(item.factor)),
        unitPrice: String(Number(item.unit_price)),
      })),
    )
    setReady(true)
  }

  const update = (key: number, patch: Partial<Line>) =>
    setLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  const chooseProduct = (key: number, product: Product | null) => {
    if (!product) return update(key, { product: null, uomId: '', unitPrice: '' })
    const preferred = product.uoms.find((u) => u.is_purchase_default)
    const factor = preferred?.factor_to_base ?? '1'
    update(key, {
      product,
      uomId: preferred ? String(preferred.uom_id) : '',
      unitPrice: Number(product.dp) > 0 ? String(Number(pricePerUnit(product.dp, factor))) : '',
    })
  }

  const changeUnit = (line: Line, uomId: string) => {
    if (!line.product) return update(line.key, { uomId })
    const factor = unitOptions(line.product).find((u) => u.id === uomId)?.factor ?? '1'
    update(line.key, {
      uomId,
      unitPrice: Number(line.product.dp) > 0 ? String(Number(pricePerUnit(line.product.dp, factor))) : line.unitPrice,
    })
  }

  const totals = useMemo(() => {
    const values = lines.map((l) =>
      QTY_PATTERN.test(l.qty) && priceRe.test(l.unitPrice) ? lineValue(l.qty, l.unitPrice) : '0.00',
    )
    return { values, total: sumMoney(values) }
  }, [lines])

  const validate = (): boolean => {
    const next: Record<string, string> = {}
    if (!supplierId) next.supplier = t('purchases.chooseSupplier')
    lines.forEach((l) => {
      if (!l.product) next[`p${l.key}`] = t('purchases.chooseProduct')
      if (!QTY_PATTERN.test(l.qty) || Number(l.qty) <= 0) next[`q${l.key}`] = t('purchases.enterQty')
      if (l.freeQty && !QTY_PATTERN.test(l.freeQty)) next[`f${l.key}`] = t('purchases.enterQty')
      if (!priceRe.test(l.unitPrice)) next[`u${l.key}`] = t('purchases.enterPrice')
    })
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const save = useMutation({
    mutationFn: async (confirm: boolean) => {
      const body = {
        supplier_id: Number(supplierId),
        business_date: date || null,
        expected_date: expected || null,
        notes: blankToNull(notes),
        items: lines.map((l) => ({
          product_id: l.product!.id,
          uom_id: l.uomId ? Number(l.uomId) : null,
          qty: l.qty,
          free_qty: l.freeQty || '0',
          unit_price: l.unitPrice,
        })),
      }
      let po = editingId
        ? await unwrap(
            api.PUT('/api/v1/purchase-orders/{po_id}', { params: { path: { po_id: editingId } }, body }),
          )
        : await unwrap(api.POST('/api/v1/purchase-orders', { body }))
      if (confirm) {
        po = await unwrap(
          api.POST('/api/v1/purchase-orders/{po_id}/confirm', { params: { path: { po_id: po.id } } }),
        )
      }
      return po
    },
    onSuccess: (po) => {
      void queryClient.invalidateQueries({ queryKey: purchaseKeys.orders })
      void queryClient.invalidateQueries({ queryKey: ['purchase-order', po.id] })
      toast.success(po.doc_no ? t('purchases.confirmedToast', { no: po.doc_no }) : t('common.saved'))
      void navigate({ to: '/purchases/$poId', params: { poId: String(po.id) } })
    },
    onError: (e) => toast.error(e.message),
  })

  const submit = (confirm: boolean) => {
    if (validate()) save.mutate(confirm)
  }

  if (existing.isError) return <ErrorState error={existing.error} />
  if (!ready) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />

  return (
    <>
      <PageHeader
        title={editingId ? t('purchases.editOrder') : t('purchases.newOrder')}
        description={t('purchases.formHint')}
      />
      <div className="grid gap-6">
        <Card>
          <CardHeader title={t('purchases.orderDetails')} />
          <div className="grid gap-4 p-5 sm:grid-cols-3">
            <Field label={t('purchases.supplier')} htmlFor="supplier" error={errors.supplier}>
              <Select id="supplier" value={supplierId} onChange={(e) => setSupplierId(e.target.value)} aria-invalid={!!errors.supplier}>
                <option value="">—</option>
                {suppliers.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('purchases.orderDate')} htmlFor="date" hint={t('purchases.defaultsToday')}>
              <Input id="date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label={t('purchases.expectedDate')} htmlFor="expected" optional optionalLabel={t('common.optional')}>
              <Input id="expected" type="date" value={expected} onChange={(e) => setExpected(e.target.value)} />
            </Field>
            <Field label={t('common.notes')} htmlFor="notes" optional optionalLabel={t('common.optional')} className="sm:col-span-3">
              <Textarea id="notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
          </div>
        </Card>

        <Card>
          <CardHeader
            title={t('purchases.items')}
            description={t('purchases.itemsHint')}
            actions={
              <Button variant="outline" size="sm" onClick={() => setLines((r) => [...r, emptyLine()])}>
                <Plus className="size-4" /> {t('purchases.addLine')}
              </Button>
            }
          />
          <div className="divide-y divide-border">
            {lines.map((line, index) => {
              const options = line.product ? unitOptions(line.product) : []
              return (
                <div key={line.key} className="grid gap-3 p-4 lg:grid-cols-[minmax(14rem,2fr)_8rem_7rem_7rem_8rem_8rem_auto] lg:items-start">
                  <Field label={t('purchases.product')} error={errors[`p${line.key}`]}>
                    <ProductPicker value={line.product} onChange={(p) => chooseProduct(line.key, p)} invalid={!!errors[`p${line.key}`]} />
                  </Field>
                  <Field label={t('purchases.unit')}>
                    <Select
                      value={line.uomId}
                      disabled={!line.product}
                      onChange={(e) => changeUnit(line, e.target.value)}
                    >
                      {options.length === 0 && <option value="">—</option>}
                      {options.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.code}
                          {Number(o.factor) > 1 ? ` (${Number(o.factor)})` : ''}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={t('purchases.qty')} error={errors[`q${line.key}`]}>
                    <Input inputMode="decimal" className="num" value={line.qty} aria-invalid={!!errors[`q${line.key}`]} onChange={(e) => update(line.key, { qty: e.target.value })} />
                  </Field>
                  <Field label={t('purchases.freeQty')} error={errors[`f${line.key}`]}>
                    <Input inputMode="decimal" className="num" value={line.freeQty} onChange={(e) => update(line.key, { freeQty: e.target.value })} />
                  </Field>
                  <Field label={t('purchases.unitPrice')} error={errors[`u${line.key}`]}>
                    <Input inputMode="decimal" className="num" value={line.unitPrice} aria-invalid={!!errors[`u${line.key}`]} onChange={(e) => update(line.key, { unitPrice: e.target.value })} />
                  </Field>
                  <div className="flex flex-col gap-1.5">
                    <span className="text-sm font-medium">{t('purchases.lineTotal')}</span>
                    <Taka value={totals.values[index]} className="num flex h-10 items-center justify-end font-medium" />
                  </div>
                  <div className="flex items-end lg:pt-7">
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
                  {line.product && (
                    <p className="text-xs text-muted lg:col-span-7">
                      {pick(line.product.name_en, line.product.name_bn)} · DP {Number(line.product.dp)} / {line.product.base_uom_code}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
          <div className="flex items-center justify-between border-t border-border px-5 py-4">
            <span className="text-sm text-muted">{t('purchases.totalPreview')}</span>
            <Taka value={totals.total} className="num text-xl font-semibold" />
          </div>
        </Card>

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" onClick={() => void navigate({ to: '/purchases' })}>
            {t('common.cancel')}
          </Button>
          <Button variant="secondary" loading={save.isPending && !save.variables} onClick={() => submit(false)}>
            {t('purchases.saveDraft')}
          </Button>
          <Button loading={save.isPending && !!save.variables} onClick={() => submit(true)}>
            {t('purchases.saveConfirm')}
          </Button>
        </div>
        <p className="text-right text-xs text-muted">{t('purchases.confirmHint')}</p>
      </div>
    </>
  )
}

