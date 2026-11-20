import { zodResolver } from '@hookform/resolvers/zod'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select, Textarea } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { applyServerErrors, blankToNull, MONEY_PATTERN } from '@/lib/forms'
import { formatMoney, formatNumber } from '@/lib/format'

type Customer = Schemas['CustomerRead']
const PAGE_SIZE = 25
const KEY = ['customers'] as const

const schema = z.object({
  code: z.string().trim().max(20),
  name: z.string().trim().min(1, 'Required').max(150),
  shop_name: z.string().trim().max(150),
  phone: z.string().trim().max(30),
  area: z.string().trim().max(100),
  address: z.string().trim().max(500),
  credit_limit: z.string().trim().regex(MONEY_PATTERN, 'Enter an amount'),
  payment_terms_days: z.string().trim().regex(/^\d{1,3}$/, 'Enter days (0–365)'),
  notes: z.string().trim().max(1000),
  is_active: z.boolean(),
})
type FormValues = z.infer<typeof schema>
const FIELDS = Object.keys(schema.shape)

function CustomerDialog({
  customer,
  open,
  onOpenChange,
}: {
  customer: Customer | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  useEffect(() => {
    if (!open) return
    reset({
      code: customer?.code ?? '',
      name: customer?.name ?? '',
      shop_name: customer?.shop_name ?? '',
      phone: customer?.phone ?? '',
      area: customer?.area ?? '',
      address: customer?.address ?? '',
      credit_limit: customer?.credit_limit ?? '0',
      payment_terms_days: String(customer?.payment_terms_days ?? 0),
      notes: customer?.notes ?? '',
      is_active: customer?.is_active ?? true,
    })
  }, [open, customer, reset])

  const mutation = useMutation({
    mutationFn: (v: FormValues) => {
      const body = {
        name: v.name,
        shop_name: blankToNull(v.shop_name),
        phone: blankToNull(v.phone),
        area: blankToNull(v.area),
        address: blankToNull(v.address),
        credit_limit: v.credit_limit,
        payment_terms_days: Number(v.payment_terms_days),
        notes: blankToNull(v.notes),
      }
      return customer
        ? unwrap(
            api.PATCH('/api/v1/customers/{customer_id}', {
              params: { path: { customer_id: customer.id } },
              body: { ...body, is_active: v.is_active },
            }),
          )
        : unwrap(api.POST('/api/v1/customers', { body: { ...body, code: blankToNull(v.code) } }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY })
      toast.success(customer ? t('common.saved') : t('common.created'))
      onOpenChange(false)
    },
  })

  const onSubmit = handleSubmit(async (v) => {
    try {
      await mutation.mutateAsync(v)
    } catch (error) {
      applyServerErrors(error, setError, FIELDS)
    }
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={customer ? t('customers.editTitle') : t('customers.newTitle')}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="customer-form" loading={isSubmitting}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="customer-form" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        <Field
          label={t('common.code')}
          htmlFor="code"
          hint={customer ? undefined : t('common.autoCode')}
          error={errors.code?.message}
        >
          <Input id="code" className="uppercase" disabled={!!customer} {...register('code')} />
        </Field>
        <Field label={t('customers.name')} htmlFor="name" error={errors.name?.message}>
          <Input id="name" aria-invalid={!!errors.name} {...register('name')} />
        </Field>
        <Field label={t('customers.shopName')} htmlFor="shop_name" optional optionalLabel={t('common.optional')}>
          <Input id="shop_name" {...register('shop_name')} />
        </Field>
        <Field label={t('common.phone')} htmlFor="phone" optional optionalLabel={t('common.optional')}>
          <Input id="phone" type="tel" inputMode="tel" {...register('phone')} />
        </Field>
        <Field label={t('customers.area')} htmlFor="area" optional optionalLabel={t('common.optional')}>
          <Input id="area" {...register('area')} />
        </Field>
        <Field
          label={t('customers.creditLimit')}
          htmlFor="credit_limit"
          hint={t('customers.creditLimitHint')}
          error={errors.credit_limit?.message}
        >
          <Input id="credit_limit" inputMode="decimal" className="num" {...register('credit_limit')} />
        </Field>
        <Field
          label={t('customers.paymentTerms')}
          htmlFor="payment_terms_days"
          error={errors.payment_terms_days?.message}
        >
          <Input id="payment_terms_days" inputMode="numeric" className="num" {...register('payment_terms_days')} />
        </Field>
        <Field label={t('common.address')} htmlFor="address" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
          <Textarea id="address" {...register('address')} />
        </Field>
        <Field label={t('common.notes')} htmlFor="notes" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
          <Textarea id="notes" rows={2} {...register('notes')} />
        </Field>
        {customer && !customer.is_walk_in && <Checkbox label={t('common.active')} {...register('is_active')} />}
      </form>
    </Dialog>
  )
}

export function CustomersPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const canWrite = can('customers:write')
  const [q, setQ] = useState('')
  const [area, setArea] = useState('')
  const [status, setStatus] = useState<'' | 'true' | 'false'>('true')
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<Customer | null>(null)
  const [open, setOpen] = useState(false)

  const query = useQuery({
    queryKey: [...KEY, { q, area, status, page }],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/customers', {
          params: {
            query: {
              q: q || undefined,
              area: area || undefined,
              is_active: status === '' ? undefined : status === 'true',
              page,
              page_size: PAGE_SIZE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const openDialog = (c: Customer | null) => {
    setEditing(c)
    setOpen(true)
  }

  const columns = useMemo<Column<Customer>[]>(
    () => [
      {
        header: t('customers.name'),
        cell: (c) => (
          <div className="min-w-44">
            <div className="flex items-center gap-2 font-medium">
              {c.name}
              {c.is_walk_in && <Badge tone="primary">{t('customers.walkIn')}</Badge>}
            </div>
            <div className="text-xs text-muted">
              {c.code}
              {c.shop_name && ` · ${c.shop_name}`}
            </div>
          </div>
        ),
      },
      { header: t('common.phone'), cell: (row) => row.phone ?? <span className="text-muted">—</span> },
      { header: t('customers.area'), cell: (row) => row.area ?? <span className="text-muted">—</span> },
      {
        header: t('customers.creditLimit'),
        align: 'right',
        cell: (row) =>
          Number(row.credit_limit) > 0 ? (
            formatMoney(row.credit_limit, lang)
          ) : (
            <span className="text-muted">{t('customers.cashOnly')}</span>
          ),
      },
      {
        header: t('customers.paymentTerms'),
        align: 'right',
        cell: (row) =>
          row.payment_terms_days
            ? t('customers.days', { count: row.payment_terms_days, formatted: formatNumber(row.payment_terms_days, lang) })
            : '—',
      },
      {
        header: t('common.status'),
        cell: (row) =>
          row.is_active ? (
            <Badge tone="success">{t('common.active')}</Badge>
          ) : (
            <Badge>{t('common.inactive')}</Badge>
          ),
      },
    ],
    [t, lang],
  )

  return (
    <>
      <PageHeader
        title={t('customers.title')}
        description={t('customers.subtitle')}
        actions={
          canWrite && (
            <Button onClick={() => openDialog(null)}>
              <Plus className="size-4" /> {t('customers.new')}
            </Button>
          )
        }
      />
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <Card>
          <Toolbar>
            <SearchInput
              value={q}
              onChange={(v) => {
                setQ(v)
                setPage(1)
              }}
            />
            <SearchInput
              className="sm:w-48"
              value={area}
              placeholder={t('customers.area')}
              onChange={(v) => {
                setArea(v)
                setPage(1)
              }}
            />
            <Select
              className="w-auto"
              value={status}
              aria-label={t('common.status')}
              onChange={(e) => {
                setStatus(e.target.value as '' | 'true' | 'false')
                setPage(1)
              }}
            >
              <option value="true">{t('common.active')}</option>
              <option value="false">{t('common.inactive')}</option>
              <option value="">{t('common.all')}</option>
            </Select>
          </Toolbar>
          <DataTable
            columns={columns}
            data={query.data?.items}
            loading={query.isPending}
            onRowClick={canWrite ? openDialog : undefined}
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPageChange={setPage} />
        </Card>
      )}
      <CustomerDialog customer={editing} open={open} onOpenChange={setOpen} />
    </>
  )
}
