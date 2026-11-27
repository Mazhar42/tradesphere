/** Built-in roles (read only) and the dealership's own. */
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

type Role = Schemas['RoleRead']

function RoleDialog({ role, onClose }: { role: Role | 'new'; onClose: () => void }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const existing = role === 'new' ? null : role
  const [code, setCode] = useState('')
  const [name, setName] = useState(existing?.name ?? '')
  const [chosen, setChosen] = useState<string[]>(existing?.permissions ?? [])
  const permissions = useQuery({ queryKey: ['permissions'], queryFn: () => unwrap(api.GET('/api/v1/permissions')) })

  const done = async () => {
    await queryClient.invalidateQueries({ queryKey: ['roles'] })
    onClose()
  }
  const onError = (error: Error) => toast.error(error.message)
  const save = useMutation({
    mutationFn: async () => {
      if (existing) {
        await unwrap(
          api.PATCH('/api/v1/roles/{role_id}', {
            params: { path: { role_id: existing.id } },
            body: { name, permissions: chosen },
          }),
        )
      } else {
        await unwrap(api.POST('/api/v1/roles', { body: { code, name, permissions: chosen } }))
      }
    },
    onSuccess: done,
    onError,
  })
  const remove = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE('/api/v1/roles/{role_id}', { params: { path: { role_id: existing!.id } } })),
    onSuccess: done,
    onError,
  })

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={existing ? t('roleEditor.editTitle') : t('roleEditor.new')}
      size="lg"
      footer={
        <>
          {existing && (
            <Button variant="outline" loading={remove.isPending} onClick={() => remove.mutate()}>
              {t('roleEditor.delete')}
            </Button>
          )}
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            loading={save.isPending}
            disabled={!name || chosen.length === 0 || (!existing && !code)}
            onClick={() => save.mutate()}
          >
            {t('common.save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {!existing && (
          <Field label={t('roleEditor.code')} htmlFor="role-code">
            <Input id="role-code" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        )}
        <Field label={t('roleEditor.name')} htmlFor="role-name">
          <Input id="role-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <fieldset className="grid gap-1 sm:grid-cols-2">
          <legend className="mb-1 text-sm font-medium">{t('roleEditor.permissions')}</legend>
          {permissions.data?.map((permission) => (
            <label key={permission.code} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4"
                // Nobody can hand out what they do not hold; the server checks too.
                disabled={!can(permission.code)}
                checked={chosen.includes(permission.code)}
                onChange={(e) =>
                  setChosen(
                    e.target.checked ? [...chosen, permission.code] : chosen.filter((c) => c !== permission.code),
                  )
                }
              />
              {permission.description}
            </label>
          ))}
        </fieldset>
      </div>
    </Dialog>
  )
}

export function RolesEditor() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [editing, setEditing] = useState<Role | 'new' | null>(null)
  const roles = useQuery({ queryKey: ['roles'], queryFn: () => unwrap(api.GET('/api/v1/roles')) })
  if (roles.isError) return <ErrorState error={roles.error} onRetry={() => void roles.refetch()} />
  return (
    <div className="space-y-4 p-4">
      {can('users:write') && (
        <Button variant="outline" onClick={() => setEditing('new')}>
          {t('roleEditor.new')}
        </Button>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {roles.data?.map((role) => (
          <div key={role.code} className="rounded-lg border border-border p-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-semibold">{t(`roles.${role.code}`, role.name)}</h3>
              {role.is_system ? (
                <Badge>{t('roleEditor.builtIn')}</Badge>
              ) : (
                can('users:write') && (
                  <Button size="sm" variant="outline" onClick={() => setEditing(role)}>
                    {t('common.edit')}
                  </Button>
                )
              )}
            </div>
            <p className="mt-1 text-sm text-muted">{role.description}</p>
            <div className="mt-3 flex flex-wrap gap-1">
              {role.permissions.map((p) => (
                <code key={p} className="rounded bg-surface-muted px-1.5 py-0.5 text-[11px] text-muted">
                  {p}
                </code>
              ))}
            </div>
          </div>
        ))}
      </div>
      {editing && <RoleDialog role={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
