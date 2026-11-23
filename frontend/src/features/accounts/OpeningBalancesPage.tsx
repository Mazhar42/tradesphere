import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { CustomerPicker, ProductPicker } from '@/components/pickers/pickers'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { Input, Select, Textarea } from '@/components/ui/input'
import { useActiveSuppliers, usePaymentAccounts } from '@/features/purchases/hooks'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { lineValue, sumMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'
import { blankToNull, MONEY_PATTERN, QTY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

type Product = Schemas['ProductRead']
type Customer = Schemas['CustomerRead']

let seq = 0
const id = () => ++seq

interface StockLine { k: number; product: Product | null; batch: string; mfg: string; expiry: string; location: 'SELLABLE' | 'DAMAGED' | 'EXPIRED'; qty: string; cost: string }
interface DueLine { k: number; customer: Customer | null; amount: string }
interface SupplierLine { k: number; supplierId: string; direction: 'advance' | 'payable'; amount: string }
interface ClaimLine { k: number; supplierId: string; amount: string }
interface CashLine { k: number; accountId: string; amount: string }

const yesterday = () => {
  const d = new Date(Date.now() - 86_400_000)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}
const costRe = /^\d{1,14}(\.\d{1,4})?$/

function Section({ title, hint, onAdd, addLabel, children }: { title: string; hint: string; onAdd: () => void; addLabel: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={hint} actions={<Button variant="outline" size="sm" onClick={onAdd}><Plus className="size-4" /> {addLabel}</Button>} />
      <div className="divide-y divide-border">{children}</div>
    </Card>
  )
}

function Remove({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation()
  return <Button variant="ghost" size="icon" aria-label={t('common.remove')} onClick={onClick}><Trash2 className="size-4 text-danger" /></Button>
}

function CurrentOpening({ onReversed }: { onReversed: () => void }) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const idem = useIdempotencyKey()
  const [confirm, setConfirm] = useState(false)
  const query = useQuery({ queryKey: ['opening-balances'], queryFn: () => unwrap(api.GET('/api/v1/opening-balances')) })
  const active = query.data?.find((o) => o.status === 'posted')
  if (query.isError) return <ErrorState error={query.error} />
  if (!active) return null
  const s = active.summary as Record<string, string | number>
  return (
    <Card className="mb-6 border-success/40">
      <CardHeader
        title={<span className="flex items-center gap-2">{t('opening.posted')} <Badge tone="success">{active.doc_no}</Badge></span>}
        description={`${t('opening.asOf')} ${formatDate(active.business_date, lang)}`}
        actions={<Button variant="danger" size="sm" onClick={() => setConfirm(true)}>{t('purchases.reverse')}</Button>}
      />
      <dl className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-5">
        {([['stock', 'stock_value'], ['customers', 'customer_dues'], ['supplierAdvance', 'supplier_advance'], ['claims', 'claims_receivable'], ['cash', 'cash']] as const).map(([label, key]) => (
          <div key={key}><dt className="text-xs text-muted uppercase">{t(`opening.sections.${label}`)}</dt><dd className="num font-semibold"><Taka value={String(s[key] ?? '0')} /></dd></div>
        ))}
      </dl>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t('opening.reverseTitle')}
        message={t('opening.reverseMessage')}
        confirmLabel={t('purchases.reverse')}
        danger
        onConfirm={async () => {
          try {
            await unwrap(api.POST('/api/v1/opening-balances/{opening_id}/reverse', { params: { path: { opening_id: active.id }, header: { 'Idempotency-Key': idem.current() } } }))
            idem.settle()
          } catch (e) { idem.settle(e); throw e }
          toast.success(t('purchases.reversed'))
          onReversed()
        }}
      />
    </Card>
  )
}

export function OpeningBalancesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const suppliers = useActiveSuppliers()
  const accounts = usePaymentAccounts()
  const idem = useIdempotencyKey()
  const list = useQuery({ queryKey: ['opening-balances'], queryFn: () => unwrap(api.GET('/api/v1/opening-balances')) })
  const hasActive = list.data?.some((o) => o.status === 'posted') ?? false

  const [date, setDate] = useState(yesterday())
  const [notes, setNotes] = useState('')
  const [stock, setStock] = useState<StockLine[]>([])
  const [dues, setDues] = useState<DueLine[]>([])
  const [balances, setBalances] = useState<SupplierLine[]>([])
  const [claims, setClaims] = useState<ClaimLine[]>([])
  const [cash, setCash] = useState<CashLine[]>([])
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const upd = <T extends { k: number }>(set: React.Dispatch<React.SetStateAction<T[]>>, k: number, patch: Partial<T>) => set((rows) => rows.map((r) => (r.k === k ? { ...r, ...patch } : r)))
  const del = <T extends { k: number }>(set: React.Dispatch<React.SetStateAction<T[]>>, k: number) => set((rows) => rows.filter((r) => r.k !== k))

  const totals = useMemo(() => ({
    stock: sumMoney(stock.map((l) => (QTY_PATTERN.test(l.qty) && costRe.test(l.cost) ? lineValue(l.qty, l.cost) : '0.00'))),
    dues: sumMoney(dues.map((l) => (MONEY_PATTERN.test(l.amount) ? l.amount : '0'))),
    cash: sumMoney(cash.map((l) => (MONEY_PATTERN.test(l.amount) ? l.amount : '0'))),
  }), [stock, dues, cash])

  const problem = (): string | null => {
    if (!date) return t('opening.errors.date')
    if (stock.length + dues.length + balances.length + claims.length + cash.length === 0) return t('opening.errors.empty')
    for (const l of stock) {
      if (!l.product) return t('opening.errors.product')
      if (!QTY_PATTERN.test(l.qty) || Number(l.qty) <= 0 || !costRe.test(l.cost)) return t('opening.errors.stockNumbers', { name: l.product.sku })
      if (l.product.track_batch && !l.batch.trim()) return t('receive.batchRequired', { name: l.product.sku })
      if (l.product.track_batch && l.product.track_expiry && !l.expiry) return t('receive.expiryRequired', { name: l.product.sku })
    }
    if (dues.some((l) => !l.customer || !MONEY_PATTERN.test(l.amount) || Number(l.amount) <= 0)) return t('opening.errors.dues')
    if (balances.some((l) => !l.supplierId || !MONEY_PATTERN.test(l.amount) || Number(l.amount) <= 0)) return t('opening.errors.suppliers')
    if (claims.some((l) => !l.supplierId || !MONEY_PATTERN.test(l.amount) || Number(l.amount) <= 0)) return t('opening.errors.suppliers')
    if (cash.some((l) => !l.accountId || !MONEY_PATTERN.test(l.amount) || Number(l.amount) <= 0)) return t('opening.errors.cash')
    return null
  }

  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/opening-balances', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            business_date: date,
            notes: blankToNull(notes),
            stock: stock.map((l) => ({ product_id: l.product!.id, batch_no: blankToNull(l.batch), mfg_date: l.mfg || null, expiry_date: l.expiry || null, location: l.location, qty_base: l.qty, unit_cost: l.cost })),
            customer_dues: dues.map((l) => ({ customer_id: l.customer!.id, amount: l.amount })),
            supplier_balances: balances.map((l) => ({ supplier_id: Number(l.supplierId), direction: l.direction, amount: l.amount })),
            claims_receivable: claims.map((l) => ({ supplier_id: Number(l.supplierId), amount: l.amount })),
            cash: cash.map((l) => ({ payment_account_id: Number(l.accountId), amount: l.amount })),
          },
        }),
      ),
    onSuccess: (r) => {
      idem.settle()
      toast.success(t('opening.postedToast', { no: r.doc_no }))
      for (const key of ['opening-balances', 'stock', 'payment-accounts', 'journal', 'trial-balance', 'supplier-summary']) void queryClient.invalidateQueries({ queryKey: [key] })
      setStock([]); setDues([]); setBalances([]); setClaims([]); setCash([]); setConfirm(false)
    },
    onError: (e) => { idem.settle(e); setError(e.message); setConfirm(false) },
  })

  const review = () => {
    const p = problem()
    setError(p)
    if (!p) setConfirm(true)
  }

  const supplierSelect = (value: string, onChange: (v: string) => void) => (
    <Select aria-label={t('purchases.supplier')} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t('purchases.supplier')}…</option>
      {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </Select>
  )
  const amountInput = (value: string, onChange: (v: string) => void) => (
    <Input aria-label={t('payment.amount')} placeholder={t('payment.amount')} inputMode="decimal" className="num" value={value} onChange={(e) => onChange(e.target.value)} />
  )

  return (
    <>
      <PageHeader title={t('opening.title')} description={t('opening.subtitle')} />
      <CurrentOpening onReversed={() => void queryClient.invalidateQueries()} />
      {hasActive ? (
        <p className="rounded-lg bg-warning-soft px-4 py-3 text-sm text-warning">{t('opening.alreadyPosted')}</p>
      ) : (
        <div className="grid gap-6">
          <Card>
            <CardHeader title={t('opening.when')} description={t('opening.whenHint')} />
            <div className="grid gap-4 p-5 sm:grid-cols-3">
              <Field label={t('opening.asOf')} htmlFor="o-date"><Input id="o-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
              <Field label={t('common.notes')} htmlFor="o-notes" optional optionalLabel={t('common.optional')} className="sm:col-span-2"><Textarea id="o-notes" rows={1} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
            </div>
          </Card>

          <Section title={t('opening.sections.stock')} hint={t('opening.stockHint')} addLabel={t('opening.add')} onAdd={() => setStock((r) => [...r, { k: id(), product: null, batch: '', mfg: '', expiry: '', location: 'SELLABLE', qty: '', cost: '' }])}>
            {stock.map((l) => (
              <div key={l.k} className="grid gap-3 p-4 lg:grid-cols-[minmax(14rem,2fr)_8rem_9rem_8rem_7rem_7rem_auto]">
                <ProductPicker value={l.product} onChange={(p) => upd(setStock, l.k, { product: p, cost: p && Number(p.dp) > 0 ? String(Number(p.dp)) : l.cost })} />
                <Input placeholder={t('receive.batchNo')} aria-label={t('receive.batchNo')} disabled={l.product ? !l.product.track_batch : false} value={l.batch} onChange={(e) => upd(setStock, l.k, { batch: e.target.value })} />
                <Input type="date" aria-label={t('receive.expiry')} disabled={l.product ? !l.product.track_expiry : false} value={l.expiry} onChange={(e) => upd(setStock, l.k, { expiry: e.target.value })} />
                <Select aria-label={t('stock.location')} value={l.location} onChange={(e) => upd(setStock, l.k, { location: e.target.value as StockLine['location'] })}>
                  {(['SELLABLE', 'DAMAGED', 'EXPIRED'] as const).map((c) => <option key={c} value={c}>{t(`stock.locations.${c}`)}</option>)}
                </Select>
                <Input placeholder={`${t('stock.qty')} (${l.product?.base_uom_code ?? '…'})`} aria-label={t('stock.qty')} inputMode="decimal" className="num" value={l.qty} onChange={(e) => upd(setStock, l.k, { qty: e.target.value })} />
                <Input placeholder={t('opening.unitCost')} aria-label={t('opening.unitCost')} inputMode="decimal" className="num" value={l.cost} onChange={(e) => upd(setStock, l.k, { cost: e.target.value })} />
                <Remove onClick={() => del(setStock, l.k)} />
              </div>
            ))}
            {stock.length > 0 && <div className="flex justify-between px-5 py-3 text-sm"><span className="text-muted">{t('opening.stockValue')}</span><Taka value={totals.stock} className="num font-semibold" /></div>}
          </Section>

          <Section title={t('opening.sections.customers')} hint={t('opening.customersHint')} addLabel={t('opening.add')} onAdd={() => setDues((r) => [...r, { k: id(), customer: null, amount: '' }])}>
            {dues.map((l) => (
              <div key={l.k} className="grid gap-3 p-4 sm:grid-cols-[1fr_12rem_auto]">
                <CustomerPicker value={l.customer} onChange={(c) => upd(setDues, l.k, { customer: c })} />
                {amountInput(l.amount, (v) => upd(setDues, l.k, { amount: v }))}
                <Remove onClick={() => del(setDues, l.k)} />
              </div>
            ))}
          </Section>

          <Section title={t('opening.sections.supplierBalance')} hint={t('opening.supplierHint')} addLabel={t('opening.add')} onAdd={() => setBalances((r) => [...r, { k: id(), supplierId: '', direction: 'advance', amount: '' }])}>
            {balances.map((l) => (
              <div key={l.k} className="grid gap-3 p-4 sm:grid-cols-[1fr_14rem_12rem_auto]">
                {supplierSelect(l.supplierId, (v) => upd(setBalances, l.k, { supplierId: v }))}
                <Select aria-label={t('opening.direction')} value={l.direction} onChange={(e) => upd(setBalances, l.k, { direction: e.target.value as 'advance' | 'payable' })}>
                  <option value="advance">{t('opening.advance')}</option>
                  <option value="payable">{t('opening.payable')}</option>
                </Select>
                {amountInput(l.amount, (v) => upd(setBalances, l.k, { amount: v }))}
                <Remove onClick={() => del(setBalances, l.k)} />
              </div>
            ))}
          </Section>

          <Section title={t('opening.sections.claims')} hint={t('opening.claimsHint')} addLabel={t('opening.add')} onAdd={() => setClaims((r) => [...r, { k: id(), supplierId: '', amount: '' }])}>
            {claims.map((l) => (
              <div key={l.k} className="grid gap-3 p-4 sm:grid-cols-[1fr_12rem_auto]">
                {supplierSelect(l.supplierId, (v) => upd(setClaims, l.k, { supplierId: v }))}
                {amountInput(l.amount, (v) => upd(setClaims, l.k, { amount: v }))}
                <Remove onClick={() => del(setClaims, l.k)} />
              </div>
            ))}
          </Section>

          <Section title={t('opening.sections.cash')} hint={t('opening.cashHint')} addLabel={t('opening.add')} onAdd={() => setCash((r) => [...r, { k: id(), accountId: '', amount: '' }])}>
            {cash.map((l) => (
              <div key={l.k} className="grid gap-3 p-4 sm:grid-cols-[1fr_12rem_auto]">
                <Select aria-label={t('payment.paidFrom')} value={l.accountId} onChange={(e) => upd(setCash, l.k, { accountId: e.target.value })}>
                  <option value="">—</option>
                  {accounts.data?.filter((a) => a.is_active).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
                {amountInput(l.amount, (v) => upd(setCash, l.k, { amount: v }))}
                <Remove onClick={() => del(setCash, l.k)} />
              </div>
            ))}
          </Section>

          {error && <div role="alert" className="rounded-lg bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>}
          <div className="flex justify-end">
            <Button onClick={review} loading={submit.isPending}>{t('opening.review')}</Button>
          </div>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t('opening.confirmTitle')}
        message={t('opening.confirmMessage', { date, stock: totals.stock, dues: totals.dues, cash: totals.cash })}
        confirmLabel={t('opening.post')}
        onConfirm={async () => { await submit.mutateAsync() }}
      />
    </>
  )
}

