import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { FilePlus, HandCoins } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { useActiveSuppliers } from '@/features/purchases/hooks'
import { Taka } from '@/features/purchases/shared'
import { useLang } from '@/i18n'
import { formatDate } from '@/lib/format'

import { CommissionDialog } from './CommissionDialog'
import { claimKeys, invalidateClaims } from './hooks'

type Tab = 'items' | 'claims' | 'commissions'
type Item = Schemas['ClaimableRead']
const PAGE_SIZE = 25
const ITEM_STATUSES = ['open', 'in_claim', 'settled', 'rejected', 'cancelled']
const CLAIM_STATUSES = ['draft', 'submitted', 'partially_settled', 'settled', 'rejected']

const TONE: Record<string, 'neutral' | 'primary' | 'success' | 'warning' | 'danger'> = {
  open: 'primary',
  in_claim: 'warning',
  settled: 'success',
  rejected: 'danger',
  cancelled: 'neutral',
  draft: 'neutral',
  submitted: 'primary',
  partially_settled: 'warning',
}

export function ClaimStatusBadge({ status, group }: { status: string; group: 'item' | 'claim' }) {
  const { t } = useTranslation()
  return <Badge tone={TONE[status] ?? 'neutral'}>{t(`claims.${group}Status.${status}`, status)}</Badge>
}

export function ClaimsPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const suppliers = useActiveSuppliers()
  const canWrite = can('claims:write')

  const [tab, setTab] = useState<Tab>('items')
  const [page, setPage] = useState(1)
  const [supplierId, setSupplierId] = useState('')
  const [status, setStatus] = useState('open')
  const [selected, setSelected] = useState<number[]>([])
  const [commissionOpen, setCommissionOpen] = useState(false)

  const supplier = supplierId ? Number(supplierId) : undefined
  const items = useQuery({
    queryKey: [...claimKeys.items, { supplier, status, page }],
    enabled: tab === 'items',
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/claimable-items', {
          params: { query: { supplier_id: supplier, status: status || undefined, page, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
  const claims = useQuery({
    queryKey: [...claimKeys.claims, { supplier, status, page }],
    enabled: tab === 'claims',
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/company-claims', {
          params: { query: { supplier_id: supplier, status: status || undefined, page, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
  const commissions = useQuery({
    queryKey: [...claimKeys.commissions, { page }],
    enabled: tab === 'commissions',
    queryFn: () => unwrap(api.GET('/api/v1/commission-payments', { params: { query: { page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
  })

  const chosen = (items.data?.items ?? []).filter((i) => selected.includes(i.id))
  const sameCompany = chosen.length > 0 && chosen.every((i) => i.supplier_id === chosen[0]!.supplier_id)

  const createClaim = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/company-claims', {
          body: { supplier_id: chosen[0]!.supplier_id, claimable_item_ids: chosen.map((i) => i.id) },
        }),
      ),
    onSuccess: (claim) => {
      toast.success(t('claims.claimCreated'))
      setSelected([])
      invalidateClaims(queryClient)
      void navigate({ to: '/claims/$claimId', params: { claimId: String(claim.id) } })
    },
    onError: (e) => toast.error(e.message),
  })

  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const itemColumns: Column<Item>[] = [
      ...(canWrite && status === 'open'
        ? ([
            {
              header: '',
              cell: (i) => (
                <input
                  type="checkbox"
                  aria-label={t('claims.select')}
                  className="size-4 accent-[var(--color-primary)]"
                  checked={selected.includes(i.id)}
                  onChange={() => toggle(i.id)}
                />
              ),
            },
          ] as Column<Item>[])
        : []),
      { header: t('sales.date'), cell: (i) => formatDate(i.business_date, lang) },
      { header: t('claims.company'), cell: (i) => i.supplier_name },
      { header: t('claims.type'), cell: (i) => t(`claims.types.${i.claim_type}`, i.claim_type) },
      { header: t('claims.source'), cell: (i) => `${t(`claims.sources.${i.source_doc_type}`, i.source_doc_type)} #${i.source_doc_id}` },
      { header: t('claims.book'), align: 'right', cell: (i) => <Taka value={i.book_amount} /> },
      { header: t('claims.claimAmount'), align: 'right', cell: (i) => <Taka value={i.claim_amount} /> },
      { header: t('common.status'), cell: (i) => <ClaimStatusBadge status={i.status} group="item" /> },
  ]

  const claimColumns = useMemo<Column<Schemas['CompanyClaimListItem']>[]>(
    () => [
      {
        header: t('claims.claim'),
        cell: (c) => (
          <div>
            <div className="font-medium">{c.doc_no ?? t('claims.draft')}</div>
            <div className="text-xs text-muted">{formatDate(c.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('claims.company'), cell: (c) => c.supplier_name },
      { header: t('common.status'), cell: (c) => <ClaimStatusBadge status={c.status} group="claim" /> },
      { header: t('claims.claimed'), align: 'right', cell: (c) => <Taka value={c.total_claimed} /> },
      { header: t('claims.settled'), align: 'right', cell: (c) => <Taka value={c.total_settled} /> },
      { header: t('claims.rejected'), align: 'right', cell: (c) => <Taka value={c.total_rejected} /> },
      { header: t('claims.outstanding'), align: 'right', cell: (c) => <Taka value={c.outstanding} className="font-medium" /> },
    ],
    [t, lang],
  )

  const commissionColumns = useMemo<Column<Schemas['CommissionRead']>[]>(
    () => [
      {
        header: t('claims.commission'),
        cell: (c) => (
          <div>
            <div className="font-medium">{c.doc_no}</div>
            <div className="text-xs text-muted">{formatDate(c.business_date, lang)}</div>
          </div>
        ),
      },
      { header: t('sales.customer'), cell: (c) => c.customer_name },
      { header: t('claims.method'), cell: (c) => t(`claims.methods.${c.method}`, c.method) },
      {
        header: t('claims.fundedBy'),
        cell: (c) => (c.is_company_funded ? <Badge tone="primary">{t('claims.company')}</Badge> : <Badge>{t('claims.dealer')}</Badge>),
      },
      { header: t('claims.schemeRef'), cell: (c) => c.scheme_ref ?? '—' },
      { header: t('sales.amount'), align: 'right', cell: (c) => <Taka value={c.amount} /> },
    ],
    [t, lang],
  )

  const current = tab === 'items' ? items : tab === 'claims' ? claims : commissions
  const total = current.data?.total ?? 0
  const statuses = tab === 'items' ? ITEM_STATUSES : CLAIM_STATUSES

  return (
    <>
      <PageHeader
        title={t('claims.title')}
        description={t('claims.subtitle')}
        actions={
          canWrite && (
            <>
              <Button variant="outline" onClick={() => setCommissionOpen(true)}>
                <HandCoins className="size-4" /> {t('claims.recordCommission')}
              </Button>
              {tab === 'items' && (
                <Button disabled={!sameCompany} loading={createClaim.isPending} onClick={() => createClaim.mutate()}>
                  <FilePlus className="size-4" /> {t('claims.createClaim', { count: chosen.length })}
                </Button>
              )}
            </>
          )
        }
      />
      <Card>
        <Toolbar>
          <Tabs
            value={tab}
            onChange={(v) => {
              setTab(v)
              setPage(1)
              setSelected([])
              setStatus(v === 'items' ? 'open' : '')
            }}
            items={[
              { value: 'items' as const, label: t('claims.items') },
              { value: 'claims' as const, label: t('claims.claims') },
              { value: 'commissions' as const, label: t('claims.commissions') },
            ]}
          />
          {tab !== 'commissions' && (
            <>
              <Select
                className="w-auto"
                aria-label={t('claims.company')}
                value={supplierId}
                onChange={(e) => {
                  setSupplierId(e.target.value)
                  setPage(1)
                  setSelected([])
                }}
              >
                <option value="">{t('purchases.allSuppliers')}</option>
                {suppliers.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
              <Select
                className="w-auto"
                aria-label={t('common.status')}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value)
                  setPage(1)
                  setSelected([])
                }}
              >
                <option value="">{t('common.all')}</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {t(`claims.${tab === 'items' ? 'item' : 'claim'}Status.${s}`)}
                  </option>
                ))}
              </Select>
            </>
          )}
        </Toolbar>
        {current.isError ? (
          <div className="p-4">
            <ErrorState error={current.error} onRetry={() => void current.refetch()} />
          </div>
        ) : tab === 'items' ? (
          <DataTable columns={itemColumns} data={items.data?.items} loading={items.isPending} />
        ) : tab === 'claims' ? (
          <DataTable
            columns={claimColumns}
            data={claims.data?.items}
            loading={claims.isPending}
            onRowClick={(c) => void navigate({ to: '/claims/$claimId', params: { claimId: String(c.id) } })}
          />
        ) : (
          <DataTable columns={commissionColumns} data={commissions.data?.items} loading={commissions.isPending} />
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </Card>
      <CommissionDialog open={commissionOpen} onOpenChange={setCommissionOpen} />
    </>
  )
}
