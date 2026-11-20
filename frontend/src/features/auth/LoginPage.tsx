import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { ApiError } from '@/api/client'
import { LanguageSwitch } from '@/components/layout/LanguageSwitch'
import { Logo } from '@/components/layout/Logo'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

import { useAuth } from './auth-context'

const schema = z.object({
  username: z.string().trim().min(1),
  password: z.string().min(1),
})
type FormValues = z.infer<typeof schema>

export function LoginPage() {
  const { t } = useTranslation()
  const { login } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    formState: { isSubmitting, errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = handleSubmit(async ({ username, password }) => {
    setError(null)
    try {
      await login(username, password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('common.somethingWrong'))
    }
  })

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <Logo inverted />
        <div className="max-w-md">
          <p className="text-3xl leading-snug font-semibold text-white">
            Stock, sales, dues and company claims — accurate to the last paisa.
          </p>
          <p className="mt-4 text-sm text-sidebar-muted">
            স্টক, বিক্রয়, বকেয়া ও কোম্পানির দাবি — এক জায়গায়, নির্ভুলভাবে।
          </p>
        </div>
        <p className="text-xs text-sidebar-muted">© TradeSphere</p>
      </div>

      <div className="flex flex-col">
        <div className="flex justify-between p-4 lg:justify-end">
          <div className="lg:hidden">
            <Logo />
          </div>
          <LanguageSwitch />
        </div>
        <div className="flex flex-1 items-center justify-center px-4 pb-16">
          <form onSubmit={onSubmit} className="w-full max-w-sm space-y-5" noValidate>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t('auth.welcome')}</h1>
              <p className="mt-1 text-sm text-muted">{t('auth.subtitle')}</p>
            </div>
            {error && (
              <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
                {error}
              </div>
            )}
            <Field label={t('auth.username')} htmlFor="username" error={errors.username && ' '}>
              <Input
                id="username"
                autoComplete="username"
                autoFocus
                aria-invalid={!!errors.username}
                {...register('username')}
              />
            </Field>
            <Field label={t('auth.password')} htmlFor="password" error={errors.password && ' '}>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                aria-invalid={!!errors.password}
                {...register('password')}
              />
            </Field>
            <Button type="submit" className="w-full justify-center" loading={isSubmitting}>
              {isSubmitting ? t('auth.signingIn') : t('auth.signIn')}
            </Button>
          </form>
        </div>
      </div>
    </div>
  )
}
