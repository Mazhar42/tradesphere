/** Asks for the authenticator code when the server wants it again before a sensitive change. */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { setStepUpHandler } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

export function StepUpDialog() {
  const { t } = useTranslation()
  const [answer, setAnswer] = useState<((code: string | null) => void) | null>(null)
  const [code, setCode] = useState('')

  useEffect(() => {
    setStepUpHandler(
      () =>
        new Promise((resolve) => {
          setCode('')
          setAnswer(() => resolve)
        }),
    )
    return () => setStepUpHandler(null)
  }, [])

  const finish = (value: string | null) => {
    answer?.(value)
    setAnswer(null)
  }

  return (
    <Dialog
      open={!!answer}
      onOpenChange={(open) => !open && finish(null)}
      title={t('security.confirmTitle')}
      footer={
        <>
          <Button variant="outline" onClick={() => finish(null)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="step-up" disabled={code.trim().length < 6}>
            {t('security.confirm')}
          </Button>
        </>
      }
    >
      <form
        id="step-up"
        onSubmit={(event) => {
          event.preventDefault()
          finish(code.trim())
        }}
      >
        <Field label={t('security.code')} htmlFor="step-up-code" hint={t('security.codeHint')}>
          <Input
            id="step-up-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
      </form>
    </Dialog>
  )
}
