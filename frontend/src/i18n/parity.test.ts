import { describe, expect, it } from 'vitest'

import bn from './bn.json'
import en from './en.json'

function keys(node: object, prefix = ''): string[] {
  return Object.entries(node).flatMap(([key, value]) =>
    typeof value === 'object' && value !== null ? keys(value, `${prefix}${key}.`) : [`${prefix}${key}`],
  )
}

describe('translations', () => {
  it('has the same keys in English and Bangla', () => {
    expect(keys(bn).sort()).toEqual(keys(en).sort())
  })
})
