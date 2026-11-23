import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { ApiError, api, type Schemas, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { CustomerPicker, ProductPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select, Textarea } from '@/components/ui/input'
import { useActiveSuppliers } from '@/features/purchases/hooks'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney, lineValue, pricePerUnit, subMoney, sumMoney } from '@/lib/decimal'
import { blankToNull, MONEY_PATTERN, QTY_PATTERN } from '@/lib/forms'
import { formatMoney } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'

import { invalidateSales, salesKeys, useSalesPaymentAccounts } from './hooks'

type Product = Schemas['ProductRead']
type Customer = Schemas['CustomerRead']
type Invoice = Schemas['InvoiceRead']

const PRICE_PATTERN = /^\d{1,14}(\.\d{1,4})?$/

interface Line {
  key: number
  product: Product | null
  uomId: string // '' = base unit
  qty: string
  freeQty: string
  unitPrice: string // per entry unit
  dealer: string
  company: string
  companySupplierId: string
  freeCompany: boolean
}

let lineKey = 0
const emptyLine = (): Line => ({
  key: ++lineKey,
  product: null,
  uomId: '',
  qty: '',
  freeQty: '0',
  unitPrice: '',
  dealer: '0',
  company: '0',
  companySupplierId: '',
  freeCompany: false,
})

function unitOptions(product: Product) {
  return [
    { id: '', code: product.base_uom_code, factor: '1' },
    ...product.uoms.map((u) => ({ id: String(u.uom_id), code: u.uom_code, factor: u.factor_to_base })),
  ]
}

/** The trade price for the unit picked (TP is per base unit). Empty when no TP is set. */
function tradePrice(product: Product, uomId: string): string {
  const factor = unitOptions(product).find((u) => u.id === uomId)?.factor ?? '1'
  return Number(product.tp) > 0 ? pricePerUnit(product.tp, factor) : ''
}

function lineGross(line: Line): string {
  return QTY_PATTERN.test(line.qty) && PRICE_PATTERN.test(line.unitPrice)
    ? lineValue(line.qty, line.unitPrice)
    : '0.00'
}

function lineNet(line: Line): string {
  const dealer = MONEY_PATTERN.test(line.dealer) ? line.dealer : '0'
  const company = MONEY_PATTERN.test(line.company) ? line.company : '0'
  return subMoney(subMoney(lineGross(line), dealer), company)
}

export function InvoiceFormPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const { invoiceId } = useParams({ strict: false }) as { invoiceId?: string }
  const editingId = invoiceId ? Number(invoiceId) : null
  const accounts = useSalesPaymentAccounts()
  const suppliers = useActiveSuppliers()

  const [customer, setCustomer] = useState<Customer | null>(null)
  const [date, setDate] = useState('')
  const [notes, setNotes] = useState('')
  const [paid, setPaid] = useState('')
  const [accountId, setAccountId] = useState('')
  const [lines, setLines] = useState<Line[]>(() => [emptyLine()])
  const [savedId, setSavedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [serverErrors, setServerErrors] = useState<string[]>([])
  const [hydratedFrom, setHydratedFrom] = useState<Invoice | undefined>(undefined)
  const [ready, setReady] = useState(editingId === null)

  const existing = useQuery({
    queryKey: salesKeys.invoice(editingId ?? 0),
    enabled: editingId !== null,
    queryFn: async () => {
      const inv = await unwrap(
        api.GET('/api/v1/sales-invoices/{invoice_id}', { params: { path: { invoice_id: editingId! } } }),
      )
      const [cust, products] = await Promise.all([
        unwrap(api.GET('/api/v1/customers/{customer_id}', { params: { path: { customer_id: inv.customer_id } } })),
        Promise.all(
          inv.items.map((i) =>
            unwrap(api.GET('/api/v1/products/{product_id}', { params: { path: { product_id: i.product_id } } })),
          ),
        ),
      ])
      return { inv, cust, products }
    },
  })

  // Load an existing draft into the form once (compared during render, not in an effect).
  if (existing.data && existing.data.inv !== hydratedFrom) {
    const { inv, cust, products } = existing.data
    setHydratedFrom(inv)
    setCustomer(cust)
    setDate(inv.business_date)
    setNotes(inv.notes ?? '')
    setPaid(Number(inv.paid_amount) > 0 ? inv.paid_amount : '')
    setAccountId(inv.payment_account_id ? String(inv.payment_account_id) : '')
    setLines(
      inv.items.map((item, index) => {
        const product = products[index]!
        return {
          key: ++lineKey,
          product,
          uomId: item.uom_id === product.base_uom_id ? '' : String(item.uom_id),
          qty: String(Number(item.qty_entry)),
          freeQty: String(Number(item.free_qty_entry)),
          unitPrice: String(Number(item.unit_price)),
          dealer: item.dealer_discount,
          company: item.company_discount,
          companySupplierId: item.company_supplier_id ? String(item.company_supplier_id) : '',
          freeCompany: item.free_is_company_scheme,
        }
      }),
    )
    setReady(true)
  }

  const summary = useQuery({
    queryKey: ['customer-summary', customer?.id],
    enabled: customer !== null,
    queryFn: () =>
      unwrap(api.GET('/api/v1/customers/{customer_id}/summary', { params: { path: { customer_id: customer!.id } } })),
  })

  const defaultAccount = String((accounts.data ?? []).find((a) => a.kind === 'cash')?.id ?? '')
  const effectiveAccount = accountId || defaultAccount

  const totals = useMemo(() => {
    const gross = sumMoney(lines.map(lineGross))
    const dealer = sumMoney(lines.map((l) => (MONEY_PATTERN.test(l.dealer) ? l.dealer : '0')))
    const company = sumMoney(lines.map((l) => (MONEY_PATTERN.test(l.company) ? l.company : '0')))
    const net = subMoney(subMoney(gross, dealer), company)
    return { gross, dealer, company, net }
  }, [lines])
  const paidNow = MONEY_PATTERN.test(paid) ? paid : '0'
  const dueNow = subMoney(totals.net, paidNow)

  const update = (key: number, patch: Partial<Line>) =>
    setLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  const chooseProduct = (key: number, product: Product | null) => {
    if (!product) return update(key, { product: null, uomId: '', unitPrice: '' })
    const preferred = product.uoms.find((u) => u.is_sales_default)
    const uomId = preferred ? String(preferred.uom_id) : ''
    update(key, { product, uomId, unitPrice: tradePrice(product, uomId) })
  }

  const changeUnit = (line: Line, uomId: string) => {
    if (!line.product) return update(line.key, { uomId })
    update(line.key, { uomId, unitPrice: tradePrice(line.product, uomId) || line.unitPrice })
  }

  const buildBody = (): Schemas['InvoiceWrite'] => {
    if (!customer) throw new Error(t('sales.chooseCustomer'))
    const items = lines.filter((l) => l.product || l.qty)
    if (items.length === 0) throw new Error(t('sales.addItem'))
    for (const l of items) {
      if (!l.product) throw new Error(t('sales.chooseProduct'))
      if (!QTY_PATTERN.test(l.qty) || Number(l.qty) <= 0) throw new Error(t('sales.enterQty'))
    }
    const usesCash = cmpMoney(paidNow, '0') > 0
    if (usesCash && !effectiveAccount) throw new Error(t('sales.chooseAccount'))
    return {
      customer_id: customer.id,
      business_date: date || null,
      notes: blankToNull(notes),
      paid_amount: paidNow,
      payment_account_id: usesCash ? Number(effectiveAccount) : null,
      items: items.map((l) => ({
        product_id: l.product!.id,
        uom_id: l.uomId ? Number(l.uomId) : null,
        qty: l.qty,
        free_qty: QTY_PATTERN.test(l.freeQty) ? l.freeQty : '0',
        unit_price: PRICE_PATTERN.test(l.unitPrice) ? l.unitPrice : null,
        dealer_discount: MONEY_PATTERN.test(l.dealer) ? l.dealer : '0',
        company_discount: MONEY_PATTERN.test(l.company) ? l.company : '0',
        company_supplier_id: l.companySupplierId ? Number(l.companySupplierId) : null,
        free_is_company_scheme: l.freeCompany,
      })),
    }
  }

  const save = useMutation({
    mutationFn: async (post: boolean) => {
      const payload = buildBody()
      // A draft saved by an earlier attempt is updated, never duplicated.
      const draftId = editingId ?? savedId
      const invoice =
        draftId === null
          ? await unwrap(api.POST('/api/v1/sales-invoices', { body: payload }))
          : await unwrap(
              api.PATCH('/api/v1/sales-invoices/{invoice_id}', {
                params: { path: { invoice_id: draftId } },
                body: payload,
              }),
            )
      setSavedId(invoice.id)
      if (!post) return invoice
      return unwrap(
        api.POST('/api/v1/sales-invoices/{invoice_id}/post', {
          params: { path: { invoice_id: invoice.id }, header: { 'Idempotency-Key': idem.current() } },
        }),
      )
    },
    onSuccess: (invoice, post) => {
      idem.settle()
      setError(null)
      setServerErrors([])
      toast.success(post ? t('sales.posted', { no: invoice.doc_no }) : t('sales.draftSaved'))
      invalidateSales(queryClient, invoice.id)
      void navigate({ to: '/sales/$invoiceId', params: { invoiceId: String(invoice.id) } })
    },
    onError: (e, post) => {
      if (post) idem.settle(e)
      setError(e.message)
      setServerErrors(e instanceof ApiError ? e.fieldErrors.map((f) => f.message) : [])
    },
  })

  if (existing.isError) return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />
  if (!ready) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />
  if (existing.data && existing.data.inv.status !== 'draft') {
    return (
      <>
        <PageHeader title={t('sales.editInvoice')} />
        <Card className="p-6 text-sm text-muted">
          {t('sales.onlyDraftsEdit')}{' '}
          <button className="text-primary underline" onClick={() => void navigate({ to: '/sales' })}>
            {t('sales.backToSales')}
          </button>
        </Card>
      </>
    )
  }

  const credit = summary.data
  const canSave = !save.isPending

  return (
    <>
      <PageHeader
        title={editingId === null ? t('sales.newInvoice') : t('sales.editInvoice')}
        description={t('sales.formHint')}
        actions={
          <Button variant="outline" onClick={() => void navigate({ to: '/sales' })}>
            {t('common.cancel')}
          </Button>
        }
      />

      <Card className="mb-4 p-5">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label={t('sales.customer')} htmlFor="s-customer">
            <CustomerPicker id="s-customer" value={customer} onChange={setCustomer} />
          </Field>
          <Field label={t('sales.date')} htmlFor="s-date" hint={t('sales.defaultsToday')}>
            <Input id="s-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('sales.notes')} htmlFor="s-notes" optional optionalLabel={t('common.optional')}>
            <Input id="s-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
        {credit && (
          <p className="mt-3 text-sm text-muted">
            {t('sales.creditLine', { limit: formatMoney(credit.credit_limit, lang), balance: formatMoney(credit.balance, lang) })}
          </p>
        )}
      </Card>

      <Card className="mb-4">
        <CardHeader title={t('sales.items')} description={t('sales.itemsHint')} />
        <div className="space-y-3 p-4">
          {lines.map((line, index) => {
            const options = line.product ? unitOptions(line.product) : []
            const needsCompany = cmpMoney(MONEY_PATTERN.test(line.company) ? line.company : '0', '0') > 0 || line.freeCompany
            return (
              <div key={line.key} className="grid gap-2 rounded-lg border border-border p-3 md:grid-cols-12">
                <div className="md:col-span-4">
                  <Field label={`${t('sales.product')} ${index + 1}`}>
                    <ProductPicker value={line.product} onChange={(p) => chooseProduct(line.key, p)} />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('sales.unit')} htmlFor={`u-${line.key}`}>
                    <Select id={`u-${line.key}`} value={line.uomId} disabled={!line.product} onChange={(e) => changeUnit(line, e.target.value)}>
                      {options.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('sales.qty')} htmlFor={`q-${line.key}`}>
                    <Input id={`q-${line.key}`} inputMode="decimal" className="num" value={line.qty} onChange={(e) => update(line.key, { qty: e.target.value })} />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('sales.unitPrice')} htmlFor={`p-${line.key}`}>
                    <Input id={`p-${line.key}`} inputMode="decimal" className="num" value={line.unitPrice} onChange={(e) => update(line.key, { unitPrice: e.target.value })} />
                  </Field>
                </div>
                <div className="flex items-end gap-2 md:col-span-2">
                  <Field label={t('sales.freeQty')} htmlFor={`f-${line.key}`} className="flex-1">
                    <Input id={`f-${line.key}`} inputMode="decimal" className="num" value={line.freeQty} onChange={(e) => update(line.key, { freeQty: e.target.value })} />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('sales.dealerDiscount')} htmlFor={`d-${line.key}`}>
                    <Input id={`d-${line.key}`} inputMode="decimal" className="num" value={line.dealer} onChange={(e) => update(line.key, { dealer: e.target.value })} />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label={t('sales.companyDiscount')} htmlFor={`c-${line.key}`}>
                    <Input id={`c-${line.key}`} inputMode="decimal" className="num" value={line.company} onChange={(e) => update(line.key, { company: e.target.value })} />
                  </Field>
                </div>
                <div className="md:col-span-4">
                  <Field label={t('sales.fundedBy')} htmlFor={`s-${line.key}`}>
                    <Select id={`s-${line.key}`} value={line.companySupplierId} disabled={!needsCompany} onChange={(e) => update(line.key, { companySupplierId: e.target.value })}>
                      <option value="">—</option>
                      {suppliers.data?.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <div className="flex flex-col justify-end gap-2 md:col-span-3">
                  <Checkbox label={t('sales.freeCompany')} checked={line.freeCompany} onChange={(e) => update(line.key, { freeCompany: e.target.checked })} />
                </div>
                <div className="flex items-end justify-between gap-2 md:col-span-3">
                  <div className="text-sm">
                    <span className="text-muted">{t('sales.lineNet')} </span>
                    <Taka value={lineNet(line)} className="font-medium" />
                  </div>
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
            )
          })}
          <Button variant="outline" onClick={() => setLines((rows) => [...rows, emptyLine()])}>
            <Plus className="size-4" /> {t('sales.addItem')}
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <Field label={t('sales.paidNow')} htmlFor="s-paid" hint={t('sales.paidHint')}>
            <Input id="s-paid" inputMode="decimal" className="num" value={paid} onChange={(e) => setPaid(e.target.value)} />
          </Field>
          {cmpMoney(paidNow, '0') > 0 && (
            <div className="mt-4">
              <Field label={t('sales.paidInto')} htmlFor="s-account">
                <Select id="s-account" value={effectiveAccount} onChange={(e) => setAccountId(e.target.value)}>
                  {accounts.data?.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          )}
          <Field label={t('sales.notesLong')} htmlFor="s-notes-long" optional optionalLabel={t('common.optional')} className="mt-4">
            <Textarea id="s-notes-long" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </Card>
        <Card className="p-5">
          <dl className="space-y-2 text-sm">
            <Row label={t('sales.gross')} value={<Taka value={totals.gross} />} />
            <Row label={t('sales.dealerDiscount')} value={<Taka value={totals.dealer} />} />
            <Row label={t('sales.companyDiscount')} value={<Taka value={totals.company} />} hint={t('sales.companyDiscountHint')} />
            <Row label={t('sales.net')} value={<Taka value={totals.net} className="font-semibold" />} strong />
            <Row label={t('sales.dueAfterPayment')} value={<Taka value={dueNow} className={cmpMoney(dueNow, '0') > 0 ? 'text-danger' : ''} />} />
          </dl>
        </Card>
      </div>

      {(error || serverErrors.length > 0) && (
        <div role="alert" className="mt-4 rounded-lg bg-danger-soft px-4 py-3 text-sm text-danger">
          {error && <p className="font-medium">{error}</p>}
          {serverErrors.map((m, i) => (
            <p key={i}>{m}</p>
          ))}
        </div>
      )}

      <div className="mt-6 flex flex-wrap justify-end gap-2">
        <Button variant="outline" disabled={!canSave} onClick={() => save.mutate(false)}>
          {t('sales.saveDraft')}
        </Button>
        <Button loading={save.isPending} onClick={() => save.mutate(true)}>
          {t('sales.savePost')}
        </Button>
      </div>
    </>
  )
}

function Row({ label, value, hint, strong }: { label: string; value: ReactNode; hint?: string; strong?: boolean }) {
  return (
    <div className={strong ? 'flex items-center justify-between border-t border-border pt-2' : 'flex items-center justify-between'}>
      <div>
        <dt className="text-muted">{label}</dt>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      <dd className="num">{value}</dd>
    </div>
  )
}
