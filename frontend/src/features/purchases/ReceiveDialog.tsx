import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { ApiError, api, type Schemas, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { QTY_PATTERN } from '@/lib/forms'
import { blankToNull } from '@/lib/forms'
import { useIdempotencyKey } from '@/lib/idempotency'
import { fromMilli, toMilli } from '@/lib/units'

import { purchaseKeys, useQtyFormatter } from './hooks'

type PO = Schemas['PODetail']
type Item = Schemas['POItemRead']

interface Line {
  key: number
  itemId: number
  unit: 'entry' | 'base'
  received: string
  free: string
  damaged: string
  batchNo: string
  mfg: string
  expiry: string
}

let key = 0
const newLine = (itemId: number): Line => ({
  key: ++key,
  itemId,
  unit: 'entry',
  received: '',
  free: '',
  damaged: '',
  batchNo: '',
  mfg: '',
  expiry: '',
})

/** Quantity typed in the chosen unit -> base units (exact, fixed point). */
function toBaseQty(qty: string, unit: 'entry' | 'base', item: Item): string {
  if (!QTY_PATTERN.test(qty)) return '0'
  const factor = unit === 'entry' ? toMilli(item.factor) : 1000n
  return fromMilli((toMilli(qty) * factor) / 1000n)
}

export function ReceiveDialog({ po, open, onOpenChange }: { po: PO; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const { pick } = useLang()
  const fmtQty = useQtyFormatter()
  const queryClient = useQueryClient()
  const idem = useIdempotencyKey()
  // The GRN drafted by the first attempt, reused on retries so a retry never creates a second one.
  const [draftId, setDraftId] = useState<number | null>(null)

  const pending = po.items.filter((i) => Number(i.remaining_qty_base) > 0)
  const [lines, setLines] = useState<Line[]>([])
  const [date, setDate] = useState('')
  const [challan, setChallan] = useState('')
  const [vehicle, setVehicle] = useState('')
  const [error, setError] = useState<string | null>(null)
  // Reset the form whenever the dialog opens (or for another PO), comparing during render.
  const session = open ? po.id : null
  const [openedFor, setOpenedFor] = useState<number | null>(session)
  if (session !== openedFor) {
    setOpenedFor(session)
    if (session !== null) {
      setLines(pending.map((i) => newLine(i.id)))
      setDate('')
      setChallan('')
      setVehicle('')
      setError(null)
      setDraftId(null)
    }
  }

  const patch = (k: number, change: Partial<Line>) => setLines((rows) => rows.map((r) => (r.key === k ? { ...r, ...change } : r)))
  const itemOf = (line: Line) => po.items.find((i) => i.id === line.itemId)!

  const validate = (active: Line[]): string | null => {
    if (active.length === 0) return t('receive.enterSomething')
    const taken = new Map<number, bigint>()
    for (const line of active) {
      const item = itemOf(line)
      const received = toMilli(toBaseQty(line.received, line.unit, item))
      const damaged = toMilli(toBaseQty(line.damaged, line.unit, item))
      if (damaged > received) return t('receive.damagedTooMany', { name: item.sku })
      taken.set(item.id, (taken.get(item.id) ?? 0n) + received)
      if (taken.get(item.id)! > toMilli(item.remaining_qty_base)) return t('receive.tooMany', { name: item.sku })
      if (item.track_batch && !line.batchNo.trim()) return t('receive.batchRequired', { name: item.sku })
      if (item.track_batch && item.track_expiry && !line.expiry) return t('receive.expiryRequired', { name: item.sku })
    }
    return null
  }

  const submit = useMutation({
    mutationFn: async () => {
      const active = lines.filter((l) => Number(l.received || 0) > 0 || Number(l.free || 0) > 0)
      const problem = validate(active)
      if (problem) throw new Error(problem)
      let id = draftId
      if (id === null) {
        const grn = await unwrap(
          api.POST('/api/v1/goods-receipts', {
            body: {
              po_id: po.id,
              business_date: date || null,
              challan_no: blankToNull(challan),
              vehicle_no: blankToNull(vehicle),
              items: active.map((l) => {
                const item = itemOf(l)
                return {
                  po_item_id: l.itemId,
                  received_qty_base: toBaseQty(l.received, l.unit, item),
                  free_qty_base: toBaseQty(l.free, l.unit, item),
                  damaged_qty_base: toBaseQty(l.damaged, l.unit, item),
                  batch_no: blankToNull(l.batchNo),
                  mfg_date: l.mfg || null,
                  expiry_date: l.expiry || null,
                }
              }),
            },
          }),
        )
        id = grn.id
        setDraftId(id)
      }
      try {
        return await unwrap(
          api.POST('/api/v1/goods-receipts/{grn_id}/post', {
            params: { path: { grn_id: id }, header: { 'Idempotency-Key': idem.current() } },
          }),
        )
      } catch (e) {
        // A definitive refusal leaves nothing posted: remove the unused draft so it doesn't linger.
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
          await api.DELETE('/api/v1/goods-receipts/{grn_id}', { params: { path: { grn_id: id } } })
          setDraftId(null)
        }
        throw e
      }
    },
    onSuccess: (grn) => {
      idem.settle()
      toast.success(t('receive.posted', { no: grn.doc_no }))
      void queryClient.invalidateQueries({ queryKey: ['purchase-order', po.id] })
      void queryClient.invalidateQueries({ queryKey: purchaseKeys.orders })
      void queryClient.invalidateQueries({ queryKey: purchaseKeys.receipts })
      void queryClient.invalidateQueries({ queryKey: ['stock'] })
      void queryClient.invalidateQueries({ queryKey: ['supplier-summary'] })
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
      title={t('receive.title', { no: po.doc_no ?? '' })}
      description={t('receive.hint')}
      size="xl"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button loading={submit.isPending} onClick={() => submit.mutate()}>
            {t('receive.post')}
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
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t('receive.date')} htmlFor="r-date" hint={t('purchases.defaultsToday')}>
            <Input id="r-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('receive.challan')} htmlFor="r-challan" optional optionalLabel={t('common.optional')}>
            <Input id="r-challan" value={challan} onChange={(e) => setChallan(e.target.value)} />
          </Field>
          <Field label={t('receive.vehicle')} htmlFor="r-vehicle" optional optionalLabel={t('common.optional')}>
            <Input id="r-vehicle" value={vehicle} onChange={(e) => setVehicle(e.target.value)} />
          </Field>
        </div>

        <div className="divide-y divide-border rounded-lg border border-border">
          {lines.map((line) => {
            const item = itemOf(line)
            const showUnit = Number(item.factor) > 1
            const sameItem = lines.filter((l) => l.itemId === line.itemId).length
            return (
              <div key={line.key} className="space-y-3 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div>
                    <p className="font-medium">{pick(item.name_en, item.name_bn)}</p>
                    <p className="text-xs text-muted">
                      {item.sku} · {t('receive.remaining')}:{' '}
                      <span className="font-medium text-foreground">
                        {fmtQty(item.remaining_qty_base, item.uom_code, item.factor, item.base_uom_code)}
                      </span>
                    </p>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        setLines((rows) => {
                          const at = rows.findIndex((r) => r.key === line.key)
                          return [...rows.slice(0, at + 1), newLine(line.itemId), ...rows.slice(at + 1)]
                        })
                      }
                    >
                      <Plus className="size-4" /> {t('receive.splitBatch')}
                    </Button>
                    {sameItem > 1 && (
                      <Button variant="ghost" size="icon" aria-label={t('common.remove')} onClick={() => setLines((rows) => rows.filter((r) => r.key !== line.key))}>
                        <Trash2 className="size-4 text-danger" />
                      </Button>
                    )}
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
                  {showUnit && (
                    <Field label={t('purchases.unit')}>
                      <Select value={line.unit} onChange={(e) => patch(line.key, { unit: e.target.value as 'entry' | 'base' })}>
                        <option value="entry">{item.uom_code}</option>
                        <option value="base">{item.base_uom_code}</option>
                      </Select>
                    </Field>
                  )}
                  <Field label={t('receive.received')}>
                    <Input inputMode="decimal" className="num" value={line.received} onChange={(e) => patch(line.key, { received: e.target.value })} />
                  </Field>
                  <Field label={t('receive.free')}>
                    <Input inputMode="decimal" className="num" value={line.free} onChange={(e) => patch(line.key, { free: e.target.value })} />
                  </Field>
                  <Field label={t('receive.damaged')}>
                    <Input inputMode="decimal" className="num" value={line.damaged} onChange={(e) => patch(line.key, { damaged: e.target.value })} />
                  </Field>
                  {item.track_batch && (
                    <Field label={t('receive.batchNo')}>
                      <Input value={line.batchNo} onChange={(e) => patch(line.key, { batchNo: e.target.value })} />
                    </Field>
                  )}
                  {item.track_batch && item.track_expiry && (
                    <Field label={t('receive.expiry')}>
                      <Input type="date" value={line.expiry} onChange={(e) => patch(line.key, { expiry: e.target.value })} />
                    </Field>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </Dialog>
  )
}
