/** A person's own security: two-step sign-in, password, email and signed-in devices. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, refreshSession, unwrap } from '@/api/client'
import { PageHeader } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDateTime } from '@/lib/format'

import { useAuth } from './auth-context'

const onError = (error: Error) => toast.error(error.message)

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="space-y-4 p-4">
      <h2 className="font-semibold">{title}</h2>
      {children}
    </Card>
  )
}

function submit(action: () => void) {
  return (event: FormEvent) => {
    event.preventDefault()
    action()
  }
}

function Secret({ label, id, value, onChange }: { label: string; id: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label} htmlFor={id}>
      <Input id={id} type="password" required value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  )
}

export function TwoStep() {
  const { t } = useTranslation()
  const { user, reload } = useAuth()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const setup = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/mfa/setup', { body: { password } })),
    onError,
  })
  const confirm = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/mfa/confirm', { body: { code } })),
    onSuccess: (result) => setCodes(result.recovery_codes),
    onError,
  })
  const disable = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/mfa/disable', { body: { password, code } })),
    onSuccess: async () => {
      setPassword('')
      setCode('')
      await reload()
    },
    onError,
  })

  const codeField = (
    <Field label={t('security.code')} htmlFor="mfa-code">
      <Input id="mfa-code" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
    </Field>
  )

  return (
    <Section title={t('security.twoStep')}>
      {codes ? (
        <div className="space-y-3">
          <p className="text-sm">{t('security.recoveryHelp')}</p>
          <ul className="grid grid-cols-2 gap-1 font-mono text-sm">
            {codes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <Button onClick={() => void reload().then(() => setCodes(null))}>{t('security.savedThem')}</Button>
        </div>
      ) : user?.mfa_enrolled ? (
        <form onSubmit={submit(() => disable.mutate())} className="max-w-sm space-y-3">
          <Badge tone="success">{t('security.on')}</Badge>
          <Secret label={t('auth.password')} id="mfa-off-password" value={password} onChange={setPassword} />
          {codeField}
          <Button type="submit" variant="outline" loading={disable.isPending}>
            {t('security.turnOff')}
          </Button>
        </form>
      ) : setup.data ? (
        <form onSubmit={submit(() => confirm.mutate())} className="max-w-md space-y-3">
          <p className="text-sm">{t('security.addToApp')}</p>
          <code className="block rounded bg-surface-muted p-2 text-sm break-all">{setup.data.secret}</code>
          <a href={setup.data.uri} className="text-sm text-primary underline">
            {t('security.openApp')}
          </a>
          {codeField}
          <Button type="submit" loading={confirm.isPending}>
            {t('security.turnOn')}
          </Button>
        </form>
      ) : (
        <form onSubmit={submit(() => setup.mutate())} className="max-w-sm space-y-3">
          <p className="text-sm text-muted">{t('security.twoStepHelp')}</p>
          <Secret label={t('auth.password')} id="mfa-password" value={password} onChange={setPassword} />
          <Button type="submit" loading={setup.isPending}>
            {t('security.start')}
          </Button>
        </form>
      )}
    </Section>
  )
}

function Password() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/v1/auth/me/password', { body: { current_password: current, new_password: next } })),
    // Changing the password ends every session, this one included.
    onSuccess: async () => {
      toast.success(t('security.passwordChanged'))
      await logout()
    },
    onError,
  })
  return (
    <Section title={t('security.password')}>
      <form onSubmit={submit(() => change.mutate())} className="max-w-sm space-y-3">
        <Secret label={t('security.currentPassword')} id="current-password" value={current} onChange={setCurrent} />
        <Secret label={t('users.newPassword')} id="new-password" value={next} onChange={setNext} />
        <Button type="submit" loading={change.isPending}>
          {t('account.setPassword')}
        </Button>
      </form>
    </Section>
  )
}

function Email() {
  const { t } = useTranslation()
  const [address, setAddress] = useState('')
  const [password, setPassword] = useState('')
  const save = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/me/email', { body: { email: address, password } })),
    onSuccess: () => toast.success(t('security.checkInbox')),
    onError,
  })
  return (
    <Section title={t('security.email')}>
      <form onSubmit={submit(() => save.mutate())} className="max-w-sm space-y-3">
        <p className="text-sm text-muted">{t('security.emailHelp')}</p>
        <Field label={t('common.email')} htmlFor="own-email">
          <Input id="own-email" type="email" required value={address} onChange={(e) => setAddress(e.target.value)} />
        </Field>
        <Secret label={t('auth.password')} id="email-password" value={password} onChange={setPassword} />
        <Button type="submit" variant="outline" loading={save.isPending}>
          {t('account.sendLink')}
        </Button>
      </form>
    </Section>
  )
}

function Sessions() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => unwrap(api.GET('/api/v1/auth/sessions')) })
  const end = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE('/api/v1/auth/sessions/{session_id}', { params: { path: { session_id: id } } })),
    onSuccess: async () => {
      await refreshSession()
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
    onError,
  })
  return (
    <Section title={t('security.sessions')}>
      <ul className="divide-y divide-border">
        {sessions.data?.map((session) => (
          <li key={session.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="min-w-0 flex-1">
              <span className="block truncate">{session.user_agent ?? '—'}</span>
              <span className="text-xs text-muted">
                {formatDateTime(session.last_used_at, lang)} · {session.ip ?? '—'}
              </span>
            </span>
            {session.current ? (
              <Badge tone="primary">{t('security.thisDevice')}</Badge>
            ) : (
              <Button size="sm" variant="outline" onClick={() => end.mutate(session.id)}>
                {t('security.signOut')}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

export function AccountPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={t('security.title')} />
      <div className="space-y-4">
        <TwoStep />
        <Password />
        <Email />
        <Sessions />
      </div>
    </>
  )
}
