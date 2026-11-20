import { describe, expect, it } from 'vitest'

import { formatQty, fromMilli, splitQty, toBase, toMilli } from './units'

const UNITS = [
  { code: 'CTN', factor: '24.000' },
  { code: 'BOX', factor: '12' },
]

describe('fixed-point helpers', () => {
  it('round-trips quantities', () => {
    expect(toMilli('53')).toBe(53000n)
    expect(toMilli('0.5')).toBe(500n)
    expect(fromMilli(53500n)).toBe('53.5')
    expect(fromMilli(-1250n)).toBe('-1.25')
  })

  it('rejects more than 3 decimals', () => {
    expect(() => toMilli('1.2345')).toThrow()
  })
})

describe('unit conversion', () => {
  it('converts mixed units to base', () => {
    expect(toBase([{ code: 'CTN', qty: '2' }, { code: 'PCS', qty: '5' }], UNITS, 'PCS')).toBe('53')
  })

  it('splits base quantity into the largest units first', () => {
    expect(splitQty('53', UNITS, 'PCS')).toEqual([
      { code: 'CTN', qty: '2' },
      { code: 'PCS', qty: '5' },
    ])
    expect(splitQty('36', UNITS, 'PCS')).toEqual([
      { code: 'CTN', qty: '1' },
      { code: 'BOX', qty: '1' },
    ])
    expect(splitQty('0', UNITS, 'PCS')).toEqual([{ code: 'PCS', qty: '0' }])
  })

  it('matches the backend for negative quantities', () => {
    expect(splitQty('-30', UNITS, 'PCS')).toEqual([
      { code: 'CTN', qty: '-1' },
      { code: 'PCS', qty: '-6' },
    ])
  })

  it('formats for display in both languages', () => {
    expect(formatQty('53', UNITS, 'PCS')).toBe('2 CTN 5 PCS')
    expect(formatQty('53', UNITS, 'PCS', 'bn')).toBe('২ CTN ৫ PCS')
  })
})
