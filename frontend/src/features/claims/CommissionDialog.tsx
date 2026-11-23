import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { CustomerPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select } from '@/components/ui/input'
import { useActiveSuppliers } from '@/features/purchases/hooks'
import { useSalesPaymentAccounts } from '@/features/sales/hooks'
import { blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

import { invalidateClaims } from './hooks'

type Customer = Schemas['CustomerRead']

/** Record a commission paid to a customer. Company-funded ones become items to claim back. */
export function CommissionDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const suppliers = useActiveSuppliers()
  const accounts = useSalesPaymentAccounts()

  const [customer, setCustomer] = useState<Customer | null>(null)
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<'cash' | 'credit_to_account'>('cash')
  const [accountId, setAccountId] = useState('')
  const [funded, setFunded] = useState(true)
  const [supplierId, setSupplierId] = useState('')
  const [scheme, setScheme] = useState('')
  const [date, setDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)

  // Reset the form each time the dialog opens (compared during render, not in an effect).
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setCustomer(null)
      setAmount('')
      setMethod('cash')
      setFunded(true)
      setSupplierId('')
      setScheme('')
      setDate('')
      setError(null)
    }
  }

  const effectiveAccount = accountId || String((accounts.data ?? []).find((a) => a.kind === 'cash')?.id ?? '')

  const submit = useMutation({
    mutationFn: () => {
      if (!customer) throw new Error(t('sales.chooseCustomer'))
      if (!MONEY_PATTERN.test(amount) || Number(amount) <= 0) throw new Error(t('sales.enterAmount'))
      if (method === 'cash' && !effectiveAccount) throw new Error(t('sales.chooseAccount'))
      if (funded && !supplierId) throw new Error(t('claims.chooseCompany'))
      return unwrap(
        api.POST('/api/v1/commission-payments', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            customer_id: customer.id,
            amount,
            method,
            payment_account_id: method === 'cash' ? Number(effectiveAccount) : null,
            is_company_funded: funded,
            supplier_id: funded ? Number(supplierId) : null,
            business_date: date || null,
            scheme_ref: blankToNull(scheme),
          },
        }),
      )
    },
    onSuccess: (payment) => {
      idem.settle()
      toast.success(t('claims.commissionRecorded', { no: payment.doc_no }))
      invalidateClaims(queryClient)
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
      title={t('claims.recordCommission')}
      description={t('claims.commissionHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('claims.recordCommission')}
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
          <Field label={t('sales.customer')} htmlFor="c-customer">
            <CustomerPicker id="c-customer" value={customer} onChange={setCustomer} />
          </Field>
          <Field label={t('sales.amount')} htmlFor="c-amount">
            <Input id="c-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('claims.method')} htmlFor="c-method">
            <Select id="c-method" value={method} onChange={(e) => setMethod(e.target.value as 'cash' | 'credit_to_account')}>
              <option value="cash">{t('claims.methods.cash')}</option>
              <option value="credit_to_account">{t('claims.methods.credit_to_account')}</option>
            </Select>
          </Field>
          {method === 'cash' && (
            <Field label={t('claims.paidFrom')} htmlFor="c-account">
              <Select id="c-account" value={effectiveAccount} onChange={(e) => setAccountId(e.target.value)}>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={t('sales.date')} htmlFor="c-date" hint={t('sales.defaultsToday')}>
            <Input id="c-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('claims.schemeRef')} htmlFor="c-scheme" optional optionalLabel={t('common.optional')}>
            <Input id="c-scheme" value={scheme} onChange={(e) => setScheme(e.target.value)} />
          </Field>
        </div>
        <Checkbox label={t('claims.companyFunded')} checked={funded} onChange={(e) => setFunded(e.target.checked)} />
        {funded && (
          <Field label={t('claims.company')} htmlFor="c-company" hint={t('claims.companyHint')}>
            <Select id="c-company" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">—</option>
              {suppliers.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  )
}
