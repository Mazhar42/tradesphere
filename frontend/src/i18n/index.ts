import i18n from 'i18next'
import { useMemo } from 'react'
import { initReactI18next, useTranslation } from 'react-i18next'

import type { Lang } from '@/lib/format'
import { safeStorage } from '@/lib/utils'

import bn from './bn.json'
import en from './en.json'

const STORAGE_KEY = 'ts.lang'

function initialLanguage(): Lang {
  return safeStorage.get(STORAGE_KEY) === 'bn' ? 'bn' : 'en'
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, bn: { translation: bn } },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

document.documentElement.lang = i18n.language

export function setLanguage(lang: Lang): void {
  void i18n.changeLanguage(lang)
  document.documentElement.lang = lang
  safeStorage.set(STORAGE_KEY, lang)
}

/** Current language plus a picker for bilingual fields (name_en / name_bn). */
export function useLang(): { lang: Lang; pick: (en: string, bn?: string | null) => string } {
  const { i18n: instance } = useTranslation()
  const lang: Lang = instance.language === 'bn' ? 'bn' : 'en'
  return useMemo(
    () => ({
      lang,
      pick: (enText: string, bnText?: string | null) => (lang === 'bn' && bnText ? bnText : enText),
    }),
    [lang],
  )
}

export default i18n
