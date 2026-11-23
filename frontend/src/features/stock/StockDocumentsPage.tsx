import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { BatchSelect } from '@/components/pickers/BatchSelect'
import { ProductPicker } from '@/components/pickers/pickers'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { DocBadge } from '@/features/sales/shared'
import { useLang } from '@/i18n'
import { formatDate, formatNumber } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'

type Product = Schemas['ProductRead']
type Location = 'SELLABLE' | 'DAMAGED' | 'EXPIRED'
type Tab = 'adjustments' | 'transfers'
const LOCATIONS: Location[] = ['SELLABLE', 'DAMAGED', 'EXPIRED']
const REASONS = ['count', 'damage', 'expiry', 'found', 'other'] as const
const SIGNED_QTY = /^-?\d{1,15}(\.\d{1,3})?$/
const QTY = /^\d{1,15}(\.\d{1,3})?$/
const PAGE_SIZE = 25

interface Line {
  key: number
  product: Product | null
  batchId: string
  location: Location
  qty: string
}
let lineKey = 0
const emptyLine = (): Line => ({ key: ++lineKey, product: null, batchId: '', location: 'SELLABLE', qty: '' })

function LinesEditor({
  lines,
  setLines,
  showLocation,
  qtyLabel,
}: {
  lines: Line[]
  setLines: (fn: (rows: Line[]) => Line[]) => void
  showLocation: boolean
  qtyLabel: string
}) {
  const { t } = useTranslation()
  const update = (key: number, patch: Partial<Line>) => setLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  return (
    <div className="space-y-3">
      {lines.map((line, index) => (
        <div key={line.key} className="grid gap-2 rounded-lg border border-border p-3 md:grid-cols-12">
          <div className="md:col-span-5">
            <Field label={`${t('sales.product')} ${index + 1}`}>
              <ProductPicker value={line.product} onChange={(p) => update(line.key, { product: p, batchId: '' })} />
            </Field>
          </div>
          <div className="md:col-span-3">
            <Field label={t('returns.batch')}>
              <BatchSelect productId={line.product?.id ?? null} value={line.batchId} disabled={!line.product?.track_batch} onChange={(v) => update(line.key, { batchId: v })} />
            </Field>
          </div>
          {showLocation && (
            <div className="md:col-span-2">
              <Field label={t('stock.location')} htmlFor={`l-${line.key}`}>
                <Select id={`l-${line.key}`} value={line.location} onChange={(e) => update(line.key, { location: e.target.value as Location })}>
                  {LOCATIONS.map((l) => (
                    <option key={l} value={l}>
                      {t(`stock.locations.${l}`)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          )}
          <div className="md:col-span-2">
            <Field label={qtyLabel} htmlFor={`q-${line.key}`}>
              <Input id={`q-${line.key}`} inputMode="decimal" className="num" value={line.qty} onChange={(e) => update(line.key, { qty: e.target.value })} />
            </Field>
          </div>
          <div className="flex items-end md:col-span-12">
            <Button variant="ghost" size="sm" disabled={lines.length === 1} onClick={() => setLines((rows) => rows.filter((r) => r.key !== line.key))}>
              <Trash2 className="size-4 text-danger" /> {t('common.remove')}
            </Button>
          </div>
        </div>
      ))}
      <Button variant="outline" onClick={() => setLines((rows) => [...rows, emptyLine()])}>
        <Plus className="size-4" /> {t('sales.addItem')}
      </Button>
    </div>
  )
}

function validate(lines: Line[], pattern: RegExp, t: (k: string) => string): Line[] {
  const used = lines.filter((l) => l.product || l.qty)
  if (used.length === 0) throw new Error(t('sales.addItem'))
  for (const l of used) {
    if (!l.product) throw new Error(t('sales.chooseProduct'))
    if (!pattern.test(l.qty) || Number(l.qty) === 0) throw new Error(t('sales.enterQty'))
    if (l.product.track_batch && !l.batchId) throw new Error(t('returns.chooseBatch'))
  }
  return used
}

function AdjustmentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const [reason, setReason] = useState<(typeof REASONS)[number]>('count')
  const [lines, setLines] = useState<Line[]>(() => [emptyLine()])
  const [savedId, setSavedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setReason('count')
      setLines([emptyLine()])
      setSavedId(null)
      setError(null)
    }
  }

  const save = useMutation({
    mutationFn: async () => {
      const used = validate(lines, SIGNED_QTY, t)
      const body = {
        reason,
        items: used.map((l) => ({
          product_id: l.product!.id,
          batch_id: l.batchId ? Number(l.batchId) : null,
          location: l.location,
          qty_delta: l.qty,
        })),
      }
      const draftId = savedId ?? (await unwrap(api.POST('/api/v1/stock-adjustments', { body }))).id
      setSavedId(draftId)
      return unwrap(
        api.POST('/api/v1/stock-adjustments/{adjustment_id}/post', {
          params: { path: { adjustment_id: draftId }, header: { 'Idempotency-Key': idem.current() } },
        }),
      )
    },
    onSuccess: (doc) => {
      idem.settle()
      toast.success(t('stockDocs.posted', { no: doc.doc_no }))
      for (const key of [['stock-adjustments'], ['stock'], ['journal'], ['trial-balance']]) void queryClient.invalidateQueries({ queryKey: key })
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
      size="xl"
      title={t('stockDocs.newAdjustment')}
      description={t('stockDocs.adjustmentHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            {t('stockDocs.postAdjustment')}
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
        <div className="max-w-xs">
          <Field label={t('stockDocs.reason')} htmlFor="a-reason" hint={reason === 'damage' || reason === 'expiry' ? t('stockDocs.writeOffNote') : undefined}>
            <Select id="a-reason" value={reason} onChange={(e) => setReason(e.target.value as (typeof REASONS)[number])}>
              {REASONS.map((r) => (
                <option key={r} value={r}>
                  {t(`stockDocs.reasons.${r}`)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <LinesEditor lines={lines} setLines={setLines} showLocation qtyLabel={t('stockDocs.qtyChange')} />
      </div>
    </Dialog>
  )
}

function TransferDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  const [from, setFrom] = useState<Location>('SELLABLE')
  const [to, setTo] = useState<Location>('EXPIRED')
  const [lines, setLines] = useState<Line[]>(() => [emptyLine()])
  const [savedId, setSavedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setFrom('SELLABLE')
      setTo('EXPIRED')
      setLines([emptyLine()])
      setSavedId(null)
      setError(null)
    }
  }

  const save = useMutation({
    mutationFn: async () => {
      if (from === to) throw new Error(t('stockDocs.differentLocations'))
      const used = validate(lines, QTY, t)
      const body = {
        from_location: from,
        to_location: to,
        items: used.map((l) => ({ product_id: l.product!.id, batch_id: l.batchId ? Number(l.batchId) : null, qty_base: l.qty })),
      }
      const draftId = savedId ?? (await unwrap(api.POST('/api/v1/stock-transfers', { body }))).id
      setSavedId(draftId)
      return unwrap(
        api.POST('/api/v1/stock-transfers/{transfer_id}/post', {
          params: { path: { transfer_id: draftId }, header: { 'Idempotency-Key': idem.current() } },
        }),
      )
    },
    onSuccess: (doc) => {
      idem.settle()
      toast.success(t('stockDocs.posted', { no: doc.doc_no }))
      for (const key of [['stock-transfers'], ['stock']]) void queryClient.invalidateQueries({ queryKey: key })
      onOpenChange(false)
    },
    onError: (e) => {
      idem.settle(e)
      setError(e.message)
    },
  })

  const locationSelect = (id: string, value: Location, set: (l: Location) => void, label: string) => (
    <Field label={label} htmlFor={id}>
      <Select id={id} value={value} onChange={(e) => set(e.target.value as Location)}>
        {LOCATIONS.map((l) => (
          <option key={l} value={l}>
            {t(`stock.locations.${l}`)}
          </option>
        ))}
      </Select>
    </Field>
  )

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="xl"
      title={t('stockDocs.newTransfer')}
      description={t('stockDocs.transferHint')}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            {t('stockDocs.postTransfer')}
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
        <div className="grid max-w-lg gap-4 sm:grid-cols-2">
          {locationSelect('t-from', from, setFrom, t('stockDocs.from'))}
          {locationSelect('t-to', to, setTo, t('stockDocs.to'))}
        </div>
        <LinesEditor lines={lines} setLines={setLines} showLocation={false} qtyLabel={t('sales.qty')} />
      </div>
    </Dialog>
  )
}

export function StockDocumentsPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const [tab, setTab] = useState<Tab>('adjustments')
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState(false)
  const canAdjust = can('inventory:adjust')

  const adjustments = useQuery({
    queryKey: ['stock-adjustments', page],
    enabled: tab === 'adjustments',
    queryFn: () => unwrap(api.GET('/api/v1/stock-adjustments', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })
  const transfers = useQuery({
    queryKey: ['stock-transfers', page],
    enabled: tab === 'transfers',
    queryFn: () => unwrap(api.GET('/api/v1/stock-transfers', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const adjustmentColumns = useMemo<Column<Schemas['AdjustmentRead']>[]>(
    () => [
      {
        header: t('stockDocs.document'),
        cell: (d) => (
          <div>
            <div className="font-medium">{d.doc_no ?? t('sales.draft')}</div>
            <div className="text-xs text-muted">{formatDate(d.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('stockDocs.reason'), cell: (d) => t(`stockDocs.reasons.${d.reason}`, d.reason) },
      { header: t('common.status'), cell: (d) => <DocBadge status={d.status} /> },
      {
        header: t('sales.items'),
        cell: (d) => (
          <div className="space-y-0.5 text-xs">
            {d.items.map((i) => (
              <div key={i.id}>
                {i.sku} · {t(`stock.locations.${i.location}`, i.location)} ·{' '}
                <span className={Number(i.qty_delta) < 0 ? 'text-danger' : 'text-success'}>
                  {Number(i.qty_delta) > 0 ? '+' : ''}
                  {formatNumber(i.qty_delta, lang)}
                </span>
              </div>
            ))}
          </div>
        ),
      },
    ],
    [t, lang],
  )
  const transferColumns = useMemo<Column<Schemas['TransferRead']>[]>(
    () => [
      {
        header: t('stockDocs.document'),
        cell: (d) => (
          <div>
            <div className="font-medium">{d.doc_no ?? t('sales.draft')}</div>
            <div className="text-xs text-muted">{formatDate(d.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('stockDocs.from'), cell: (d) => t(`stock.locations.${d.from_location}`, d.from_location) },
      { header: t('stockDocs.to'), cell: (d) => t(`stock.locations.${d.to_location}`, d.to_location) },
      { header: t('common.status'), cell: (d) => <DocBadge status={d.status} /> },
      {
        header: t('sales.items'),
        cell: (d) => (
          <div className="space-y-0.5 text-xs">
            {d.items.map((i) => (
              <div key={i.id}>
                {i.sku} · {formatNumber(i.qty_base, lang)} {i.base_uom_code}
              </div>
            ))}
          </div>
        ),
      },
    ],
    [t, lang],
  )

  const current = tab === 'adjustments' ? adjustments : transfers

  return (
    <>
      <PageHeader
        title={t('stockDocs.title')}
        description={t('stockDocs.subtitle')}
        actions={
          canAdjust && (
            <Button onClick={() => setDialog(true)}>
              <Plus className="size-4" /> {tab === 'adjustments' ? t('stockDocs.newAdjustment') : t('stockDocs.newTransfer')}
            </Button>
          )
        }
      />
      <Card>
        <Toolbar>
          <Tabs
            value={tab}
            onChange={(v) => {
              setTab(v)
              setPage(1)
            }}
            items={[
              { value: 'adjustments' as const, label: t('stockDocs.adjustments') },
              { value: 'transfers' as const, label: t('stockDocs.transfers') },
            ]}
          />
        </Toolbar>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : tab === 'adjustments' ? (
          <DataTable columns={adjustmentColumns} data={adjustments.data?.items} loading={adjustments.isPending} />
        ) : (
          <DataTable columns={transferColumns} data={transfers.data?.items} loading={transfers.isPending} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={current.data?.total ?? 0} onPageChange={setPage} />
      </Card>
      <AdjustmentDialog open={dialog && tab === 'adjustments'} onOpenChange={setDialog} />
      <TransferDialog open={dialog && tab === 'transfers'} onOpenChange={setDialog} />
    </>
  )
}
