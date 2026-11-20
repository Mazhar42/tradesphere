import { safeStorage } from '@/lib/utils'

export type Theme = 'light' | 'dark'
const KEY = 'ts.theme'

export function initialTheme(): Theme {
  const stored = safeStorage.get(KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  safeStorage.set(KEY, theme)
}
