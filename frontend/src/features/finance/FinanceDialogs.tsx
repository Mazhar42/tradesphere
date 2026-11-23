import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { useSalesPaymentAccounts } from '@/features/sales/hooks'
import { useLang } from '@/i18n'
import { blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

import { invalidateFinance, useExpenseAccounts } from './hooks'

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type PaymentAccount = NonNullable<ReturnType<typeof useSalesPaymentAccounts>['data']>[number]

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </div>
  )
}

function accountOptions(accounts: PaymentAccount[] | undefined) {
  return accounts?.map((a) => (
    <option key={a.id} value={a.id}>
      {a.name}
    </option>
  ))
}

function defaultCash(accounts: PaymentAccount[] | undefined): string {
  return String(accounts?.find((a) => a.kind === 'cash')?.id ?? '')
}

function checkAmount(value: string, message: string): void {
  if (!MONEY_PATTERN.test(value) || Number(value) <= 0) throw new Error(message)
}

/** Record an expense: Dr expense account, Cr the payment account it was paid from. */
export function ExpenseDialog({ open, onOpenChange }: DialogProps) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const expenseAccounts = useExpenseAccounts()
  const payAccounts = useSalesPaymentAccounts()

  const [account, setAccount] = useState('')
  const [payFrom, setPayFrom] = useState('')
  const [amount, setAmount] = useState('')
  const [payee, setPayee] = useState('')
  const [notes, setNotes] = useState('')
  const [date, setDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)

  // Reset each time the dialog opens (compared during render, not in an effect).
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setAccount('')
      setPayFrom('')
      setAmount('')
      setPayee('')
      setNotes('')
      setDate('')
      setError(null)
    }
  }

  const effectivePayFrom = payFrom || defaultCash(payAccounts.data)

  const submit = useMutation({
    mutationFn: () => {
      if (!account) throw new Error(t('finance.expense.chooseExpense'))
      checkAmount(amount, t('sales.enterAmount'))
      if (!effectivePayFrom) throw new Error(t('sales.chooseAccount'))
      return unwrap(
        api.POST('/api/v1/expenses', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            expense_account_id: Number(account),
            amount,
            payment_account_id: Number(effectivePayFrom),
            business_date: date || null,
            payee: blankToNull(payee),
            notes: blankToNull(notes),
          },
        }),
      )
    },
    onSuccess: (expense) => {
      idem.settle()
      toast.success(t('finance.recorded', { doc: expense.doc_no }))
      invalidateFinance(queryClient)
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
      title={t('finance.expense.title')}
      description={t('finance.expense.hint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('finance.actions.expenses')}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <ErrorBanner message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('finance.expense.account')} htmlFor="e-account">
            <Select id="e-account" value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">{t('finance.expense.chooseExpense')}</option>
              {expenseAccounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code} {lang === 'bn' && a.name_bn ? a.name_bn : a.name_en}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('sales.amount')} htmlFor="e-amount">
            <Input id="e-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('finance.expense.paidFrom')} htmlFor="e-pay">
            <Select id="e-pay" value={effectivePayFrom} onChange={(e) => setPayFrom(e.target.value)}>
              {accountOptions(payAccounts.data)}
            </Select>
          </Field>
          <Field label={t('finance.expense.payee')} htmlFor="e-payee" optional optionalLabel={t('common.optional')}>
            <Input id="e-payee" value={payee} onChange={(e) => setPayee(e.target.value)} />
          </Field>
          <Field label={t('sales.date')} htmlFor="e-date" hint={t('sales.defaultsToday')}>
            <Input id="e-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('finance.columns.notes')} htmlFor="e-notes" optional optionalLabel={t('common.optional')}>
            <Input id="e-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}

/** Move money between two payment accounts; charges become bank and MFS charges. */
export function TransferDialog({ open, onOpenChange }: DialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const payAccounts = useSalesPaymentAccounts()

  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [charges, setCharges] = useState('')
  const [reference, setReference] = useState('')
  const [date, setDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)

  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setFrom('')
      setTo('')
      setAmount('')
      setCharges('')
      setReference('')
      setDate('')
      setError(null)
    }
  }

  const effectiveFrom = from || defaultCash(payAccounts.data)

  const submit = useMutation({
    mutationFn: () => {
      if (!effectiveFrom || !to) throw new Error(t('sales.chooseAccount'))
      if (effectiveFrom === to) throw new Error(t('finance.transfer.sameAccount'))
      checkAmount(amount, t('sales.enterAmount'))
      if (charges && !MONEY_PATTERN.test(charges)) throw new Error(t('sales.enterAmount'))
      return unwrap(
        api.POST('/api/v1/fund-transfers', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            from_payment_account_id: Number(effectiveFrom),
            to_payment_account_id: Number(to),
            amount,
            charges: charges || '0',
            business_date: date || null,
            reference: blankToNull(reference),
          },
        }),
      )
    },
    onSuccess: (transfer) => {
      idem.settle()
      toast.success(t('finance.recorded', { doc: transfer.doc_no }))
      invalidateFinance(queryClient)
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
      title={t('finance.transfer.title')}
      description={t('finance.transfer.hint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('finance.actions.transfers')}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <ErrorBanner message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('finance.columns.from')} htmlFor="t-from">
            <Select id="t-from" value={effectiveFrom} onChange={(e) => setFrom(e.target.value)}>
              {accountOptions(payAccounts.data)}
            </Select>
          </Field>
          <Field label={t('finance.columns.to')} htmlFor="t-to">
            <Select id="t-to" value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">{t('finance.chooseAccount')}</option>
              {accountOptions(payAccounts.data)}
            </Select>
          </Field>
          <Field label={t('sales.amount')} htmlFor="t-amount">
            <Input id="t-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('finance.columns.charges')} htmlFor="t-charges" optional optionalLabel={t('common.optional')}>
            <Input id="t-charges" inputMode="decimal" className="num" value={charges} onChange={(e) => setCharges(e.target.value)} />
          </Field>
          <Field label={t('sales.date')} htmlFor="t-date" hint={t('sales.defaultsToday')}>
            <Input id="t-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('finance.transfer.reference')} htmlFor="t-ref" optional optionalLabel={t('common.optional')}>
            <Input id="t-ref" value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}

/** Owner capital (money in, equity) or drawings (money out, equity). Neither is P&L. */
export function OwnerDialog({ open, onOpenChange }: DialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const payAccounts = useSalesPaymentAccounts()

  const [kind, setKind] = useState<'capital' | 'drawing'>('capital')
  const [account, setAccount] = useState('')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)

  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setKind('capital')
      setAccount('')
      setAmount('')
      setDate('')
      setNotes('')
      setError(null)
    }
  }

  const effectiveAccount = account || defaultCash(payAccounts.data)

  const submit = useMutation({
    mutationFn: () => {
      checkAmount(amount, t('sales.enterAmount'))
      if (!effectiveAccount) throw new Error(t('sales.chooseAccount'))
      return unwrap(
        api.POST('/api/v1/owner-transactions', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            kind,
            amount,
            payment_account_id: Number(effectiveAccount),
            business_date: date || null,
            notes: blankToNull(notes),
          },
        }),
      )
    },
    onSuccess: (owner) => {
      idem.settle()
      toast.success(t('finance.recorded', { doc: owner.doc_no }))
      invalidateFinance(queryClient)
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
      title={t('finance.owner.title')}
      description={t('finance.owner.hint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('finance.actions.owner')}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <ErrorBanner message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('finance.owner.kind')} htmlFor="o-kind">
            <Select id="o-kind" value={kind} onChange={(e) => setKind(e.target.value as 'capital' | 'drawing')}>
              <option value="capital">{t('finance.kinds.capital')}</option>
              <option value="drawing">{t('finance.kinds.drawing')}</option>
            </Select>
          </Field>
          <Field label={t('sales.amount')} htmlFor="o-amount">
            <Input id="o-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('finance.owner.account')} htmlFor="o-account">
            <Select id="o-account" value={effectiveAccount} onChange={(e) => setAccount(e.target.value)}>
              {accountOptions(payAccounts.data)}
            </Select>
          </Field>
          <Field label={t('sales.date')} htmlFor="o-date" hint={t('sales.defaultsToday')}>
            <Input id="o-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('finance.columns.notes')} htmlFor="o-notes" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
            <Input id="o-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}
