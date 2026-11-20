/**
 * Display formatting for Bangladesh: Taka with lakh/crore grouping, optional Bangla digits.
 * Values arrive from the API as decimal strings and are formatted without float arithmetic.
 */

export type Lang = 'en' | 'bn'

const NUMBER_LOCALE: Record<Lang, string> = { en: 'en-IN', bn: 'bn-BD' }
const DATE_LOCALE: Record<Lang, string> = { en: 'en-GB', bn: 'bn-BD' }

type Numeric = string | number | null | undefined

// Intl.NumberFormat.format accepts decimal strings exactly (no float rounding) in modern engines.
type DecimalFormatter = { format(value: string | number): string }

function formatter(lang: Lang, minFraction: number, maxFraction: number): DecimalFormatter {
  return new Intl.NumberFormat(NUMBER_LOCALE[lang], {
    minimumFractionDigits: minFraction,
    maximumFractionDigits: maxFraction,
  }) as unknown as DecimalFormatter
}

/** "1250000.5" -> "৳12,50,000.50" (en) / "৳১২,৫০,০০০.৫০" (bn). */
export function formatMoney(value: Numeric, lang: Lang = 'en', withSymbol = true): string {
  if (value === null || value === undefined || value === '') return '—'
  const text = formatter(lang, 2, 2).format(value)
  return withSymbol ? `৳${text}` : text
}

/** Plain number with lakh grouping and up to `maxFraction` decimals ("48.000" -> "48"). */
export function formatNumber(value: Numeric, lang: Lang = 'en', maxFraction = 3): string {
  if (value === null || value === undefined || value === '') return '—'
  return formatter(lang, 0, maxFraction).format(value)
}

export function formatDate(value: string | Date | null | undefined, lang: Lang = 'en'): string {
  if (!value) return '—'
  const date = typeof value === 'string' ? new Date(value) : value
  return new Intl.DateTimeFormat(DATE_LOCALE[lang], { dateStyle: 'medium' }).format(date)
}

export function formatDateTime(value: string | Date | null | undefined, lang: Lang = 'en'): string {
  if (!value) return '—'
  const date = typeof value === 'string' ? new Date(value) : value
  return new Intl.DateTimeFormat(DATE_LOCALE[lang], {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Dhaka',
  }).format(date)
}
