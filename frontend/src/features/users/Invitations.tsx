/** Invite someone to this dealership, and see or withdraw invitations. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { api, type Schemas, unwrap } from '@/api/client'
import { ErrorState } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/features/auth/auth-context'
import { formatDateTime } from '@/lib/format'
import { useLang } from '@/i18n'

type Invitation = Schemas['InvitationRead']
const KEY = ['invitations'] as const

export function InviteDialog({
  open,
  onOpenChange,
  roles,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  roles: Schemas['RoleRead'][]
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [note, setNote] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [link, setLink] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/v1/invitations', { body: { email: email || null, note: note || null, roles: chosen } })),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: KEY })
      setLink(result.link)
      if (!result.link) {
        toast.success(t('invite.emailed'))
        close(false)
      }
    },
  })
  const close = (next: boolean) => {
    if (!next) {
      setEmail('')
      setNote('')
      setChosen([])
      setLink(null)
      create.reset()
    }
    onOpenChange(next)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t('invite.title')}
      footer={
        <>
          <Button variant="outline" onClick={() => close(false)}>
            {t('common.close')}
          </Button>
          {!link && (
            <Button loading={create.isPending} disabled={chosen.length === 0} onClick={() => create.mutate()}>
              {t('invite.create')}
            </Button>
          )}
        </>
      }
    >
      {link ? (
        <div className="space-y-3">
          <p className="text-sm">{t('invite.share')}</p>
          <Input readOnly value={link} aria-label={t('invite.link')} onFocus={(e) => e.target.select()} />
          <Button variant="outline" onClick={() => void navigator.clipboard.writeText(link)}>
            {t('invite.copy')}
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {create.isError && (
            <p role="alert" className="text-sm text-danger">
              {create.error.message}
            </p>
          )}
          <Field label={t('invite.note')} htmlFor="invite-note">
            <Input id="invite-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Field label={t('common.email')} htmlFor="invite-email" hint={t('invite.emailHint')}>
            <Input id="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <fieldset className="grid gap-2 sm:grid-cols-2">
            <legend className="mb-1 text-sm font-medium">{t('users.roles')}</legend>
            {roles.map((role) => (
              <label key={role.code} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={chosen.includes(role.code)}
                  onChange={(e) =>
                    setChosen(e.target.checked ? [...chosen, role.code] : chosen.filter((c) => c !== role.code))
                  }
                />
                {t(`roles.${role.code}`, role.name)}
              </label>
            ))}
          </fieldset>
        </div>
      )}
    </Dialog>
  )
}

export function InvitationList() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { lang } = useLang()
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/v1/invitations')) })
  const revoke = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/v1/invitations/{invitation_id}', { params: { path: { invitation_id: id } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
    onError: (e) => toast.error(e.message),
  })
  if (list.isError) return <ErrorState error={list.error} onRetry={() => void list.refetch()} />
  const state = (i: Invitation) =>
    i.accepted_at ? 'accepted' : i.revoked_at ? 'revoked' : new Date(i.expires_at) < new Date() ? 'expired' : 'pending'
  return (
    <ul className="divide-y divide-border">
      {list.data?.length === 0 && <li className="p-4 text-sm text-muted">{t('invite.none')}</li>}
      {list.data?.map((invitation) => (
        <li key={invitation.id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
          <span className="flex-1">
            {invitation.note || invitation.email || `#${invitation.id}`}
            <span className="block text-xs text-muted">
              {invitation.roles.map((r) => t(`roles.${r}`, r)).join(', ')} · {formatDateTime(invitation.expires_at, lang)}
            </span>
          </span>
          <Badge>{t(`invite.state.${state(invitation)}`)}</Badge>
          {state(invitation) === 'pending' && can('users:write') && (
            <Button size="sm" variant="outline" onClick={() => revoke.mutate(invitation.id)}>
              {t('invite.revoke')}
            </Button>
          )}
        </li>
      ))}
    </ul>
  )
}
