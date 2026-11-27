/** The owner decides whether, and for how long, a support operator may read the records. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, unwrap } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useLang } from '@/i18n'
import { formatDateTime } from '@/lib/format'

const KEY = ['support-grants'] as const

export function SupportAccess() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const [operator, setOperator] = useState('')
  const [reason, setReason] = useState('')
  const grants = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/v1/support-grants')) })
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEY })
  const onError = (error: Error) => toast.error(error.message)
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/v1/support-grants', { body: { operator_username: operator, reason, hours: 24 } })),
    onSuccess: async () => {
      setOperator('')
      setReason('')
      await refresh()
    },
    onError,
  })
  const revoke = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/v1/support-grants/{grant_id}', { params: { path: { grant_id: id } } })),
    onSuccess: refresh,
    onError,
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }
  const active = grants.data?.filter((g) => !g.revoked_at && new Date(g.expires_at) > new Date()) ?? []

  return (
    <Card className="space-y-4 p-4">
      <div>
        <h2 className="font-semibold">{t('support.title')}</h2>
        <p className="text-sm text-muted">{t('support.help')}</p>
      </div>
      <ul className="divide-y divide-border">
        {active.map((grant) => (
          <li key={grant.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="flex-1">
              {grant.reason}
              <span className="block text-xs text-muted">
                {t('support.until', { when: formatDateTime(grant.expires_at, lang) })}
              </span>
            </span>
            <Button size="sm" variant="outline" onClick={() => revoke.mutate(grant.id)}>
              {t('support.end')}
            </Button>
          </li>
        ))}
      </ul>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
        <Field label={t('support.operator')} htmlFor="support-operator">
          <Input id="support-operator" required value={operator} onChange={(e) => setOperator(e.target.value)} />
        </Field>
        <Field label={t('support.reason')} htmlFor="support-reason">
          <Input id="support-reason" required minLength={5} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Button type="submit" variant="outline" loading={create.isPending}>
          {t('support.allow')}
        </Button>
      </form>
    </Card>
  )
}
