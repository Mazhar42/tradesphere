import { describe, expect, it } from 'vitest'

import { cmpMoney, lineValue, pricePerUnit, subMoney, sumMoney } from './decimal'

describe('decimal helpers', () => {
  it('multiplies quantity by price exactly, rounding half up to 2dp', () => {
    expect(lineValue('10000', '100')).toBe('1000000.00')
    expect(lineValue('3', '33.33')).toBe('99.99')
    expect(lineValue('240', '20.8333')).toBe('4999.99')
    expect(lineValue('1', '0.005')).toBe('0.01')
    expect(lineValue('', '5')).toBe('0.00')
  })

  it('adds and subtracts money without float error', () => {
    expect(sumMoney(['0.10', '0.20'])).toBe('0.30')
    expect(subMoney('1000000.00', '800000.00')).toBe('200000.00')
    expect(cmpMoney('10.00', '9.99')).toBe(1)
    expect(cmpMoney('10.00', '10.00')).toBe(0)
  })

  it('converts a base price to a per-unit price', () => {
    expect(pricePerUnit('21.5', '24')).toBe('516.0000')
    expect(pricePerUnit('20.8333', '24')).toBe('499.9992')
  })
})
