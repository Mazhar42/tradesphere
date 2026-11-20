import { describe, expect, it } from 'vitest'

import { formatMoney, formatNumber } from './format'

describe('formatMoney', () => {
  it('uses lakh/crore grouping with the Taka sign', () => {
    expect(formatMoney('1250000.5')).toBe('৳12,50,000.50')
    expect(formatMoney('10000000')).toBe('৳1,00,00,000.00')
  })

  it('renders Bangla digits', () => {
    expect(formatMoney('1250000.5', 'bn')).toBe('৳১২,৫০,০০০.৫০')
  })

  it('keeps precision for large decimal strings', () => {
    expect(formatMoney('9999999999999.99')).toBe('৳99,99,99,99,99,999.99')
  })

  it('shows a dash for empty values', () => {
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney('')).toBe('—')
  })
})

describe('formatNumber', () => {
  it('drops trailing zeros up to 3 decimals', () => {
    expect(formatNumber('48.000')).toBe('48')
    expect(formatNumber('2.500')).toBe('2.5')
  })
})
