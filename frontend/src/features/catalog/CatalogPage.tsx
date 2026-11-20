import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { Pencil, Plus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { applyServerErrors, blankToNull } from '@/lib/forms'

import { catalogKeys, useBrands, useCategories, useUoms } from './queries'

type Kind = 'categories' | 'brands' | 'uoms'

interface Row {
  id: number
  code?: string
  name_en: string
  name_bn: string | null
  parent_id?: number | null
  is_active: boolean
}

const schema = z.object({
  code: z.string().trim().max(10),
  name_en: z.string().trim().min(1, 'Required').max(100),
  name_bn: z.string().trim().max(100),
  parent_id: z.string(),
  is_active: z.boolean(),
})
type FormValues = z.infer<typeof schema>
const FIELDS = Object.keys(schema.shape)

async function save(kind: Kind, row: Row | null, v: FormValues): Promise<unknown> {
  const name_bn = blankToNull(v.name_bn)
  if (kind === 'categories') {
    const parent_id = v.parent_id ? Number(v.parent_id) : null
    return row
      ? unwrap(
          api.PATCH('/api/v1/categories/{category_id}', {
            params: { path: { category_id: row.id } },
            body: { name_en: v.name_en, name_bn, parent_id, is_active: v.is_active },
          }),
        )
      : unwrap(api.POST('/api/v1/categories', { body: { name_en: v.name_en, name_bn, parent_id } }))
  }
  if (kind === 'brands') {
    return row
      ? unwrap(
          api.PATCH('/api/v1/brands/{brand_id}', {
            params: { path: { brand_id: row.id } },
            body: { name_en: v.name_en, name_bn, is_active: v.is_active },
          }),
        )
      : unwrap(api.POST('/api/v1/brands', { body: { name_en: v.name_en, name_bn } }))
  }
  return row
    ? unwrap(
        api.PATCH('/api/v1/uoms/{uom_id}', {
          params: { path: { uom_id: row.id } },
          body: { name_en: v.name_en, name_bn, is_active: v.is_active },
        }),
      )
    : unwrap(api.POST('/api/v1/uoms', { body: { code: v.code, name_en: v.name_en, name_bn } }))
}

function EditDialog({
  kind,
  row,
  open,
  onOpenChange,
  categories,
}: {
  kind: Kind
  row: Row | null
  open: boolean
  onOpenChange: (open: boolean) => void
  categories: Schemas['CategoryRead'][]
}) {
  const { t } = useTranslation()
  const { pick } = useLang()
  const queryClient = useQueryClient()
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  useEffect(() => {
    if (open) {
      reset({
        code: row?.code ?? '',
        name_en: row?.name_en ?? '',
        name_bn: row?.name_bn ?? '',
        parent_id: row?.parent_id ? String(row.parent_id) : '',
        is_active: row?.is_active ?? true,
      })
    }
  }, [open, row, reset])

  const mutation = useMutation({
    mutationFn: (v: FormValues) => save(kind, row, v),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: catalogKeys[kind] })
      toast.success(row ? t('common.saved') : t('common.created'))
      onOpenChange(false)
    },
  })

  const onSubmit = handleSubmit(async (v) => {
    if (kind === 'uoms' && !row && !/^[A-Za-z0-9]{1,10}$/.test(v.code)) {
      setError('code', { message: 'Letters and digits only' })
      return
    }
    try {
      await mutation.mutateAsync(v)
    } catch (error) {
      applyServerErrors(error, setError, FIELDS)
    }
  })

  const titles: Record<Kind, [string, string]> = {
    categories: [t('catalog.newCategory'), t('catalog.editCategory')],
    brands: [t('catalog.newBrand'), t('catalog.editBrand')],
    uoms: [t('catalog.newUom'), t('catalog.editUom')],
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={titles[kind][row ? 1 : 0]}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="catalog-form" loading={isSubmitting}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="catalog-form" onSubmit={onSubmit} className="space-y-4" noValidate>
        {kind === 'uoms' && (
          <Field label={t('common.code')} htmlFor="code" error={errors.code?.message}>
            <Input id="code" className="uppercase" disabled={!!row} {...register('code')} />
          </Field>
        )}
        <Field label={t('common.nameEn')} htmlFor="name_en" error={errors.name_en?.message}>
          <Input id="name_en" aria-invalid={!!errors.name_en} {...register('name_en')} />
        </Field>
        <Field label={t('common.nameBn')} htmlFor="name_bn" optional optionalLabel={t('common.optional')}>
          <Input id="name_bn" lang="bn" {...register('name_bn')} />
        </Field>
        {kind === 'categories' && (
          <Field label={t('catalog.parent')} htmlFor="parent_id">
            <Select id="parent_id" {...register('parent_id')}>
              <option value="">{t('catalog.noParent')}</option>
              {categories
                .filter((c) => c.id !== row?.id)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {pick(c.name_en, c.name_bn)}
                  </option>
                ))}
            </Select>
          </Field>
        )}
        {row && <Checkbox label={t('common.active')} {...register('is_active')} />}
      </form>
    </Dialog>
  )
}

export function CatalogPage() {
  const { t } = useTranslation()
  const { pick } = useLang()
  const { can } = useAuth()
  const canWrite = can('catalog:write')
  const [kind, setKind] = useState<Kind>('categories')
  const [editing, setEditing] = useState<Row | null>(null)
  const [open, setOpen] = useState(false)
  const categories = useCategories()
  const brands = useBrands()
  const uoms = useUoms()
  const queries: Record<Kind, UseQueryResult<Row[]>> = { categories, brands, uoms }
  const current = queries[kind]

  const categoryName = useMemo(() => {
    const map = new Map(categories.data?.map((c) => [c.id, pick(c.name_en, c.name_bn)]))
    return (id: number | null | undefined) => (id ? map.get(id) : undefined)
  }, [categories.data, pick])

  const columns = useMemo<Column<Row>[]>(() => {
    const cols: Column<Row>[] = []
    if (kind === 'uoms') {
      cols.push({ header: t('common.code'), cell: (row) => <span className="font-mono">{row.code}</span> })
    }
    cols.push(
      { header: t('common.nameEn'), cell: (row) => <span className="font-medium">{row.name_en}</span> },
      { header: t('common.nameBn'), cell: (row) => row.name_bn ?? <span className="text-muted">—</span> },
    )
    if (kind === 'categories') {
      cols.push({
        header: t('catalog.parent'),
        cell: (row) => categoryName(row.parent_id) ?? <span className="text-muted">—</span>,
      })
    }
    cols.push({
      header: t('common.status'),
      cell: (row) =>
        row.is_active ? (
          <Badge tone="success">{t('common.active')}</Badge>
        ) : (
          <Badge>{t('common.inactive')}</Badge>
        ),
    })
    if (canWrite) {
      cols.push({
        id: 'actions',
        header: '',
        align: 'right',
        cell: (row) => (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('common.edit')}
            onClick={() => {
              setEditing(row)
              setOpen(true)
            }}
          >
            <Pencil className="size-4" />
          </Button>
        ),
      })
    }
    return cols
  }, [kind, t, canWrite, categoryName])

  const newLabel: Record<Kind, string> = {
    categories: t('catalog.newCategory'),
    brands: t('catalog.newBrand'),
    uoms: t('catalog.newUom'),
  }

  return (
    <>
      <PageHeader title={t('catalog.title')} description={t('catalog.subtitle')} />
      <Card>
        <Toolbar>
          <Tabs
            value={kind}
            onChange={setKind}
            items={[
              { value: 'categories', label: t('catalog.categories') },
              { value: 'brands', label: t('catalog.brands') },
              { value: 'uoms', label: t('catalog.uoms') },
            ]}
          />
          <div className="flex-1" />
          {canWrite && (
            <Button
              onClick={() => {
                setEditing(null)
                setOpen(true)
              }}
            >
              <Plus className="size-4" /> {newLabel[kind]}
            </Button>
          )}
        </Toolbar>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : (
          <DataTable columns={columns} data={current.data} loading={current.isPending} />
        )}
      </Card>
      <EditDialog
        kind={kind}
        row={editing}
        open={open}
        onOpenChange={setOpen}
        categories={categories.data ?? []}
      />
    </>
  )
}
