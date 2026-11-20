/**
 * Carton / box / piece conversion for display and entry.
 * Mirrors backend `app/modules/catalog/units.py`. Uses fixed-point BigInt
 * arithmetic on thousandths (quantities have 3 decimals) - no float error.
 */

import { formatNumber, type Lang } from './format'

export interface UnitFactor {
  code: string
  factor: string // base units per one of this unit, e.g. "24.000"
}

export interface QtyPart {
  code: string
  qty: string
}

const SCALE = 1000n

export function toMilli(value: string): bigint {
  const trimmed = value.trim()
  const negative = trimmed.startsWith('-')
  const [whole = '0', frac = ''] = trimmed.replace(/^[-+]/, '').split('.')
  if (!/^\d*$/.test(whole) || !/^\d*$/.test(frac) || frac.length > 3) {
    throw new Error(`Invalid quantity: ${value}`)
  }
  const milli = BigInt(whole || '0') * SCALE + BigInt(frac.padEnd(3, '0') || '0')
  return negative ? -milli : milli
}

export function fromMilli(milli: bigint): string {
  const negative = milli < 0n
  const abs = negative ? -milli : milli
  const whole = abs / SCALE
  const frac = (abs % SCALE).toString().padStart(3, '0').replace(/0+$/, '')
  return `${negative ? '-' : ''}${whole}${frac ? `.${frac}` : ''}`
}

/** [2 CTN, 5 PCS] with CTN = 24 -> "53". */
export function toBase(parts: QtyPart[], units: UnitFactor[], baseCode: string): string {
  const factors = new Map(units.map((u) => [u.code, toMilli(u.factor)]))
  factors.set(baseCode, SCALE)
  let total = 0n
  for (const part of parts) {
    const factor = factors.get(part.code)
    if (factor === undefined) throw new Error(`Unit ${part.code} is not configured`)
    total += (toMilli(part.qty) * factor) / SCALE
  }
  return fromMilli(total)
}

/** "53" with CTN = 24 -> [2 CTN, 5 PCS]. Largest unit first, zero parts omitted. */
export function splitQty(baseQty: string, units: UnitFactor[], baseCode: string): QtyPart[] {
  const total = toMilli(baseQty)
  let remaining = total < 0n ? -total : total
  const parts: QtyPart[] = []
  const sorted = [...units].sort((a, b) => Number(toMilli(b.factor) - toMilli(a.factor)))
  for (const unit of sorted) {
    const factor = toMilli(unit.factor)
    const whole = remaining / factor
    if (whole > 0n) {
      parts.push({ code: unit.code, qty: whole.toString() })
      remaining -= whole * factor
    }
  }
  if (remaining > 0n || parts.length === 0) {
    parts.push({ code: baseCode, qty: fromMilli(remaining) })
  }
  return total < 0n ? parts.map((p) => ({ ...p, qty: `-${p.qty}` })) : parts
}

/** "53" -> "2 CTN 5 PCS" (Bangla digits when lang = "bn"). */
export function formatQty(
  baseQty: string,
  units: UnitFactor[],
  baseCode: string,
  lang: Lang = 'en',
): string {
  return splitQty(baseQty, units, baseCode)
    .map((p) => `${formatNumber(p.qty, lang)} ${p.code}`)
    .join(' ')
}
