import { useTranslation } from 'react-i18next'

import { setLanguage, useLang } from '@/i18n'
import { cn } from '@/lib/utils'

export function LanguageSwitch() {
  const { t } = useTranslation()
  const { lang } = useLang()
  return (
    <div className="inline-flex rounded-lg border border-border bg-surface p-0.5 text-sm" role="group">
      {(['en', 'bn'] as const).map((code) => (
        <button
          key={code}
          type="button"
          onClick={() => setLanguage(code)}
          aria-pressed={lang === code}
          className={cn(
            'rounded-md px-2.5 py-1 font-medium transition-colors',
            lang === code ? 'bg-primary text-primary-foreground' : 'text-muted hover:text-foreground',
          )}
        >
          {t(`lang.${code}`)}
        </button>
      ))}
    </div>
  )
}
