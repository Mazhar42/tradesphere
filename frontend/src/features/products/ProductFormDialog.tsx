import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect } from 'react'
import { useFieldArray, useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Checkbox, Input, Select } from '@/components/ui/input'
import { catalogKeys, useBrands, useCategories, useUoms } from '@/features/catalog/queries'
import { useLang } from '@/i18n'
import { applyServerErrors, blankToNull, MONEY_PATTERN, QTY_PATTERN } from '@/lib/forms'
import { formatQty } from '@/lib/units'

type Product = Schemas['ProductRead']

const money = z.string().trim().regex(MONEY_PATTERN, 'Enter an amount like 120 or 120.50')

const baseSchema = z.object({
    sku: z.string().trim().min(1, 'Required').max(50),
    barcode: z.string().trim().max(64),
    name_en: z.string().trim().min(1, 'Required').max(200),
    name_bn: z.string().trim().max(200),
    category_id: z.string(),
    brand_id: z.string(),
    base_uom_id: z.string().min(1, 'Required'),
    dp: money,
    tp: money,
    mrp: money,
    reorder_level: z.string().trim().regex(QTY_PATTERN, 'Enter a quantity'),
    track_batch: z.boolean(),
    track_expiry: z.boolean(),
    uoms: z.array(
      z.object({
        uom_id: z.string().min(1, 'Required'),
        factor_to_base: z
          .string()
          .trim()
          .regex(QTY_PATTERN, 'Enter a number')
          .refine((v) => Number(v) > 1, 'Must be more than 1'),
        is_purchase_default: z.boolean(),
        is_sales_default: z.boolean(),
      }),
    ),
  })

const schema = baseSchema
  .refine((v) => Number(v.mrp) === 0 || Number(v.tp) <= Number(v.mrp), {
    path: ['tp'],
    message: 'TP cannot be above MRP',
  })
  .refine((v) => v.uoms.every((u) => u.uom_id !== v.base_uom_id), {
    path: ['uoms'],
    message: 'The base unit cannot also be listed here',
  })
  .refine((v) => new Set(v.uoms.map((u) => u.uom_id)).size === v.uoms.length, {
    path: ['uoms'],
    message: 'Each unit may appear only once',
  })

type FormValues = z.infer<typeof schema>
const FIELDS = Object.keys(baseSchema.shape)

function toForm(p: Product | null, defaultBaseUomId: string): FormValues {
  return {
    sku: p?.sku ?? '',
    barcode: p?.barcode ?? '',
    name_en: p?.name_en ?? '',
    name_bn: p?.name_bn ?? '',
    category_id: p?.category_id ? String(p.category_id) : '',
    brand_id: p?.brand_id ? String(p.brand_id) : '',
    base_uom_id: p ? String(p.base_uom_id) : defaultBaseUomId,
    dp: p?.dp ?? '0',
    tp: p?.tp ?? '0',
    mrp: p?.mrp ?? '0',
    reorder_level: p ? String(Number(p.reorder_level)) : '0',
    track_batch: p?.track_batch ?? true,
    track_expiry: p?.track_expiry ?? true,
    uoms:
      p?.uoms.map((u) => ({
        uom_id: String(u.uom_id),
        factor_to_base: String(Number(u.factor_to_base)),
        is_purchase_default: u.is_purchase_default,
        is_sales_default: u.is_sales_default,
      })) ?? [],
  }
}

function toPayload(v: FormValues): Schemas['ProductCreate'] {
  return {
    sku: v.sku,
    barcode: blankToNull(v.barcode),
    name_en: v.name_en,
    name_bn: blankToNull(v.name_bn),
    category_id: v.category_id ? Number(v.category_id) : null,
    brand_id: v.brand_id ? Number(v.brand_id) : null,
    base_uom_id: Number(v.base_uom_id),
    dp: v.dp,
    tp: v.tp,
    mrp: v.mrp,
    reorder_level: v.reorder_level,
    track_batch: v.track_batch,
    track_expiry: v.track_expiry,
    uoms: v.uoms.map((u) => ({
      uom_id: Number(u.uom_id),
      factor_to_base: u.factor_to_base,
      is_purchase_default: u.is_purchase_default,
      is_sales_default: u.is_sales_default,
    })),
  }
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  product: Product | null
}

export function ProductFormDialog({ open, onOpenChange, product }: Props) {
  const { t } = useTranslation()
  const { lang, pick } = useLang()
  const queryClient = useQueryClient()
  const categories = useCategories()
  const brands = useBrands()
  const uoms = useUoms()
  const pcsId = String(uoms.data?.find((u) => u.code === 'PCS')?.id ?? '')

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: toForm(product, pcsId),
  })
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = form
  const { fields, append, remove } = useFieldArray({ control, name: 'uoms' })

  useEffect(() => {
    if (open) reset(toForm(product, pcsId))
  }, [open, product, pcsId, reset])

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      product
        ? unwrap(
            api.PATCH('/api/v1/products/{product_id}', {
              params: { path: { product_id: product.id } },
              body: toPayload(values),
            }),
          )
        : unwrap(api.POST('/api/v1/products', { body: toPayload(values) })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: catalogKeys.products })
      toast.success(product ? t('common.saved') : t('common.created'))
      onOpenChange(false)
    },
  })

  const onSubmit = handleSubmit(async (values) => {
    try {
      await mutation.mutateAsync(values)
    } catch (error) {
      applyServerErrors(error, setError, FIELDS)
    }
  })

  // Live example of the unit conversion, e.g. "2 CTN 5 PCS = 53 PCS".
  const watched = useWatch({ control, name: ['base_uom_id', 'uoms', 'dp', 'tp'] })
  const [baseUomId, watchedUoms, dp, tp] = watched
  const uomCode = (id: string) => uoms.data?.find((u) => String(u.id) === id)?.code ?? '?'
  const baseCode = uomCode(baseUomId)
  const validUnits = watchedUoms
    .filter((u) => u.uom_id && QTY_PATTERN.test(u.factor_to_base) && Number(u.factor_to_base) > 1)
    .map((u) => ({ code: uomCode(u.uom_id), factor: u.factor_to_base }))
  let example = ''
  if (validUnits.length > 0) {
    const biggest = [...validUnits].sort((a, b) => Number(b.factor) - Number(a.factor))[0]!
    const sample = String(Number(biggest.factor) * 2 + 5)
    example = `${formatQty(sample, validUnits, baseCode, lang)} = ${sample} ${baseCode}`
  }
  const marginPct =
    MONEY_PATTERN.test(dp) && MONEY_PATTERN.test(tp) && Number(tp) > 0
      ? (((Number(tp) - Number(dp)) / Number(tp)) * 100).toFixed(1)
      : null

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={product ? t('products.editTitle') : t('products.newTitle')}
      size="xl"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="product-form" loading={isSubmitting}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="product-form" onSubmit={onSubmit} className="space-y-6" noValidate>
        <section className="grid gap-4 sm:grid-cols-2">
          <h3 className="text-sm font-semibold text-muted sm:col-span-2">{t('products.details')}</h3>
          <Field label={t('products.sku')} htmlFor="sku" error={errors.sku?.message}>
            <Input id="sku" className="uppercase" aria-invalid={!!errors.sku} {...register('sku')} />
          </Field>
          <Field
            label={t('products.barcode')}
            htmlFor="barcode"
            optional
            optionalLabel={t('common.optional')}
            error={errors.barcode?.message}
          >
            <Input id="barcode" inputMode="numeric" {...register('barcode')} />
          </Field>
          <Field label={t('common.nameEn')} htmlFor="name_en" error={errors.name_en?.message}>
            <Input id="name_en" aria-invalid={!!errors.name_en} {...register('name_en')} />
          </Field>
          <Field
            label={t('common.nameBn')}
            htmlFor="name_bn"
            optional
            optionalLabel={t('common.optional')}
          >
            <Input id="name_bn" lang="bn" {...register('name_bn')} />
          </Field>
          <Field label={t('products.category')} htmlFor="category_id">
            <Select id="category_id" {...register('category_id')}>
              <option value="">{t('common.none')}</option>
              {categories.data
                ?.filter((c) => c.is_active || String(c.id) === form.getValues('category_id'))
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {pick(c.name_en, c.name_bn)}
                  </option>
                ))}
            </Select>
          </Field>
          <Field label={t('products.brand')} htmlFor="brand_id">
            <Select id="brand_id" {...register('brand_id')}>
              <option value="">{t('common.none')}</option>
              {brands.data
                ?.filter((b) => b.is_active || String(b.id) === form.getValues('brand_id'))
                .map((b) => (
                  <option key={b.id} value={b.id}>
                    {pick(b.name_en, b.name_bn)}
                  </option>
                ))}
            </Select>
          </Field>
        </section>

        <section className="grid gap-4 sm:grid-cols-3">
          <div className="flex items-baseline justify-between sm:col-span-3">
            <h3 className="text-sm font-semibold text-muted">
              {t('products.pricing')}{' '}
              <span className="font-normal">({t('products.perBase', { unit: baseCode })})</span>
            </h3>
            {marginPct !== null && (
              <span className="text-xs text-muted">{t('products.margin', { value: marginPct })}</span>
            )}
          </div>
          <Field label={t('products.dpLong')} htmlFor="dp" error={errors.dp?.message}>
            <Input id="dp" inputMode="decimal" className="num" aria-invalid={!!errors.dp} {...register('dp')} />
          </Field>
          <Field label={t('products.tpLong')} htmlFor="tp" error={errors.tp?.message}>
            <Input id="tp" inputMode="decimal" className="num" aria-invalid={!!errors.tp} {...register('tp')} />
          </Field>
          <Field label={t('products.mrpLong')} htmlFor="mrp" error={errors.mrp?.message}>
            <Input id="mrp" inputMode="decimal" className="num" aria-invalid={!!errors.mrp} {...register('mrp')} />
          </Field>
        </section>

        <section className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field
              label={t('products.baseUnit')}
              htmlFor="base_uom_id"
              hint={t('products.baseUnitHint')}
              error={errors.base_uom_id?.message}
            >
              <Select id="base_uom_id" {...register('base_uom_id')}>
                {uoms.data?.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.code} — {pick(u.name_en, u.name_bn)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="rounded-lg border border-border">
            <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
              <div>
                <p className="text-sm font-medium">{t('products.conversions')}</p>
                <p className="text-xs text-muted">{t('products.conversionsHint')}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  append({
                    uom_id: '',
                    factor_to_base: '',
                    is_purchase_default: fields.length === 0,
                    is_sales_default: false,
                  })
                }
              >
                <Plus className="size-4" /> {t('products.addConversion')}
              </Button>
            </div>
            <div className="divide-y divide-border">
              {fields.map((field, index) => (
                <div key={field.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-muted">1</span>
                    <Select
                      aria-label={t('products.units')}
                      className="w-28"
                      aria-invalid={!!errors.uoms?.[index]?.uom_id}
                      {...register(`uoms.${index}.uom_id`)}
                    >
                      <option value="">—</option>
                      {uoms.data
                        ?.filter((u) => String(u.id) !== baseUomId)
                        .map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.code}
                          </option>
                        ))}
                    </Select>
                    <span className="text-muted">=</span>
                    <Input
                      aria-label={t('products.factor')}
                      inputMode="decimal"
                      className="num w-24"
                      aria-invalid={!!errors.uoms?.[index]?.factor_to_base}
                      {...register(`uoms.${index}.factor_to_base`)}
                    />
                    <span className="font-medium">{baseCode}</span>
                  </div>
                  <div className="flex flex-1 flex-wrap items-center gap-4 pt-2">
                    <Checkbox label={t('products.purchaseDefault')} {...register(`uoms.${index}.is_purchase_default`)} />
                    <Checkbox label={t('products.salesDefault')} {...register(`uoms.${index}.is_sales_default`)} />
                  </div>
                  <Button variant="ghost" size="icon" onClick={() => remove(index)} aria-label="Remove">
                    <Trash2 className="size-4 text-danger" />
                  </Button>
                  {errors.uoms?.[index]?.factor_to_base && (
                    <p className="w-full text-xs text-danger">{errors.uoms[index].factor_to_base.message}</p>
                  )}
                </div>
              ))}
            </div>
            {(errors.uoms?.message || errors.uoms?.root?.message) && (
              <p className="px-4 py-2 text-xs text-danger">{errors.uoms.message ?? errors.uoms.root?.message}</p>
            )}
            {example && (
              <p className="border-t border-border bg-surface-muted/50 px-4 py-2 text-xs text-muted">
                {t('products.example', { example })}
              </p>
            )}
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-3">
          <h3 className="text-sm font-semibold text-muted sm:col-span-3">{t('products.tracking')}</h3>
          <Field
            label={t('products.reorderLevel')}
            htmlFor="reorder_level"
            hint={t('products.reorderLevelHint')}
            error={errors.reorder_level?.message}
          >
            <Input id="reorder_level" inputMode="decimal" className="num" {...register('reorder_level')} />
          </Field>
          <div className="flex flex-col justify-center gap-3 sm:col-span-2">
            <Checkbox label={t('products.trackBatch')} {...register('track_batch')} />
            <Checkbox label={t('products.trackExpiry')} {...register('track_expiry')} />
          </div>
        </section>
      </form>
    </Dialog>
  )
}
