import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { cmpMoney, subMoney, sumMoney } from '@/lib/decimal'
import { blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

import { purchaseKeys, useActiveSuppliers, useAllocatableOrders, usePaymentAccounts } from './hooks'
import { Taka } from './shared'

interface PaymentDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  supplierId?: number
  /** Pre-allocate the payment to this purchase order. */
  poId?: number
}

export function PaymentDialog({ open, onOpenChange, supplierId: fixedSupplier, poId }: PaymentDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const suppliers = useActiveSuppliers()
  const accounts = usePaymentAccounts()

  const [supplierId, setSupplierId] = useState('')
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [date, setDate] = useState('')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [alloc, setAlloc] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)
  // Render-time bookkeeping instead of effects: reset the form when the dialog opens, and
  // pre-allocate once the order we came from is known.
  const [wasOpen, setWasOpen] = useState(open)
  const [prefilledPo, setPrefilledPo] = useState<number | null>(null)

  const effectiveSupplier = fixedSupplier ?? (supplierId ? Number(supplierId) : null)
  const orders = useAllocatableOrders(open ? effectiveSupplier : null)

  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setSupplierId(fixedSupplier ? String(fixedSupplier) : '')
      setAmount('')
      setDate('')
      setReference('')
      setNotes('')
      setAlloc({})
      setError(null)
      setPrefilledPo(null)
    }
  }

  if (open && poId !== undefined && prefilledPo !== poId) {
    const order = orders.data?.find((o) => o.id === poId)
    if (order) {
      setPrefilledPo(poId)
      setAlloc({ [poId]: order.room })
      setAmount((a) => a || order.room)
    }
  }

  // Default to the first active account until the user picks one.
  const defaultAccountId = useMemo(() => {
    const list = accounts.data ?? []
    const first = list.find((a) => a.is_active) ?? list[0]
    return first ? String(first.id) : ''
  }, [accounts.data])
  const effectiveAccountId = accountId || defaultAccountId

  const allocated = useMemo(
    () => sumMoney(Object.values(alloc).filter((v) => MONEY_PATTERN.test(v) && Number(v) > 0)),
    [alloc],
  )
  const unallocated = MONEY_PATTERN.test(amount) ? subMoney(amount, allocated) : '0.00'

  const submit = useMutation({
    mutationFn: () => {
      if (!effectiveSupplier) throw new Error(t('payment.chooseSupplier'))
      if (!MONEY_PATTERN.test(amount) || Number(amount) <= 0) throw new Error(t('payment.enterAmount'))
      if (!effectiveAccountId) throw new Error(t('payment.chooseAccount'))
      if (cmpMoney(unallocated, '0') < 0) throw new Error(t('payment.overAllocated'))
      return unwrap(
        api.POST('/api/v1/supplier-payments', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            supplier_id: effectiveSupplier,
            amount,
            payment_account_id: Number(effectiveAccountId),
            business_date: date || null,
            reference: blankToNull(reference),
            notes: blankToNull(notes),
            allocations: Object.entries(alloc)
              .filter(([, v]) => MONEY_PATTERN.test(v) && Number(v) > 0)
              .map(([id, v]) => ({ po_id: Number(id), amount: v })),
          },
        }),
      )
    },
    onSuccess: (payment) => {
      idem.settle()
      toast.success(t('payment.recorded', { no: payment.doc_no }))
      for (const key of [purchaseKeys.orders, purchaseKeys.payments, ['purchase-order'], ['supplier-summary'], ['supplier-ledger'], ['payment-accounts']]) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
      onOpenChange(false)
    },
    onError: (e) => {
      idem.settle(e)
      setError(e.message)
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('payment.title')}
      description={t('payment.hint')}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('payment.record')}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {error && (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          {!fixedSupplier && (
            <Field label={t('purchases.supplier')} htmlFor="p-supplier">
              <Select id="p-supplier" value={supplierId} onChange={(e) => { setSupplierId(e.target.value); setAlloc({}) }}>
                <option value="">—</option>
                {suppliers.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={t('payment.amount')} htmlFor="p-amount">
            <Input id="p-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('payment.paidFrom')} htmlFor="p-account">
            <Select id="p-account" value={effectiveAccountId} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.data
                ?.filter((a) => a.is_active)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </Select>
          </Field>
          <Field label={t('payment.date')} htmlFor="p-date" hint={t('purchases.defaultsToday')}>
            <Input id="p-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('payment.reference')} htmlFor="p-ref" optional optionalLabel={t('common.optional')}>
            <Input id="p-ref" value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
          <Field label={t('common.notes')} htmlFor="p-notes" optional optionalLabel={t('common.optional')}>
            <Input id="p-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>

        {effectiveSupplier && (
          <div className="rounded-lg border border-border">
            <div className="border-b border-border px-4 py-2.5">
              <p className="text-sm font-medium">{t('payment.allocate')}</p>
              <p className="text-xs text-muted">{t('payment.allocateHint')}</p>
            </div>
            {orders.data?.length === 0 && <p className="px-4 py-3 text-sm text-muted">{t('payment.noOrders')}</p>}
            <div className="divide-y divide-border">
              {orders.data?.map((po) => (
                <div key={po.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
                  <div className="text-sm">
                    <span className="font-medium">{po.doc_no}</span>
                    <span className="ml-2 text-xs text-muted">
                      {t('payment.payable')}: <Taka value={po.room} />
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="ghost" size="sm" onClick={() => setAlloc((a) => ({ ...a, [po.id]: po.room }))}>
                      {t('payment.fill')}
                    </Button>
                    <Input
                      aria-label={po.doc_no ?? ''}
                      inputMode="decimal"
                      className="num h-9 w-32"
                      value={alloc[po.id] ?? ''}
                      onChange={(e) => setAlloc((a) => ({ ...a, [po.id]: e.target.value }))}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between border-t border-border bg-surface-muted/50 px-4 py-2.5 text-sm">
              <span className="text-muted">{t('payment.unallocated')}</span>
              <Taka value={unallocated} className={cmpMoney(unallocated, '0') < 0 ? 'font-medium text-danger' : 'font-medium'} />
            </div>
          </div>
        )}
      </div>
    </Dialog>
  )
}
