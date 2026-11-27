/**
 * Platform administration. Deliberately separate from the dealership app: its own sign-in and
 * token (held in memory only), loaded only when /platform is opened.
 */
import { type FormEvent, useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Schemas } from '@/api/client'
import { LanguageSwitch } from '@/components/layout/LanguageSwitch'
import { Logo } from '@/components/layout/Logo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDateTime } from '@/lib/format'

type Tenant = Schemas['TenantRow']
type Audit = Schemas['PlatformAuditRow']
type Jobs = Schemas['JobHealth']

async function call<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1/platform${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  })
  const body: unknown = response.status === 204 ? null : await response.json()
  if (!response.ok) throw new Error((body as { detail?: string }).detail ?? `Request failed (${response.status})`)
  return body as T
}

export function PlatformApp() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const [token, setToken] = useState<string | null>(null)
  const [form, setForm] = useState({ username: '', password: '', code: '' })
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [audit, setAudit] = useState<Audit[]>([])
  const [jobs, setJobs] = useState<Jobs | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (action: () => Promise<void>) => {
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const load = async (using: string) => {
    setTenants(await call<Tenant[]>('/tenants', using))
    setAudit(await call<Audit[]>('/audit', using))
    setJobs(await call<Jobs>('/jobs', using))
  }

  const signIn = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const result = await call<{ access_token: string }>('/login', null, {
        method: 'POST',
        body: JSON.stringify(form),
      })
      setToken(result.access_token)
      await load(result.access_token)
    })
  }

  const setStatus = (tenant: Tenant, status: 'active' | 'suspended') => {
    const reason = window.prompt(t('platform.reason', { name: tenant.legal_name }))
    if (!reason) return
    void run(async () => {
      await call(`/tenants/${tenant.id}`, token, { method: 'PATCH', body: JSON.stringify({ status, reason }) })
      await load(token!)
    })
  }

  const field = (name: keyof typeof form, label: string, type = 'text') => (
    <Field label={label} htmlFor={`platform-${name}`}>
      <Input
        id={`platform-${name}`}
        type={type}
        required
        value={form[name]}
        onChange={(e) => setForm({ ...form, [name]: e.target.value })}
      />
    </Field>
  )

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <Badge>{t('platform.title')}</Badge>
        </div>
        <LanguageSwitch />
      </header>
      {error && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {!token ? (
        <form onSubmit={signIn} className="max-w-sm space-y-3">
          {field('username', t('auth.username'))}
          {field('password', t('auth.password'), 'password')}
          {field('code', t('security.code'))}
          <Button type="submit">{t('auth.signIn')}</Button>
        </form>
      ) : (
        <>
          <Card className="overflow-x-auto p-4">
            <h2 className="mb-2 font-semibold">{t('platform.dealerships')}</h2>
            <table className="w-full text-sm">
              <tbody>
                {tenants.map((tenant) => (
                  <tr key={tenant.id} className="border-t border-border">
                    <td className="py-2">
                      {tenant.legal_name}
                      <span className="block text-xs text-muted">{tenant.slug}</span>
                    </td>
                    <td>{t('platform.members', { count: tenant.members })}</td>
                    <td>
                      <Badge>{t(`platform.status.${tenant.status}`, tenant.status)}</Badge>
                    </td>
                    <td className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setStatus(tenant, tenant.status === 'active' ? 'suspended' : 'active')}
                      >
                        {tenant.status === 'active' ? t('platform.suspend') : t('platform.activate')}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          {jobs && (
            <Card className="p-4">
              <h2 className="mb-2 font-semibold">{t('platform.jobs')}</h2>
              <p className="text-sm">
                {t('platform.jobCounts', {
                  queued: jobs.queued,
                  failed: jobs.failed,
                  minutes: Math.round(jobs.oldest_queued_seconds / 60),
                })}
              </p>
              <ul className="mt-1 text-sm text-danger">
                {jobs.recent_failures.map((failure) => (
                  <li key={failure}>{failure}</li>
                ))}
              </ul>
            </Card>
          )}
          <Card className="p-4">
            <h2 className="mb-2 font-semibold">{t('platform.audit')}</h2>
            <ul className="space-y-1 text-sm">
              {audit.map((row) => (
                <li key={row.id}>
                  <span className="text-muted">{formatDateTime(row.occurred_at, lang)}</span> {row.action}
                  {row.tenant_id ? ` · #${row.tenant_id}` : ''}
                  {row.reason ? ` · ${row.reason}` : ''}
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  )
}
