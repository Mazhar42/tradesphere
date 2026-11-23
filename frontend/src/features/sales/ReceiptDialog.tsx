import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { CustomerPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { Taka } from '@/features/purchases/shared'
import { blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'

import { invalidateSales, useSalesPaymentAccounts } from './hooks'

type Customer = Schemas['CustomerRead']

interface ReceiptDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Fixed when recording from a customer's page or an invoice; otherwise the user picks one. */
  customer?: { id: number; name: string }
}

/** Record money received from a customer. The server applies it to the oldest open invoices. */
export function ReceiptDialog({ open, onOpenChange, customer: fixedCustomer }: ReceiptDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const accounts = useSalesPaymentAccounts()

  const [picked, setPicked] = useState<Customer | null>(null)
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [date, setDate] = useState('')
  const [reference, setReference] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)

  // Reset the form each time the dialog opens (compared during render, not in an effect).
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setPicked(null)
      setAmount('')
      setDate('')
      setReference('')
      setError(null)
    }
  }

  const chosenId = fixedCustomer?.id ?? picked?.id ?? null
  const chosenName = fixedCustomer?.name ?? picked?.name ?? ''
  const summary = useQuery({
    queryKey: ['customer-summary', chosenId],
    enabled: open && chosenId !== null,
    queryFn: () =>
      unwrap(api.GET('/api/v1/customers/{customer_id}/summary', { params: { path: { customer_id: chosenId! } } })),
  })

  const effectiveAccount = accountId || String((accounts.data ?? []).find((a) => a.kind === 'cash')?.id ?? '')

  const submit = useMutation({
    mutationFn: () => {
      if (chosenId === null) throw new Error(t('sales.chooseCustomer'))
      if (!MONEY_PATTERN.test(amount) || Number(amount) <= 0) throw new Error(t('sales.enterAmount'))
      if (!effectiveAccount) throw new Error(t('sales.chooseAccount'))
      return unwrap(
        api.POST('/api/v1/customer-receipts', {
          params: { header: { 'Idempotency-Key': idem.current() } },
          body: {
            customer_id: chosenId,
            amount,
            payment_account_id: Number(effectiveAccount),
            business_date: date || null,
            reference: blankToNull(reference),
            notes: null,
            allocations: [],
          },
        }),
      )
    },
    onSuccess: (receipt) => {
      idem.settle()
      toast.success(t('sales.receiptRecorded', { no: receipt.doc_no }))
      invalidateSales(queryClient)
      onOpenChange(false)
    },
    onError: (e) => {
      idem.settle(e)
      setError(e.message)
    },
  })

  const balance = summary.data?.balance

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('sales.receivePayment')}
      description={t('sales.receiptHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('sales.recordReceipt')}
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
          {fixedCustomer ? (
            <Field label={t('sales.customer')} htmlFor="r-customer">
              <div id="r-customer" className="rounded-lg border border-border bg-surface-muted/50 px-3 py-2 text-sm">
                {chosenName}
              </div>
            </Field>
          ) : (
            <Field label={t('sales.customer')} htmlFor="r-customer">
              <CustomerPicker id="r-customer" value={picked} onChange={setPicked} />
            </Field>
          )}
          <Field label={t('sales.amount')} htmlFor="r-amount">
            <Input id="r-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t('sales.receivedInto')} htmlFor="r-account">
            <Select id="r-account" value={effectiveAccount} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('sales.date')} htmlFor="r-date" hint={t('sales.defaultsToday')}>
            <Input id="r-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('sales.reference')} htmlFor="r-ref" optional optionalLabel={t('common.optional')}>
            <Input id="r-ref" value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
        </div>
        {chosenId !== null && balance !== undefined && (
          <div className="flex items-center justify-between rounded-lg border border-border px-4 py-2.5 text-sm">
            <span className="text-muted">{t('sales.balanceNow')}</span>
            <Taka value={balance} className="font-medium" />
          </div>
        )}
        <p className="text-xs text-muted">{t('sales.allocationNote')}</p>
      </div>
    </Dialog>
  )
}
