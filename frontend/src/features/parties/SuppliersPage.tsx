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
import { Checkbox, Input, Textarea } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { applyServerErrors, blankToNull } from '@/lib/forms'

type Supplier = Schemas['SupplierRead']
const PAGE_SIZE = 25
const KEY = ['suppliers'] as const

const schema = z.object({
  code: z.string().trim().max(20),
  name: z.string().trim().min(1, 'Required').max(150),
  contact_person: z.string().trim().max(120),
  phone: z.string().trim().max(30),
  email: z.union([z.literal(''), z.email('Enter a valid email')]),
  address: z.string().trim().max(500),
  notes: z.string().trim().max(1000),
  is_active: z.boolean(),
})
type FormValues = z.infer<typeof schema>
const FIELDS = Object.keys(schema.shape)

function SupplierDialog({
  supplier,
  open,
  onOpenChange,
}: {
  supplier: Supplier | null
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
      code: supplier?.code ?? '',
      name: supplier?.name ?? '',
      contact_person: supplier?.contact_person ?? '',
      phone: supplier?.phone ?? '',
      email: supplier?.email ?? '',
      address: supplier?.address ?? '',
      notes: supplier?.notes ?? '',
      is_active: supplier?.is_active ?? true,
    })
  }, [open, supplier, reset])

  const mutation = useMutation({
    mutationFn: (v: FormValues) => {
      const body = {
        name: v.name,
        contact_person: blankToNull(v.contact_person),
        phone: blankToNull(v.phone),
        email: blankToNull(v.email),
        address: blankToNull(v.address),
        notes: blankToNull(v.notes),
      }
      return supplier
        ? unwrap(
            api.PATCH('/api/v1/suppliers/{supplier_id}', {
              params: { path: { supplier_id: supplier.id } },
              body: { ...body, is_active: v.is_active },
            }),
          )
        : unwrap(api.POST('/api/v1/suppliers', { body: { ...body, code: blankToNull(v.code) } }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY })
      toast.success(supplier ? t('common.saved') : t('common.created'))
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
      title={supplier ? t('suppliers.editTitle') : t('suppliers.newTitle')}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="supplier-form" loading={isSubmitting}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="supplier-form" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        <Field
          label={t('common.code')}
          htmlFor="code"
          hint={supplier ? undefined : t('common.autoCode')}
          error={errors.code?.message}
        >
          <Input id="code" className="uppercase" disabled={!!supplier} {...register('code')} />
        </Field>
        <Field label={t('suppliers.name')} htmlFor="name" error={errors.name?.message}>
          <Input id="name" aria-invalid={!!errors.name} {...register('name')} />
        </Field>
        <Field label={t('suppliers.contactPerson')} htmlFor="contact_person" optional optionalLabel={t('common.optional')}>
          <Input id="contact_person" {...register('contact_person')} />
        </Field>
        <Field label={t('common.phone')} htmlFor="phone" optional optionalLabel={t('common.optional')}>
          <Input id="phone" type="tel" {...register('phone')} />
        </Field>
        <Field label={t('common.email')} htmlFor="email" optional optionalLabel={t('common.optional')} error={errors.email?.message}>
          <Input id="email" type="email" aria-invalid={!!errors.email} {...register('email')} />
        </Field>
        <Field label={t('common.address')} htmlFor="address" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
          <Textarea id="address" {...register('address')} />
        </Field>
        <Field label={t('common.notes')} htmlFor="notes" optional optionalLabel={t('common.optional')} className="sm:col-span-2">
          <Textarea id="notes" rows={2} {...register('notes')} />
        </Field>
        {supplier && <Checkbox label={t('common.active')} {...register('is_active')} />}
      </form>
    </Dialog>
  )
}

export function SuppliersPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const canWrite = can('suppliers:write')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<Supplier | null>(null)
  const [open, setOpen] = useState(false)

  const query = useQuery({
    queryKey: [...KEY, { q, page }],
    queryFn: () =>
      unwrap(api.GET('/api/v1/suppliers', { params: { query: { q: q || undefined, page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const openDialog = (s: Supplier | null) => {
    setEditing(s)
    setOpen(true)
  }

  const columns = useMemo<Column<Supplier>[]>(
    () => [
      {
        header: t('suppliers.name'),
        cell: (s) => (
          <div>
            <div className="font-medium">{s.name}</div>
            <div className="text-xs text-muted">{s.code}</div>
          </div>
        ),
      },
      { header: t('suppliers.contactPerson'), cell: (row) => row.contact_person ?? <span className="text-muted">—</span> },
      { header: t('common.phone'), cell: (row) => row.phone ?? <span className="text-muted">—</span> },
      { header: t('common.email'), cell: (row) => row.email ?? <span className="text-muted">—</span> },
      {
        header: t('common.status'),
        cell: (row) =>
          row.is_active ? <Badge tone="success">{t('common.active')}</Badge> : <Badge>{t('common.inactive')}</Badge>,
      },
    ],
    [t],
  )

  return (
    <>
      <PageHeader
        title={t('suppliers.title')}
        description={t('suppliers.subtitle')}
        actions={
          canWrite && (
            <Button onClick={() => openDialog(null)}>
              <Plus className="size-4" /> {t('suppliers.new')}
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
      <SupplierDialog supplier={editing} open={open} onOpenChange={setOpen} />
    </>
  )
}
