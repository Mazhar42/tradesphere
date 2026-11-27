/** Working beside another system: who runs what, connections and their history, API keys,
 * the other system's figures, and CSV exports. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input, Select } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { useDomains } from './domains'

type Connection = Schemas['ConnectionRead']
const CONNECTIONS = ['connections'] as const
const KEYS = ['api-keys'] as const
const onError = (error: Error) => toast.error(error.message)

async function download(request: Promise<{ data?: unknown; response: Response }>, name: string) {
  const { data, response } = await request
  if (!response.ok) {
    toast.error(response.statusText)
    return
  }
  const url = URL.createObjectURL(data as Blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function Ownership() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const domains = useDomains()
  const set = useMutation({
    mutationFn: (body: Schemas['DomainWrite']) => unwrap(api.PUT('/api/v1/integrations/domains', { body })),
    onSuccess: (data) => queryClient.setQueryData(['domains'], data),
    onError,
  })
  return (
    <Card>
      <CardHeader title={t('integrations.ownership')} description={t('integrations.ownershipHint')} />
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
        {domains.data?.map((d) => (
          <Field key={d.domain} label={t(`integrations.domains.${d.domain}`)} htmlFor={`own-${d.domain}`}>
            <Select
              id={`own-${d.domain}`}
              value={d.owner}
              disabled={!can('settings:write') || set.isPending}
              onChange={(e) => set.mutate({ domain: d.domain, owner: e.target.value as 'external' | 'tradesphere' })}
            >
              <option value="tradesphere">TradeSphere</option>
              <option value="external">{t('integrations.external')}</option>
            </Select>
          </Field>
        ))}
      </div>
    </Card>
  )
}

function Runs({ connectionId }: { connectionId: number }) {
  const { t } = useTranslation()
  const runs = useQuery({
    queryKey: [...CONNECTIONS, connectionId, 'runs'],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/integrations/connections/{connection_id}/runs', {
          params: { path: { connection_id: connectionId } },
        }),
      ),
  })
  if (runs.data?.length === 0) return <p className="text-xs text-muted">{t('integrations.noRuns')}</p>
  return (
    <ul className="space-y-1 text-xs">
      {runs.data?.map((run) => (
        <li key={run.id}>
          <Badge tone={run.status === 'ok' ? 'success' : 'danger'}>{t(`integrations.run.${run.status}`)}</Badge>{' '}
          {new Date(run.started_at).toLocaleString()}
          <ul className="pl-4 text-muted">
            {run.results.map((r, index) => (
              <li key={index}>
                {String(r.data)}:{' '}
                {r.problem
                  ? String(r.problem)
                  : r.unchanged
                    ? t('integrations.unchanged')
                    : r.import_job_id
                      ? t('integrations.staged', { job: Number(r.import_job_id) })
                      : t('integrations.stored', { rows: Number(r.rows), unmatched: Number(r.unmatched) })}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

function ConnectionRow({ connection }: { connection: Connection }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const refresh = () => queryClient.invalidateQueries({ queryKey: CONNECTIONS })
  const sync = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/integrations/connections/{connection_id}/sync', {
          params: { path: { connection_id: connection.id } },
        }),
      ),
    onSuccess: () => {
      toast.success(t('integrations.queued'))
      void refresh()
    },
    onError,
  })
  const toggle = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT('/api/v1/integrations/connections/{connection_id}', {
          params: { path: { connection_id: connection.id } },
          body: {
            name: connection.name,
            source_system: connection.source_system,
            feeds: connection.feeds as { data: string; url: string }[],
            every_minutes: connection.every_minutes,
            enabled: !connection.enabled,
          },
        }),
      ),
    onSuccess: refresh,
    onError,
  })
  return (
    <li className="space-y-2 p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          <span className="font-medium">{connection.name}</span>{' '}
          <Badge tone={!connection.enabled ? 'neutral' : connection.failures ? 'danger' : 'success'}>
            {!connection.enabled
              ? t('integrations.off')
              : connection.failures
                ? t('integrations.failing', { count: connection.failures })
                : t('integrations.on')}
          </Badge>
          <span className="block text-xs text-muted">
            {connection.feeds.map((f) => f.data).join(', ')} ·{' '}
            {connection.last_success_at
              ? t('integrations.lastSuccess', { when: new Date(connection.last_success_at).toLocaleString() })
              : t('integrations.never')}
          </span>
        </span>
        <span className="flex gap-2">
          <Button size="sm" variant="outline" disabled={!connection.enabled} loading={sync.isPending} onClick={() => sync.mutate()}>
            {t('integrations.syncNow')}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
            {t('integrations.history')}
          </Button>
          <Button size="sm" variant="ghost" loading={toggle.isPending} onClick={() => toggle.mutate()}>
            {connection.enabled ? t('integrations.switchOff') : t('integrations.switchOn')}
          </Button>
        </span>
      </div>
      {open && <Runs connectionId={connection.id} />}
    </li>
  )
}

function Connections({ kinds }: { kinds: string[] }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const empty = { name: '', source: '', data: 'products', url: '', every: '', secret: '' }
  const [form, setForm] = useState(empty)
  const list = useQuery({
    queryKey: CONNECTIONS,
    queryFn: () => unwrap(api.GET('/api/v1/integrations/connections')),
  })
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/integrations/connections', {
          body: {
            name: form.name,
            source_system: form.source,
            feeds: [{ data: form.data, url: form.url }],
            every_minutes: form.every ? Number(form.every) : null,
            enabled: true,
            header_value: form.secret || null,
          },
        }),
      ),
    onSuccess: () => {
      setForm(empty)
      void queryClient.invalidateQueries({ queryKey: CONNECTIONS })
    },
    onError,
  })
  const field = (name: keyof typeof form) => ({
    id: `connection-${name}`,
    value: form[name],
    onChange: (e: { target: { value: string } }) => setForm((f) => ({ ...f, [name]: e.target.value })),
  })
  return (
    <Card>
      <CardHeader title={t('integrations.connections')} description={t('integrations.connectionsHint')} />
      <ul className="divide-y divide-border">
        {list.data?.map((c) => <ConnectionRow key={c.id} connection={c} />)}
      </ul>
      <form
        className="grid gap-3 border-t border-border p-4 sm:grid-cols-3"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          add.mutate()
        }}
      >
        <Field label={t('integrations.name')} htmlFor="connection-name">
          <Input required {...field('name')} />
        </Field>
        <Field label={t('imports.source')} htmlFor="connection-source">
          <Input required pattern="[A-Za-z0-9 _.\-]+" {...field('source')} />
        </Field>
        <Field label={t('imports.what')} htmlFor="connection-data">
          <Select {...field('data')}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {t(`imports.entities.${k}`, k)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('integrations.url')} htmlFor="connection-url" className="sm:col-span-3">
          <Input type="url" required placeholder="https://" {...field('url')} />
        </Field>
        <Field label={t('integrations.every')} htmlFor="connection-every" hint={t('integrations.everyHint')}>
          <Input type="number" min={15} {...field('every')} />
        </Field>
        <Field label={t('integrations.secret')} htmlFor="connection-secret" hint={t('integrations.secretHint')}>
          <Input type="password" autoComplete="off" {...field('secret')} />
        </Field>
        <div className="flex items-end">
          <Button type="submit" loading={add.isPending}>
            {t('integrations.add')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function ApiKeys() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [snapshots, setSnapshots] = useState(false)
  const [shown, setShown] = useState('')
  const list = useQuery({ queryKey: KEYS, queryFn: () => unwrap(api.GET('/api/v1/integrations/api-keys')) })
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEYS })
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/integrations/api-keys', {
          body: {
            name,
            source_system: name.replace(/[^A-Za-z0-9 _.-]/g, '').slice(0, 40) || 'api',
            scopes: snapshots ? ['files:write', 'snapshots:write'] : ['files:write'],
          },
        }),
      ),
    onSuccess: (made) => {
      setShown(made.key)
      setName('')
      void refresh()
    },
    onError,
  })
  const revoke = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/v1/integrations/api-keys/{api_key_id}', { params: { path: { api_key_id: id } } })),
    onSuccess: refresh,
    onError,
  })
  return (
    <Card>
      <CardHeader title={t('integrations.keys')} description={t('integrations.keysHint')} />
      {shown && (
        <p role="status" className="mx-4 mt-3 rounded-lg bg-warning-soft p-3 text-sm break-all">
          {t('integrations.copyNow')} <code>{shown}</code>
        </p>
      )}
      <ul className="divide-y divide-border">
        {list.data?.map((k) => (
          <li key={k.id} className="flex flex-wrap items-center justify-between gap-2 p-4 text-sm">
            <span>
              <span className="font-medium">{k.name}</span> …{k.hint}
              <span className="block text-xs text-muted">{k.scopes.join(', ')}</span>
            </span>
            {k.revoked_at ? (
              <Badge tone="neutral">{t('integrations.revoked')}</Badge>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => revoke.mutate(k.id)}>
                {t('integrations.revoke')}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-3 border-t border-border p-4"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <Field label={t('integrations.name')} htmlFor="key-name">
          <Input id="key-name" required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={snapshots} onChange={(e) => setSnapshots(e.target.checked)} />
          {t('integrations.allowSnapshots')}
        </label>
        <Button type="submit" loading={create.isPending}>
          {t('integrations.createKey')}
        </Button>
      </form>
    </Card>
  )
}

function Snapshots({ kind }: { kind: 'stock' | 'balances' }) {
  const { t } = useTranslation()
  const rows = useQuery({
    queryKey: ['snapshots', kind],
    queryFn: () =>
      unwrap(
        api.GET(kind === 'stock' ? '/api/v1/integrations/snapshots/stock' : '/api/v1/integrations/snapshots/balances', {
          params: { query: { page_size: 100 } },
        }),
      ),
  })
  if (!rows.data?.total) return null
  return (
    <Card>
      <CardHeader title={t(`integrations.snapshots.${kind}`)} description={t('integrations.snapshotsHint')} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="px-4 py-2">{t('integrations.code')}</th>
              <th className="px-4 py-2">{t('integrations.theirs')}</th>
              {kind === 'stock' && <th className="px-4 py-2">{t('integrations.ours')}</th>}
              <th className="px-4 py-2">{t('integrations.asOf')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.data.items.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2">
                  {r.code} {r.detail} {!r.matched && <Badge tone="warning">{t('integrations.unmatched')}</Badge>}
                </td>
                <td className="num px-4 py-2">{Number(r.value)}</td>
                {kind === 'stock' && <td className="num px-4 py-2">{r.ours === null ? '—' : Number(r.ours)}</td>}
                <td className="px-4 py-2 text-muted">
                  {new Date(r.as_of).toLocaleString()} · {r.source_system}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function Exports() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  return (
    <Card>
      <CardHeader title={t('integrations.exports')} />
      <div className="flex flex-wrap items-end gap-3 p-4">
        {can('finance:read') && (
          <>
            <Field label={t('integrations.from')} htmlFor="from">
              <Input id="from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </Field>
            <Field label={t('integrations.to')} htmlFor="to">
              <Input id="to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </Field>
            <Button
              variant="outline"
              disabled={!from || !to}
              onClick={() =>
                void download(
                  api.GET('/api/v1/exports/journal.csv', {
                    params: { query: { date_from: from, date_to: to } },
                    parseAs: 'blob',
                  }),
                  `journal-${from}-${to}.csv`,
                )
              }
            >
              {t('integrations.journalCsv')}
            </Button>
          </>
        )}
        {can('inventory:read') && (
          <Button variant="outline" onClick={() => void download(api.GET('/api/v1/exports/stock.csv', { parseAs: 'blob' }), 'stock.csv')}>
            {t('integrations.stockCsv')}
          </Button>
        )}
      </div>
    </Card>
  )
}

export function IntegrationsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const manage = can('integrations:manage')
  const kinds = useQuery({
    queryKey: ['feed-kinds'],
    queryFn: () => unwrap(api.GET('/api/v1/integrations/feed-kinds')),
    enabled: manage,
  })
  const domains = useDomains()
  if (domains.isError) return <ErrorState error={domains.error} />
  return (
    <>
      <PageHeader title={t('integrations.title')} description={t('integrations.subtitle')} />
      <div className="grid gap-6">
        <Ownership />
        {manage && kinds.data && <Connections kinds={kinds.data.kinds} />}
        {manage && <ApiKeys />}
        {can('inventory:read') && <Snapshots kind="stock" />}
        {can('finance:read') && <Snapshots kind="balances" />}
        <Exports />
      </div>
    </>
  )
}
