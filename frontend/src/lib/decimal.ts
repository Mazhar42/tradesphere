/**
 * Exact decimal arithmetic for *display-only* previews (e.g. a line total while typing).
 * The server remains the authority for every stored amount.
 */

const parse = (value: string, scale: number): bigint => {
  const trimmed = value.trim()
  if (!/^-?\d*(\.\d*)?$/.test(trimmed) || trimmed === '' || trimmed === '-' || trimmed === '.') return 0n
  const negative = trimmed.startsWith('-')
  const [whole = '0', frac = ''] = trimmed.replace('-', '').split('.')
  const padded = (frac + '0'.repeat(scale)).slice(0, scale)
  const result = BigInt(whole || '0') * 10n ** BigInt(scale) + BigInt(padded || '0')
  return negative ? -result : result
}

const format = (value: bigint, scale: number): string => {
  const negative = value < 0n
  const abs = negative ? -value : value
  const base = 10n ** BigInt(scale)
  const whole = abs / base
  const frac = (abs % base).toString().padStart(scale, '0')
  return `${negative ? '-' : ''}${whole}${scale > 0 ? `.${frac}` : ''}`
}

/** qty (up to 3 dp) x price (up to 4 dp) -> money with 2 dp, rounded half up. */
export function lineValue(qty: string, price: string): string {
  const product = parse(qty, 3) * parse(price, 4) // 7 decimal places
  const divisor = 10n ** 5n
  const rounded = (product + (product >= 0n ? divisor / 2n : -divisor / 2n)) / divisor
  return format(rounded, 2)
}

/** Sum of money strings (2 dp). */
export function sumMoney(values: string[]): string {
  return format(
    values.reduce((total, v) => total + parse(v, 2), 0n),
    2,
  )
}

/** a - b for money strings. */
export function subMoney(a: string, b: string): string {
  return format(parse(a, 2) - parse(b, 2), 2)
}

export function isPositive(value: string): boolean {
  return parse(value, 4) > 0n
}

export function cmpMoney(a: string, b: string): number {
  const diff = parse(a, 2) - parse(b, 2)
  return diff === 0n ? 0 : diff > 0n ? 1 : -1
}

/** price per entry unit given a per-base price (DP) and the unit factor. */
export function pricePerUnit(basePrice: string, factor: string): string {
  const product = parse(basePrice, 4) * parse(factor, 3) // 7 dp
  return format((product + 500n) / 1000n, 4)
}
