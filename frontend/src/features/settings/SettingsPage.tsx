import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select, Textarea } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { applyServerErrors, blankToNull } from '@/lib/forms'
import { formatDate } from '@/lib/format'

const KEY = ['settings'] as const

const profileSchema = z.object({
  name: z.string().trim().min(1, 'Required').max(150),
  name_bn: z.string().trim().max(150),
  address: z.string().trim().max(500),
  phone: z.string().trim().max(50),
  email: z.string().trim().max(150),
  trade_license_no: z.string().trim().max(50),
  bin_no: z.string().trim().max(50),
  invoice_footer: z.string().trim().max(300),
})
type ProfileValues = z.infer<typeof profileSchema>

const prefsSchema = z.object({
  default_language: z.enum(['en', 'bn']),
  invoice_paper: z.enum(['a4', 'thermal80']),
  allow_sell_below_dp: z.boolean(),
  near_expiry_days: z.coerce.number().int().min(1).max(365),
})
type PrefsInput = z.input<typeof prefsSchema>
type PrefsValues = z.output<typeof prefsSchema>

function ProfileCard({ data, canWrite }: { data: Schemas['BusinessProfile']; canWrite: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<ProfileValues>({ resolver: zodResolver(profileSchema) })

  useEffect(() => {
    reset({
      name: data.name ?? '',
      name_bn: data.name_bn ?? '',
      address: data.address ?? '',
      phone: data.phone ?? '',
      email: data.email ?? '',
      trade_license_no: data.trade_license_no ?? '',
      bin_no: data.bin_no ?? '',
      invoice_footer: data.invoice_footer ?? '',
    })
  }, [data, reset])

  const mutation = useMutation({
    mutationFn: (v: ProfileValues) =>
      unwrap(
        api.PUT('/api/v1/settings/business-profile', {
          body: {
            name: v.name,
            name_bn: blankToNull(v.name_bn),
            address: blankToNull(v.address),
            phone: blankToNull(v.phone),
            email: blankToNull(v.email),
            trade_license_no: blankToNull(v.trade_license_no),
            bin_no: blankToNull(v.bin_no),
            invoice_footer: blankToNull(v.invoice_footer),
          },
        }),
      ),
    onSuccess: (settings) => {
      queryClient.setQueryData(KEY, settings)
      toast.success(t('common.saved'))
    },
  })

  const onSubmit = handleSubmit(async (v) => {
    try {
      await mutation.mutateAsync(v)
    } catch (error) {
      applyServerErrors(error, setError, Object.keys(profileSchema.shape))
    }
  })

  return (
    <Card>
      <CardHeader title={t('settings.profile')} description={t('settings.profileHint')} />
      <form onSubmit={onSubmit} className="grid gap-4 p-5 sm:grid-cols-2" noValidate>
        <fieldset disabled={!canWrite} className="contents">
          <Field label={t('settings.businessName')} htmlFor="name" error={errors.name?.message}>
            <Input id="name" {...register('name')} />
          </Field>
          <Field label={t('settings.businessNameBn')} htmlFor="name_bn" optional optionalLabel={t('common.optional')}>
            <Input id="name_bn" lang="bn" {...register('name_bn')} />
          </Field>
          <Field label={t('common.phone')} htmlFor="biz_phone" optional optionalLabel={t('common.optional')}>
            <Input id="biz_phone" {...register('phone')} />
          </Field>
          <Field label={t('common.email')} htmlFor="biz_email" optional optionalLabel={t('common.optional')}>
            <Input id="biz_email" type="email" {...register('email')} />
          </Field>
          <Field label={t('settings.tradeLicense')} htmlFor="trade_license_no" optional optionalLabel={t('common.optional')}>
            <Input id="trade_license_no" {...register('trade_license_no')} />
          </Field>
          <Field label={t('settings.bin')} htmlFor="bin_no" optional optionalLabel={t('common.optional')}>
            <Input id="bin_no" {...register('bin_no')} />
          </Field>
          <Field label={t('common.address')} htmlFor="biz_address" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
            <Textarea id="biz_address" {...register('address')} />
          </Field>
          <Field label={t('settings.invoiceFooter')} htmlFor="invoice_footer" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
            <Input id="invoice_footer" {...register('invoice_footer')} />
          </Field>
        </fieldset>
        {canWrite && (
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" loading={isSubmitting} disabled={!isDirty}>
              {t('common.save')}
            </Button>
          </div>
        )}
      </form>
    </Card>
  )
}

function PreferencesCard({ data, canWrite }: { data: Schemas['Preferences']; canWrite: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<PrefsInput, unknown, PrefsValues>({ resolver: zodResolver(prefsSchema) })

  useEffect(() => {
    reset({
      default_language: data.default_language ?? 'en',
      invoice_paper: data.invoice_paper ?? 'a4',
      allow_sell_below_dp: data.allow_sell_below_dp ?? false,
      near_expiry_days: data.near_expiry_days ?? 30,
    })
  }, [data, reset])

  const mutation = useMutation({
    mutationFn: (v: PrefsValues) => unwrap(api.PUT('/api/v1/settings/preferences', { body: v })),
    onSuccess: (settings) => {
      queryClient.setQueryData(KEY, settings)
      toast.success(t('common.saved'))
    },
    onError: (e) => toast.error(e.message),
  })

  return (
    <Card>
      <CardHeader title={t('settings.preferences')} />
      <form onSubmit={handleSubmit((v) => mutation.mutate(v))} className="grid gap-4 p-5 sm:grid-cols-2" noValidate>
        <fieldset disabled={!canWrite} className="contents">
          <Field label={t('settings.defaultLanguage')} htmlFor="default_language">
            <Select id="default_language" {...register('default_language')}>
              <option value="en">{t('lang.en')}</option>
              <option value="bn">{t('lang.bn')}</option>
            </Select>
          </Field>
          <Field label={t('settings.invoicePaper')} htmlFor="invoice_paper">
            <Select id="invoice_paper" {...register('invoice_paper')}>
              <option value="a4">{t('settings.a4')}</option>
              <option value="thermal80">{t('settings.thermal80')}</option>
            </Select>
          </Field>
          <Field label={t('settings.nearExpiryDays')} htmlFor="near_expiry_days" error={errors.near_expiry_days?.message}>
            <Input id="near_expiry_days" inputMode="numeric" className="num" {...register('near_expiry_days')} />
          </Field>
          <div className="flex items-end pb-2">
            <Checkbox label={t('settings.allowBelowDp')} {...register('allow_sell_below_dp')} />
          </div>
        </fieldset>
        {canWrite && (
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" loading={isSubmitting || mutation.isPending} disabled={!isDirty}>
              {t('common.save')}
            </Button>
          </div>
        )}
      </form>
    </Card>
  )
}

function PeriodLockCard({ data, canWrite }: { data: Schemas['PeriodLock']; canWrite: boolean }) {
  const { t } = useTranslation()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const { register, handleSubmit, reset, setError, formState } = useForm<{ lock_date: string }>()

  useEffect(() => {
    reset({ lock_date: data.lock_date ?? '' })
  }, [data, reset])

  const mutation = useMutation({
    mutationFn: (lockDate: string) =>
      unwrap(api.PUT('/api/v1/settings/period-lock', { body: { lock_date: lockDate || null } })),
    onSuccess: (settings) => {
      queryClient.setQueryData(KEY, settings)
      toast.success(t('common.saved'))
    },
  })

  const onSubmit = handleSubmit(async ({ lock_date }) => {
    try {
      await mutation.mutateAsync(lock_date)
    } catch (error) {
      applyServerErrors(error, setError, ['lock_date'])
    }
  })

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Lock className="size-4" /> {t('settings.periodLock')}
          </span>
        }
        description={t('settings.periodLockHint')}
      />
      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-4 p-5" noValidate>
        <Field
          label={t('settings.lockDate')}
          htmlFor="lock_date"
          hint={data.lock_date ? formatDate(data.lock_date, lang) : t('settings.notLocked')}
          error={formState.errors.lock_date?.message}
        >
          <Input id="lock_date" type="date" className="w-48" disabled={!canWrite} {...register('lock_date')} />
        </Field>
        {canWrite && (
          <Button type="submit" loading={mutation.isPending} disabled={!formState.isDirty}>
            {t('common.save')}
          </Button>
        )}
      </form>
    </Card>
  )
}

export function SettingsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const canWrite = can('settings:write')
  const query = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/v1/settings')) })

  return (
    <>
      <PageHeader title={t('settings.title')} description={t('settings.subtitle')} />
      {!canWrite && (
        <p className="mb-4 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning">{t('settings.readOnly')}</p>
      )}
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data ? (
        <div className="grid gap-6">
          <ProfileCard data={query.data.business_profile} canWrite={canWrite} />
          <div className="grid gap-6 lg:grid-cols-2">
            <PreferencesCard data={query.data.preferences} canWrite={canWrite} />
            <PeriodLockCard data={query.data.period_lock} canWrite={canWrite} />
          </div>
        </div>
      ) : (
        <div className="h-64 animate-pulse rounded-xl bg-surface-muted" />
      )}
    </>
  )
}
