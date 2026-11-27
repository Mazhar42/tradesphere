import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Ban, Banknote, Send, Trash2 } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { ReverseButton } from '@/components/data/ReverseButton'
import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { Taka } from '@/features/purchases/shared'
import { useSalesPaymentAccounts } from '@/features/sales/hooks'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { formatDate } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'
import { cn } from '@/lib/utils'

import { ClaimStatusBadge } from './ClaimsPage'
import { claimKeys, invalidateClaims } from './hooks'

type Claim = Schemas['CompanyClaimRead']
type Line = Schemas['ClaimLineRead']
type Method = 'cash' | 'bank' | 'mfs' | 'supplier_credit'

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'danger' | 'success' }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p className={cn('num mt-1.5 text-xl font-semibold', tone === 'danger' && 'text-danger', tone === 'success' && 'text-success')}>{value}</p>
    </Card>
  )
}

function SettleDialog({ claim, open, onOpenChange }: { claim: Claim; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const accounts = useSalesPaymentAccounts()
  const [method, setMethod] = useState<Method>('cash')
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [date, setDate] = useState('')
  const [reference, setReference] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setAmount(claim.outstanding)
      setDate('')
      setReference('')
      setError(null)
    }
  }
  const needsAccount = method !== 'supplier_credit'
  const effectiveAccount = accountId || String((accounts.data ?? []).find((a) => a.kind === 'cash')?.id ?? '')

  const submit = useMutation({
    mutationFn: () => {
      if (!MONEY_PATTERN.test(amount) || Number(amount) <= 0) throw new Error(t('sales.enterAmount'))
      if (needsAccount && !effectiveAccount) throw new Error(t('sales.chooseAccount'))
      return unwrap(
        api.POST('/api/v1/company-claims/{claim_id}/settlements', {
          params: { path: { claim_id: claim.id }, header: { 'Idempotency-Key': idem.current() } },
          body: {
            method,
            amount,
            payment_account_id: needsAccount ? Number(effectiveAccount) : null,
            business_date: date || null,
            reference: blankToNull(reference),
          },
        }),
      )
    },
    onSuccess: () => {
      idem.settle()
      toast.success(t('claims.settlementRecorded'))
      invalidateClaims(queryClient, claim.id)
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
      title={t('claims.recordSettlement')}
      description={t('claims.settlementHint', { value: claim.outstanding })}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('claims.recordSettlement')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('claims.method')} htmlFor="s-method">
            <Select id="s-method" value={method} onChange={(e) => setMethod(e.target.value as Method)}>
              {(['cash', 'bank', 'mfs', 'supplier_credit'] as const).map((m) => (
                <option key={m} value={m}>
                  {t(`claims.settleMethods.${m}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('sales.amount')} htmlFor="s-amount">
            <Input id="s-amount" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          {needsAccount && (
            <Field label={t('sales.receivedInto')} htmlFor="s-account">
              <Select id="s-account" value={effectiveAccount} onChange={(e) => setAccountId(e.target.value)}>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={t('sales.date')} htmlFor="s-date" hint={t('sales.defaultsToday')}>
            <Input id="s-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('sales.reference')} htmlFor="s-ref" optional optionalLabel={t('common.optional')}>
            <Input id="s-ref" value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
        </div>
        {!needsAccount && <p className="text-xs text-muted">{t('claims.supplierCreditNote')}</p>}
      </div>
    </Dialog>
  )
}

function RejectDialog({ claim, open, onOpenChange }: { claim: Claim; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const [amounts, setAmounts] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setAmounts({})
      setError(null)
    }
  }
  const open_lines = claim.lines.filter((l) => cmpMoney(l.outstanding_amount, '0') > 0)

  const submit = useMutation({
    mutationFn: () => {
      const lines = Object.entries(amounts)
        .filter(([, v]) => MONEY_PATTERN.test(v) && Number(v) > 0)
        .map(([id, v]) => ({ claim_line_id: Number(id), amount: v }))
      if (lines.length === 0) throw new Error(t('claims.enterRejection'))
      return unwrap(
        api.POST('/api/v1/company-claims/{claim_id}/reject-lines', {
          params: { path: { claim_id: claim.id }, header: { 'Idempotency-Key': idem.current() } },
          body: { lines },
        }),
      )
    },
    onSuccess: () => {
      idem.settle()
      toast.success(t('claims.rejectionRecorded'))
      invalidateClaims(queryClient, claim.id)
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
      size="lg"
      title={t('claims.rejectLines')}
      description={t('claims.rejectHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button variant="danger" loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('claims.rejectLines')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error && (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        {open_lines.map((line) => (
          <div key={line.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-4 py-2.5">
            <div className="text-sm">
              <div className="font-medium">
                {t(`claims.types.${line.claim_type}`, line.claim_type)} · #{line.source_doc_id}
              </div>
              <div className="text-xs text-muted">
                {t('claims.outstanding')}: <Taka value={line.outstanding_amount} />
              </div>
            </div>
            <Input
              aria-label={t('claims.rejectAmount')}
              inputMode="decimal"
              className="num h-9 w-32"
              placeholder="0.00"
              value={amounts[line.id] ?? ''}
              onChange={(e) => setAmounts((a) => ({ ...a, [line.id]: e.target.value }))}
            />
          </div>
        ))}
      </div>
    </Dialog>
  )
}

function SubmitDialog({ claim, open, onOpenChange }: { claim: Claim; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const [ref, setRef] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/company-claims/{claim_id}/submit', {
          params: { path: { claim_id: claim.id }, header: { 'Idempotency-Key': idem.current() } },
          body: { company_ref: blankToNull(ref) },
        }),
      ),
    onSuccess: (c) => {
      idem.settle()
      toast.success(t('claims.submitted', { no: c.doc_no }))
      invalidateClaims(queryClient, claim.id)
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
      title={t('claims.submitClaim')}
      description={t('claims.submitHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('claims.submitClaim')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        <Field label={t('claims.companyRef')} htmlFor="sub-ref" optional optionalLabel={t('common.optional')}>
          <Input id="sub-ref" value={ref} onChange={(e) => setRef(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  )
}

export function ClaimDetailPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { claimId } = useParams({ strict: false }) as { claimId: string }
  const id = Number(claimId)
  const [dialog, setDialog] = useState<'submit' | 'settle' | 'reject' | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const query = useQuery({
    queryKey: claimKeys.claim(id),
    queryFn: () => unwrap(api.GET('/api/v1/company-claims/{claim_id}', { params: { path: { claim_id: id } } })),
  })

  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/v1/company-claims/{claim_id}', { params: { path: { claim_id: id } } })),
    onSuccess: () => {
      toast.success(t('claims.draftDeleted'))
      invalidateClaims(queryClient)
      void navigate({ to: '/claims' })
    },
  })

  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const claim = query.data
  if (!claim) return <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />

  const canWrite = can('claims:write')
  const isDraft = claim.status === 'draft'
  const isOpen = claim.status === 'submitted' || claim.status === 'partially_settled'

  const lineColumns: Column<Line>[] = [
    { header: t('claims.type'), cell: (l) => t(`claims.types.${l.claim_type}`, l.claim_type) },
    { header: t('claims.source'), cell: (l) => `${t(`claims.sources.${l.source_doc_type}`, l.source_doc_type)} #${l.source_doc_id}` },
    { header: t('sales.date'), cell: (l) => formatDate(l.business_date, lang) },
    { header: t('claims.book'), align: 'right', cell: (l) => <Taka value={l.book_amount} /> },
    { header: t('claims.claimed'), align: 'right', cell: (l) => <Taka value={l.claimed_amount} /> },
    { header: t('claims.settled'), align: 'right', cell: (l) => <Taka value={l.settled_amount} /> },
    { header: t('claims.rejected'), align: 'right', cell: (l) => <Taka value={l.rejected_amount} /> },
    { header: t('claims.outstanding'), align: 'right', cell: (l) => <Taka value={l.outstanding_amount} className="font-medium" /> },
  ]
  const settlementColumns: Column<Schemas['ClaimSettlementRead']>[] = [
    { header: t('claims.settlement'), cell: (s) => s.doc_no },
    { header: t('sales.date'), cell: (s) => formatDate(s.business_date, lang) },
    { header: t('claims.method'), cell: (s) => t(`claims.settleMethods.${s.method}`, s.method) },
    { header: t('claims.account'), cell: (s) => s.payment_account_name ?? '—' },
    { header: t('sales.reference'), cell: (s) => s.reference ?? '—' },
    { header: t('sales.amount'), align: 'right', cell: (s) => <Taka value={s.amount} /> },
    {
      id: 'reverse',
      header: '',
      align: 'right',
      cell: (s) => (
        <ReverseButton
          docNo={s.doc_no ?? ''}
          status={s.status}
          allowed={can('claims:reverse')}
          post={(header) => api.POST('/api/v1/company-claims/{claim_id}/settlements/{settlement_id}/reverse', { params: { path: { claim_id: claim.id, settlement_id: s.id }, header } })}
        />
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={claim.doc_no ?? t('claims.draft')}
        description={`${claim.supplier_name} · ${formatDate(claim.business_date, lang)}${claim.company_ref ? ` · ${claim.company_ref}` : ''}`}
        actions={
          <>
            <ClaimStatusBadge status={claim.status} group="claim" />
            {isDraft && canWrite && (
              <>
                <Button variant="outline" onClick={() => setConfirmDelete(true)}>
                  <Trash2 className="size-4" /> {t('common.delete')}
                </Button>
                <Button onClick={() => setDialog('submit')}>
                  <Send className="size-4" /> {t('claims.submitClaim')}
                </Button>
              </>
            )}
            {isOpen && canWrite && (
              <>
                <Button variant="outline" onClick={() => setDialog('reject')}>
                  <Ban className="size-4" /> {t('claims.rejectLines')}
                </Button>
                <Button onClick={() => setDialog('settle')}>
                  <Banknote className="size-4" /> {t('claims.recordSettlement')}
                </Button>
              </>
            )}
          </>
        }
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={t('claims.claimed')} value={<Taka value={claim.total_claimed} />} />
        <Stat label={t('claims.settled')} value={<Taka value={claim.total_settled} />} tone="success" />
        <Stat label={t('claims.rejected')} value={<Taka value={claim.total_rejected} />} tone={cmpMoney(claim.total_rejected, '0') > 0 ? 'danger' : undefined} />
        <Stat label={t('claims.outstanding')} value={<Taka value={claim.outstanding} />} />
      </div>

      <Card className="mb-6">
        <CardHeader title={t('claims.lines')} />
        <DataTable columns={lineColumns} data={claim.lines} />
      </Card>
      <Card>
        <CardHeader title={t('claims.settlements')} />
        <DataTable columns={settlementColumns} data={claim.settlements} />
        {claim.settlements.length === 0 && <p className="px-4 pb-4 text-sm text-muted">{t('claims.noSettlements')}</p>}
      </Card>

      <SubmitDialog claim={claim} open={dialog === 'submit'} onOpenChange={(o) => !o && setDialog(null)} />
      <SettleDialog claim={claim} open={dialog === 'settle'} onOpenChange={(o) => !o && setDialog(null)} />
      <RejectDialog claim={claim} open={dialog === 'reject'} onOpenChange={(o) => !o && setDialog(null)} />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('common.delete')}
        message={t('claims.deleteDraftMessage')}
        confirmLabel={t('common.delete')}
        danger
        onConfirm={() => remove.mutateAsync()}
      />
    </>
  )
}
