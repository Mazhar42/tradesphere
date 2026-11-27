/** Header search over everything the signed-in person may read, and the "find a product that
 * is not here yet" dialog. */
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, unwrap } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'

function useDebounced(value: string, delay = 300) {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return settled
}

function Discovery({ query, onClose }: { query: string; onClose: () => void }) {
  const { t } = useTranslation()
  const found = useQuery({
    queryKey: ['discover', query],
    queryFn: () => unwrap(api.GET('/api/v1/search/discover', { params: { query: { q: query } } })),
  })
  return (
    <Dialog open onOpenChange={onClose} title={t('search.discoverTitle', { q: query })} description={t('search.discoverHint')} size="lg">
      {found.data?.length === 0 && <p className="text-sm text-muted">{t('search.nothingKnown')}</p>}
      <ul className="divide-y divide-border">
        {found.data?.map((c) => (
          <li key={`${c.source}-${c.product_id}-${c.supplier_product_id}`} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0">
              <span className="block truncate font-medium">{c.title}</span>
              <span className="block text-xs text-muted">
                {t(`search.source.${c.source}`)} · {c.detail}
              </span>
            </span>
            <Badge tone={c.confidence === 'exact' ? 'success' : c.confidence === 'likely' ? 'primary' : 'neutral'}>
              {t(`search.confidence.${c.confidence}`)}
            </Badge>
          </li>
        ))}
      </ul>
    </Dialog>
  )
}

export function GlobalSearch() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [discover, setDiscover] = useState('')
  const q = useDebounced(text.trim())
  const results = useQuery({
    queryKey: ['search', q],
    queryFn: () => unwrap(api.GET('/api/v1/search', { params: { query: { q } } })),
    enabled: q.length >= 2,
  })
  const close = () => {
    setOpen(false)
    setText('')
  }

  return (
    <div
      className="relative max-w-md flex-1"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false)
      }}
      onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
    >
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <Input
        type="search"
        className="pl-9"
        value={text}
        maxLength={100}
        placeholder={t('search.placeholder')}
        aria-label={t('search.placeholder')}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
        }}
      />
      {open && q.length >= 2 && (
        <div className="absolute top-full left-0 z-40 mt-1 max-h-[70vh] w-full min-w-72 overflow-y-auto rounded-lg border border-border bg-surface p-2 shadow-lg">
          {results.isError && <p className="p-2 text-sm text-danger">{results.error.message}</p>}
          {results.data?.length === 0 && <p className="p-2 text-sm text-muted">{t('search.none')}</p>}
          {results.data?.map((group) => (
            <section key={group.group} className="mb-2">
              <h2 className="px-2 py-1 text-xs font-semibold text-muted uppercase">{t(`search.groups.${group.group}`)}</h2>
              {group.hits.map((hit) => (
                <Link key={hit.id} to={hit.url as '/'} onClick={close} className="flex justify-between gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-surface-muted">
                  <span className="truncate">{hit.title}</span>
                  <span className="shrink-0 text-xs text-muted">{hit.subtitle}</span>
                </Link>
              ))}
            </section>
          ))}
          {can('catalog:read') && results.data && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setDiscover(q)
                setOpen(false)
              }}
            >
              {t('search.findProduct')}
            </Button>
          )}
        </div>
      )}
      {discover && <Discovery query={discover} onClose={() => setDiscover('')} />}
    </div>
  )
}
