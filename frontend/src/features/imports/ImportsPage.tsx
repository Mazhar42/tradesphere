/** Import wizard: choose, upload, map columns, check the preview, approve, then sign off. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { ErrorState, PageHeader } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'

type Job = Schemas['JobRead']
type Entity = Schemas['EntityRead']
const KEY = ['imports'] as const
const SELECT = 'h-10 rounded-lg border border-border bg-surface px-2 text-sm'
const TOTALS = ['stock_value', 'receivables', 'payables', 'advances', 'claims', 'cash'] as const
const onError = (error: Error) => toast.error(error.message)

async function download(request: Promise<{ data?: unknown; response: Response }>, name: string) {
  const { data, response } = await request
  if (!response.ok) return
  const url = URL.createObjectURL(data as Blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function Upload({ entities, onJob }: { entities: Entity[]; onJob: (job: Job) => void }) {
  const { t, i18n } = useTranslation()
  const [entity, setEntity] = useState('products')
  const [file, setFile] = useState<File | null>(null)
  const [source, setSource] = useState('file')
  const [policy, setPolicy] = useState('skip')
  const [dateFormat, setDateFormat] = useState('YYYY-MM-DD')
  const [cutover, setCutover] = useState('')
  const [prices, setPrices] = useState(false)
  const opening = entities.find((e) => e.name === entity)?.opening
  const send = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set('file', file!)
      form.set('entity', entity)
      form.set('source_system', source)
      form.set('conflict_policy', policy)
      form.set('date_format', dateFormat)
      form.set('update_prices', String(prices))
      if (cutover) form.set('cutover_date', cutover)
      return unwrap(
        api.POST('/api/v1/imports', {
          body: {} as Schemas['Body_upload'],
          bodySerializer: () => form,
        }),
      )
    },
    onSuccess: onJob,
    onError,
  })
  return (
    <Card className="p-4">
      <form
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          send.mutate()
        }}
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
      >
        <Field label={t('imports.what')} htmlFor="entity">
          <select id="entity" className={SELECT} value={entity} onChange={(e) => setEntity(e.target.value)}>
            {entities.map((e) => (
              <option key={e.name} value={e.name}>
                {t(`imports.entities.${e.name}`, e.name)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('imports.file')} htmlFor="file" hint="CSV / XLSX, 10 MB">
          <Input id="file" type="file" accept=".csv,.xlsx" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
        <Field label={t('imports.source')} htmlFor="source">
          <Input id="source" value={source} onChange={(e) => setSource(e.target.value)} />
        </Field>
        <Field label={t('imports.existing')} htmlFor="policy">
          <select id="policy" className={SELECT} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {['skip', 'update', 'reject'].map((p) => (
              <option key={p} value={p}>
                {t(`imports.policies.${p}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('imports.dateFormat')} htmlFor="date-format">
          <select id="date-format" className={SELECT} value={dateFormat} onChange={(e) => setDateFormat(e.target.value)}>
            {['YYYY-MM-DD', 'DD/MM/YYYY', 'MM/DD/YYYY'].map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </Field>
        {(opening || entity === 'history') && (
          <Field label={t('imports.cutover')} htmlFor="cutover">
            <Input id="cutover" type="date" required={opening} value={cutover} onChange={(e) => setCutover(e.target.value)} />
          </Field>
        )}
        {entity === 'products' && policy === 'update' && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={prices} onChange={(e) => setPrices(e.target.checked)} />
            {t('imports.updatePrices')}
          </label>
        )}
        <div className="flex items-end gap-2">
          <Button type="submit" loading={send.isPending} disabled={!file}>
            {t('imports.upload')}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              void download(
                api.GET('/api/v1/imports/templates/{entity}', {
                  params: { path: { entity }, query: { lang: i18n.language === 'bn' ? 'bn' : 'en' } },
                  parseAs: 'blob',
                }),
                `${entity}.csv`,
              )
            }
          >
            {t('imports.template')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function JobCard({ jobId, entities }: { jobId: number; entities: Entity[] }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const path = { job_id: jobId }
  const job = useQuery({
    queryKey: [...KEY, jobId],
    queryFn: () => unwrap(api.GET('/api/v1/imports/{job_id}', { params: { path } })),
    // The worker commits in the background.
    refetchInterval: (query) => (['approved', 'committing'].includes(query.state.data?.status ?? '') ? 2000 : false),
  })
  const rows = useQuery({
    queryKey: [...KEY, jobId, 'errors', job.data?.dry_run_digest, job.data?.status],
    enabled: !!job.data,
    queryFn: () =>
      unwrap(api.GET('/api/v1/imports/{job_id}/rows', { params: { path, query: { only: 'error', page_size: 50 } } })),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEY })
  const remap = useMutation({
    mutationFn: (mapping: Record<string, string>) =>
      unwrap(api.PUT('/api/v1/imports/{job_id}/mapping', { params: { path }, body: { mapping } })),
    onSuccess: refresh,
    onError,
  })
  const approve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/imports/{job_id}/approve', {
          params: { path },
          body: { dry_run_digest: job.data?.dry_run_digest ?? '' },
        }),
      ),
    onSuccess: refresh,
    onError: (error) => {
      onError(error)
      void refresh()
    },
  })
  if (job.isError) return <ErrorState error={job.error} onRetry={() => void job.refetch()} />
  const data = job.data
  if (!data) return null
  const columns = entities.find((e) => e.name === data.entity)?.columns ?? []
  const open = ['needs_mapping', 'dry_run_ready'].includes(data.status)

  return (
    <Card className="space-y-3 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">
          #{data.id} {t(`imports.entities.${data.entity}`, data.entity)}
        </span>
        <Badge>{t(`imports.status.${data.status}`, data.status)}</Badge>
        {data.message && <span className="text-warning">{data.message}</span>}
      </div>
      {open && (
        <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <legend className="mb-1 font-medium">{t('imports.columns')}</legend>
          {columns.map((column) => (
            <label key={column.name} className="flex items-center justify-between gap-2">
              <span>
                {column.name}
                {column.required && ' *'}
              </span>
              <select
                className={SELECT}
                value={data.mapping[column.name] ?? ''}
                onChange={(e) => {
                  const next = { ...data.mapping }
                  if (e.target.value) next[column.name] = e.target.value
                  else delete next[column.name]
                  remap.mutate(next)
                }}
              >
                <option value="">—</option>
                {data.headers.map((header) => (
                  <option key={header}>{header}</option>
                ))}
              </select>
            </label>
          ))}
        </fieldset>
      )}
      <p>
        {t('imports.counts', {
          create: data.counts.create ?? 0,
          update: data.counts.update ?? 0,
          skip: data.counts.skip ?? 0,
          error: data.counts.error ?? 0,
        })}
        {data.counts.total ? ` · ${t('imports.total', { total: data.counts.total })}` : ''}
      </p>
      {!!rows.data?.total && (
        <>
          <ul className="max-h-48 overflow-auto text-danger">
            {rows.data.items.map((row) => (
              <li key={row.row_no}>
                {t('imports.row', { row: row.row_no })}: {row.errors.join('; ')}
              </li>
            ))}
          </ul>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void download(
                api.GET('/api/v1/imports/{job_id}/errors', { params: { path }, parseAs: 'blob' }),
                `import-${jobId}-errors.csv`,
              )
            }
          >
            {t('imports.errorFile')}
          </Button>
        </>
      )}
      {data.status === 'dry_run_ready' && can('imports:approve') && (
        <Button loading={approve.isPending} onClick={() => approve.mutate()}>
          {t('imports.approve')}
        </Button>
      )}
    </Card>
  )
}

function Cutover() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [declared, setDeclared] = useState<Record<string, string>>({})
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const report = useQuery({ queryKey: [...KEY, 'cutover'], queryFn: () => unwrap(api.GET('/api/v1/imports/cutover')) })
  const sign = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/imports/cutover/sign-off', {
          body: {
            declared: Object.fromEntries(TOTALS.map((name) => [name, declared[name] || '0'])),
            reasons,
          },
        }),
      ),
    onSuccess: () => {
      toast.success(t('imports.signed'))
      void queryClient.invalidateQueries({ queryKey: KEY })
    },
    onError,
  })
  const data = report.data
  if (!data?.cutover_date) return null
  return (
    <Card className="space-y-2 p-4 text-sm">
      <h2 className="font-semibold">{t('imports.signOffTitle', { date: data.cutover_date })}</h2>
      <p className="text-muted">{t('imports.signOffHelp')}</p>
      {!data.reconcile_ok && <p className="text-danger">{data.reconcile_issues.join('; ')}</p>}
      <table className="w-full">
        <thead>
          <tr className="text-left">
            <th />
            <th>{t('imports.loaded')}</th>
            <th>{t('imports.yours')}</th>
            <th>{t('imports.reason')}</th>
          </tr>
        </thead>
        <tbody>
          {TOTALS.map((name) => (
            <tr key={name}>
              <th scope="row" className="text-left font-normal">
                {t(`imports.totals.${name}`)}
              </th>
              <td>{data.imported[name]}</td>
              <td>
                <Input
                  aria-label={`${t(`imports.totals.${name}`)} ${t('imports.yours')}`}
                  inputMode="decimal"
                  value={declared[name] ?? ''}
                  onChange={(e) => setDeclared({ ...declared, [name]: e.target.value })}
                />
              </td>
              <td>
                <Input
                  aria-label={`${t(`imports.totals.${name}`)} ${t('imports.reason')}`}
                  value={reasons[name] ?? ''}
                  onChange={(e) => setReasons({ ...reasons, [name]: e.target.value })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.signed_at ? (
        <Badge tone="success">{t('imports.signed')}</Badge>
      ) : (
        can('finance:opening') && (
          <Button loading={sign.isPending} onClick={() => sign.mutate()}>
            {t('imports.signOff')}
          </Button>
        )
      )}
    </Card>
  )
}

export function ImportsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const entities = useQuery({ queryKey: [...KEY, 'entities'], queryFn: () => unwrap(api.GET('/api/v1/imports/entities')) })
  const jobs = useQuery({ queryKey: [...KEY, 'list'], queryFn: () => unwrap(api.GET('/api/v1/imports')) })
  const checklist = useQuery({ queryKey: [...KEY, 'checklist'], queryFn: () => unwrap(api.GET('/api/v1/imports/checklist')) })
  if (entities.isError) return <ErrorState error={entities.error} onRetry={() => void entities.refetch()} />
  const done = checklist.data

  return (
    <>
      <PageHeader title={t('imports.title')} description={t('imports.subtitle')} />
      <div className="space-y-4">
        {done && (
          <Card className="flex flex-wrap gap-2 p-4 text-sm">
            {(
              [
                ['products', done.products > 0],
                ['suppliers', done.suppliers > 0],
                ['customers', done.customers > 0],
                ['opening', done.opening_domains.length > 0],
                ['signOff', done.signed_off],
              ] as const
            ).map(([step, ok]) => (
              <Badge key={step} tone={ok ? 'success' : undefined}>
                {ok ? '✓ ' : ''}
                {t(`imports.steps.${step}`)}
              </Badge>
            ))}
          </Card>
        )}
        <Upload
          entities={entities.data ?? []}
          onJob={(job) => {
            setSelected(job.id)
            void queryClient.invalidateQueries({ queryKey: KEY })
          }}
        />
        {selected && <JobCard jobId={selected} entities={entities.data ?? []} />}
        {!!jobs.data?.total && (
          <Card className="divide-y divide-border">
            {jobs.data.items.map((job) => (
              <button
                key={job.id}
                type="button"
                className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-surface-muted"
                onClick={() => setSelected(job.id)}
              >
                <span className="flex-1">
                  #{job.id} {t(`imports.entities.${job.entity}`, job.entity)} · {job.source_system}
                </span>
                <Badge>{t(`imports.status.${job.status}`, job.status)}</Badge>
              </button>
            ))}
          </Card>
        )}
        <Cutover />
      </div>
    </>
  )
}
