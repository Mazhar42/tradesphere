import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Select } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDateTime } from '@/lib/format'

type Entry = Schemas['AuditLogRead']
const PAGE_SIZE = 50
const ENTITIES = ['product', 'category', 'brand', 'uom', 'customer', 'supplier', 'user', 'setting', 'auth']

const ACTION_TONE: Record<string, 'success' | 'primary' | 'danger' | 'neutral'> = {
  create: 'success',
  update: 'primary',
  login_failed: 'danger',
  refresh_reuse: 'danger',
}

function Changes({ entry }: { entry: Entry }) {
  const { t } = useTranslation()
  const keys = Array.from(new Set([...Object.keys(entry.before ?? {}), ...Object.keys(entry.after ?? {})]))
  if (keys.length === 0) return <span className="text-muted">—</span>
  return (
    <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {keys.slice(0, 8).map((key) => (
        <div key={key} className="contents">
          <dt className="font-mono text-muted">{key}</dt>
          <dd className="truncate">
            {entry.before && key in entry.before && (
              <span className="text-danger line-through" title={t('audit.before')}>
                {JSON.stringify(entry.before[key])}
              </span>
            )}{' '}
            {entry.after && key in entry.after && (
              <span className="text-success" title={t('audit.after')}>
                {JSON.stringify(entry.after[key])}
              </span>
            )}
          </dd>
        </div>
      ))}
      {keys.length > 8 && <dd className="col-span-2 text-muted">+{keys.length - 8}</dd>}
    </dl>
  )
}

export function AuditPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const [entityType, setEntityType] = useState('')
  const [page, setPage] = useState(1)

  const query = useQuery({
    queryKey: ['audit-logs', { entityType, page }],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/audit-logs', {
          params: { query: { entity_type: entityType || undefined, page, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const columns = useMemo<Column<Entry>[]>(
    () => [
      {
        header: t('audit.when'),
        className: 'whitespace-nowrap',
        cell: (row) => formatDateTime(row.occurred_at, lang),
      },
      {
        header: t('audit.who'),
        cell: (row) => row.actor_username ?? <span className="text-muted">{t('audit.system')}</span>,
      },
      {
        header: t('audit.action'),
        cell: (row) => <Badge tone={ACTION_TONE[row.action] ?? 'neutral'}>{row.action}</Badge>,
      },
      {
        header: t('audit.entity'),
        cell: (row) => (
          <span className="font-mono text-xs">
            {row.entity_type}
            {row.entity_id && `#${row.entity_id}`}
          </span>
        ),
      },
      { header: t('audit.changes'), cell: (row) => <Changes entry={row} /> },
    ],
    [t, lang],
  )

  return (
    <>
      <PageHeader title={t('audit.title')} description={t('audit.subtitle')} />
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <Card>
          <Toolbar>
            <Select
              className="w-auto"
              value={entityType}
              aria-label={t('audit.entity')}
              onChange={(e) => {
                setEntityType(e.target.value)
                setPage(1)
              }}
            >
              <option value="">{t('audit.allEntities')}</option>
              {ENTITIES.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </Select>
          </Toolbar>
          <DataTable columns={columns} data={query.data?.items} loading={query.isPending} />
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPageChange={setPage} />
        </Card>
      )}
    </>
  )
}
