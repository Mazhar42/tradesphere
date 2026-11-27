/**
 * Pages reached from a link, before signing in: accept an invitation, recover a password,
 * confirm an email address. The one-time token arrives after '#', which is never sent to a server.
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { type FormEvent, type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, unwrap } from '@/api/client'
import { LanguageSwitch } from '@/components/layout/LanguageSwitch'
import { Logo } from '@/components/layout/Logo'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

function hashToken(): string {
  return new URLSearchParams(window.location.hash.slice(1)).get('token') ?? ''
}

function Shell({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-screen flex-col">
      <div className="flex items-center justify-between p-4">
        <Logo />
        <LanguageSwitch />
      </div>
      <main className="flex flex-1 items-start justify-center px-4 py-10">
        <div className="w-full max-w-sm space-y-5">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {children}
          <Link to="/login" className="inline-block text-sm text-primary underline">
            {t('account.backToLogin')}
          </Link>
        </div>
      </main>
    </div>
  )
}

function Message({ tone, children }: { tone: 'ok' | 'bad'; children: ReactNode }) {
  return (
    <div
      role={tone === 'bad' ? 'alert' : 'status'}
      className={
        tone === 'bad'
          ? 'rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger'
          : 'rounded-lg bg-primary-soft px-3 py-2 text-sm'
      }
    >
      {children}
    </div>
  )
}

function submit(action: () => void) {
  return (event: FormEvent) => {
    event.preventDefault()
    action()
  }
}

export function ForgotPasswordPage() {
  const { t } = useTranslation()
  const [identifier, setIdentifier] = useState('')
  const request = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/password-reset', { body: { identifier } })),
  })
  return (
    <Shell title={t('account.forgotTitle')}>
      {request.isSuccess ? (
        <Message tone="ok">{t('account.forgotSent')}</Message>
      ) : (
        <form onSubmit={submit(() => request.mutate())} className="space-y-4">
          <p className="text-sm text-muted">{t('account.forgotHelp')}</p>
          {request.isError && <Message tone="bad">{request.error.message}</Message>}
          <Field label={t('account.usernameOrEmail')} htmlFor="identifier">
            <Input id="identifier" required value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
          </Field>
          <Button type="submit" loading={request.isPending}>
            {t('account.sendLink')}
          </Button>
        </form>
      )}
    </Shell>
  )
}

export function ResetPasswordPage() {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const reset = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/v1/auth/password-reset/confirm', { body: { token: hashToken(), new_password: password } })),
  })
  return (
    <Shell title={t('account.resetTitle')}>
      {reset.isSuccess ? (
        <Message tone="ok">{t('account.resetDone')}</Message>
      ) : (
        <form onSubmit={submit(() => reset.mutate())} className="space-y-4">
          {reset.isError && <Message tone="bad">{reset.error.message}</Message>}
          <Field label={t('users.newPassword')} htmlFor="password" hint={t('users.passwordHint')}>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Button type="submit" loading={reset.isPending}>
            {t('account.setPassword')}
          </Button>
        </form>
      )}
    </Shell>
  )
}

export function VerifyEmailPage() {
  const { t } = useTranslation()
  const verify = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/verify-email', { body: { token: hashToken() } })),
  })
  return (
    <Shell title={t('account.verifyTitle')}>
      {verify.isSuccess ? (
        <Message tone="ok">{t('account.verifyDone')}</Message>
      ) : (
        <>
          {verify.isError && <Message tone="bad">{verify.error.message}</Message>}
          {/* A button, not an automatic request: mail scanners open links. */}
          <Button loading={verify.isPending} onClick={() => verify.mutate()}>
            {t('account.confirmEmail')}
          </Button>
        </>
      )}
    </Shell>
  )
}

export function AcceptInvitationPage() {
  const { t } = useTranslation()
  const token = hashToken()
  const [form, setForm] = useState({ username: '', full_name: '', password: '' })
  const preview = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => unwrap(api.POST('/api/v1/auth/invitations/inspect', { body: { token } })),
    retry: false,
  })
  const accept = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/auth/invitations/accept', {
          body: { token, username: form.username, password: form.password, full_name: form.full_name || null },
        }),
      ),
  })
  const input = (name: keyof typeof form) => ({
    id: name,
    value: form[name],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }),
  })

  return (
    <Shell title={t('account.inviteTitle')}>
      {preview.isError && <Message tone="bad">{preview.error.message}</Message>}
      {accept.isSuccess && <Message tone="ok">{t('account.inviteDone')}</Message>}
      {preview.data && !accept.isSuccess && (
        <form onSubmit={submit(() => accept.mutate())} className="space-y-4">
          <p className="text-sm">{t('account.inviteFrom', { dealership: preview.data.dealership })}</p>
          <p className="text-sm text-muted">{t('account.inviteExisting')}</p>
          {accept.isError && <Message tone="bad">{accept.error.message}</Message>}
          <Field label={t('auth.username')} htmlFor="username">
            <Input {...input('username')} required autoComplete="username" />
          </Field>
          <Field label={t('users.fullName')} htmlFor="full_name">
            <Input {...input('full_name')} autoComplete="name" />
          </Field>
          <Field label={t('users.password')} htmlFor="password" hint={t('users.passwordHint')}>
            <Input {...input('password')} type="password" required minLength={8} />
          </Field>
          <Button type="submit" loading={accept.isPending}>
            {t('account.join')}
          </Button>
        </form>
      )}
    </Shell>
  )
}
