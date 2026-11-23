import { zodResolver } from '@hookform/resolvers/zod'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { cmpMoney } from '@/lib/decimal'
import { formatDate } from '@/lib/format'
import { applyServerErrors, blankToNull } from '@/lib/forms'

type Tab = 'accounts' | 'journal' | 'trial'
const PAGE_SIZE = 15

const schema = z.object({
  name: z.string().trim().min(1, 'Required').max(100),
  kind: z.enum(['cash', 'bank', 'mfs']),
  gl_account_code: z.string(),
  bank_name: z.string().trim().max(100),
  account_no: z.string().trim().max(50),
})
type FormValues = z.infer<typeof schema>

function NewAccountDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { register, handleSubmit, reset, setError, control, formState: { errors, isSubmitting } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', kind: 'bank', gl_account_code: '', bank_name: '', account_no: '' },
  })
  const kind = useWatch({ control, name: 'kind' })

  const mutation = useMutation({
    mutationFn: (v: FormValues) =>
      unwrap(
        api.POST('/api/v1/payment-accounts', {
          body: {
            name: v.name,
            kind: v.kind,
            gl_account_code: (v.kind === 'mfs' && v.gl_account_code ? v.gl_account_code : null) as '1020' | '1030' | null,
            bank_name: blankToNull(v.bank_name),
            account_no: blankToNull(v.account_no),
          },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['payment-accounts'] })
      toast.success(t('common.created'))
      reset()
      onOpenChange(false)
    },
  })

  const onSubmit = handleSubmit(async (v) => {
    try {
      await mutation.mutateAsync(v)
    } catch (e) {
      applyServerErrors(e, setError, Object.keys(schema.shape))
    }
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('accounts.newAccount')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button type="submit" form="pa-form" loading={isSubmitting}>{t('common.save')}</Button>
        </>
      }
    >
      <form id="pa-form" onSubmit={onSubmit} className="grid gap-4" noValidate>
        <Field label={t('common.nameEn')} htmlFor="pa-name" error={errors.name?.message}>
          <Input id="pa-name" aria-invalid={!!errors.name} {...register('name')} />
        </Field>
        <Field label={t('accounts.kind')} htmlFor="pa-kind">
          <Select id="pa-kind" {...register('kind')}>
            <option value="bank">{t('accounts.kinds.bank')}</option>
            <option value="mfs">{t('accounts.kinds.mfs')}</option>
            <option value="cash">{t('accounts.kinds.cash')}</option>
          </Select>
        </Field>
        {kind === 'mfs' && (
          <Field label={t('accounts.provider')} htmlFor="pa-gl">
            <Select id="pa-gl" {...register('gl_account_code')}>
              <option value="1020">bKash</option>
              <option value="1030">Nagad</option>
            </Select>
          </Field>
        )}
        {kind === 'bank' && (
          <>
            <Field label={t('accounts.bankName')} htmlFor="pa-bank" optional optionalLabel={t('common.optional')}>
              <Input id="pa-bank" {...register('bank_name')} />
            </Field>
            <Field label={t('accounts.accountNo')} htmlFor="pa-no" optional optionalLabel={t('common.optional')}>
              <Input id="pa-no" {...register('account_no')} />
            </Field>
          </>
        )}
      </form>
    </Dialog>
  )
}

function PaymentAccounts() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['payment-accounts'], queryFn: () => unwrap(api.GET('/api/v1/payment-accounts')) })
  const total = (query.data ?? []).reduce((sum, a) => sum + Number(a.balance), 0)

  if (query.isError) return <div className="p-4"><ErrorState error={query.error} onRetry={() => void query.refetch()} /></div>
  return (
    <div className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">{t('accounts.totalHeld')}: <Taka value={total.toFixed(2)} className="font-semibold text-foreground" /></p>
        {can('finance:write') && (
          <Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" /> {t('accounts.newAccount')}</Button>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {query.data?.map((a) => (
          <Card key={a.id} className="p-4">
            <div className="flex items-start justify-between">
              <div>
                <p className="font-medium">{a.name}</p>
                <p className="text-xs text-muted">{t(`accounts.kinds.${a.kind}`)}{a.account_no ? ` · ${a.account_no}` : ''}</p>
              </div>
              {!a.is_active && <Badge>{t('common.inactive')}</Badge>}
            </div>
            <p className={`num mt-3 text-2xl font-semibold ${cmpMoney(a.balance, '0') < 0 ? 'text-danger' : ''}`}><Taka value={a.balance} /></p>
          </Card>
        ))}
      </div>
      <NewAccountDialog open={open} onOpenChange={setOpen} />
    </div>
  )
}

function Journal() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const query = useQuery({
    queryKey: ['journal', { page, q }],
    queryFn: () => unwrap(api.GET('/api/v1/journal', { params: { query: { page, page_size: PAGE_SIZE, q: q || undefined } } })),
    placeholderData: keepPreviousData,
  })
  return (
    <>
      <Toolbar><SearchInput value={q} onChange={(v) => { setQ(v); setPage(1) }} /></Toolbar>
      {query.isError ? (
        <div className="p-4"><ErrorState error={query.error} onRetry={() => void query.refetch()} /></div>
      ) : (
        <div className="divide-y divide-border">
          {query.data?.items.map((e: Schemas['JournalEntryRead']) => (
            <div key={e.id} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm"><span className="font-medium">{e.entry_no}</span> <span className="text-muted">· {formatDate(e.business_date, lang)} · {e.memo}</span></p>
                {e.reversal_of_id && <Badge tone="danger">{t('accounts.reversal')}</Badge>}
              </div>
              <table className="mt-2 w-full text-sm">
                <tbody>
                  {e.lines.map((l) => (
                    <tr key={l.id} className="text-muted">
                      <td className={`py-0.5 ${Number(l.credit) > 0 ? 'pl-8' : ''}`}>{l.account_code} · {l.account_name}</td>
                      <td className="num w-36 py-0.5 text-right text-foreground">{Number(l.debit) > 0 && <Taka value={l.debit} />}</td>
                      <td className="num w-36 py-0.5 text-right text-foreground">{Number(l.credit) > 0 && <Taka value={l.credit} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {query.data?.items.length === 0 && <p className="px-4 py-10 text-center text-sm text-muted">{t('common.noResults')}</p>}
        </div>
      )}
      <Pagination page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPageChange={setPage} />
    </>
  )
}

function TrialBalance() {
  const { t } = useTranslation()
  const [asOf, setAsOf] = useState('')
  const query = useQuery({
    queryKey: ['trial-balance', asOf],
    queryFn: () => unwrap(api.GET('/api/v1/reports/trial-balance', { params: { query: { as_of: asOf || undefined } } })),
  })
  const r = query.data
  return (
    <>
      <Toolbar>
        <Field label={t('accounts.asOf')} htmlFor="asof" className="w-48"><Input id="asof" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field>
        {r && <Badge tone={r.balanced ? 'success' : 'danger'}>{r.balanced ? t('accounts.balanced') : t('accounts.unbalanced')}</Badge>}
      </Toolbar>
      {query.isError ? (
        <div className="p-4"><ErrorState error={query.error} onRetry={() => void query.refetch()} /></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-muted/60 text-xs text-muted uppercase">
              <tr><th className="px-4 py-2.5 text-left">{t('accounts.account')}</th><th className="px-4 py-2.5 text-right">{t('accounts.debit')}</th><th className="px-4 py-2.5 text-right">{t('accounts.credit')}</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {r?.rows.map((row) => (
                <tr key={row.account_code}>
                  <td className="px-4 py-2"><span className="font-mono text-xs text-muted">{row.account_code}</span> {row.account_name}</td>
                  <td className="num px-4 py-2 text-right">{Number(row.debit) > 0 && <Taka value={row.debit} />}</td>
                  <td className="num px-4 py-2 text-right">{Number(row.credit) > 0 && <Taka value={row.credit} />}</td>
                </tr>
              ))}
            </tbody>
            {r && (
              <tfoot className="border-t-2 border-border font-semibold">
                <tr><td className="px-4 py-2.5">{t('accounts.total')}</td><td className="num px-4 py-2.5 text-right"><Taka value={r.total_debit} /></td><td className="num px-4 py-2.5 text-right"><Taka value={r.total_credit} /></td></tr>
              </tfoot>
            )}
          </table>
          {r?.rows.length === 0 && <p className="px-4 py-10 text-center text-sm text-muted">{t('common.noResults')}</p>}
        </div>
      )}
    </>
  )
}

export function AccountsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('accounts')
  return (
    <>
      <PageHeader title={t('accounts.title')} description={t('accounts.subtitle')} />
      <Card>
        <Toolbar>
          <Tabs value={tab} onChange={setTab} items={[{ value: 'accounts', label: t('accounts.paymentAccounts') }, { value: 'journal', label: t('accounts.journal') }, { value: 'trial', label: t('accounts.trialBalance') }]} />
        </Toolbar>
        {tab === 'accounts' ? <PaymentAccounts /> : tab === 'journal' ? <Journal /> : <TrialBalance />}
      </Card>
    </>
  )
}
