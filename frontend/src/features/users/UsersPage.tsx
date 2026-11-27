import { zodResolver } from '@hookform/resolvers/zod'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Pencil, Plus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'

import { api, type Schemas, unwrap } from '@/api/client'
import { type Column, DataTable } from '@/components/data/data-table'
import { Pagination } from '@/components/data/pagination'
import { SearchInput } from '@/components/data/search-input'
import { ErrorState, PageHeader, Toolbar } from '@/components/layout/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Checkbox, Input } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { useAuth } from '@/features/auth/auth-context'
import { useLang } from '@/i18n'
import { applyServerErrors, blankToNull } from '@/lib/forms'
import { formatDateTime } from '@/lib/format'

import { InvitationList, InviteDialog } from './Invitations'
import { RolesEditor } from './Roles'

type User = Schemas['UserRead']
type Role = Schemas['RoleRead']
const PAGE_SIZE = 25
const KEY = ['users'] as const

function useRoles() {
  return useQuery({ queryKey: ['roles'], queryFn: () => unwrap(api.GET('/api/v1/roles')), staleTime: 300_000 })
}

const createSchema = z.object({
  username: z
    .string()
    .trim()
    .min(3, 'At least 3 characters')
    .max(50)
    .regex(/^[A-Za-z0-9_.-]+$/, 'Letters, digits, . _ - only'),
  full_name: z.string().trim().min(1, 'Required').max(120),
  phone: z.string().trim().max(30),
  password: z.string().min(8, 'At least 8 characters').max(128),
  roles: z.array(z.string()).min(1, 'Choose at least one role'),
  is_active: z.boolean(),
})
const editSchema = createSchema.extend({ username: z.string(), password: z.string() })
type FormValues = z.infer<typeof createSchema>
const FIELDS = Object.keys(createSchema.shape)

function RolePicker({
  roles,
  value,
  onChange,
}: {
  roles: Role[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {roles.map((role) => (
        <label
          key={role.code}
          className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 has-[:checked]:border-primary has-[:checked]:bg-primary-soft/40"
        >
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--color-primary)]"
            checked={value.includes(role.code)}
            onChange={(e) =>
              onChange(e.target.checked ? [...value, role.code] : value.filter((r) => r !== role.code))
            }
          />
          <span>
            <span className="block text-sm font-medium">{t(`roles.${role.code}`, role.name)}</span>
            <span className="block text-xs text-muted">{role.description}</span>
          </span>
        </label>
      ))}
    </div>
  )
}

function UserDialog({
  user,
  open,
  onOpenChange,
}: {
  user: User | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const roles = useRoles()
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(user ? editSchema : createSchema) })

  useEffect(() => {
    if (!open) return
    reset({
      username: user?.username ?? '',
      full_name: user?.full_name ?? '',
      phone: user?.phone ?? '',
      password: '',
      roles: user?.roles ?? [],
      is_active: user?.is_active ?? true,
    })
  }, [open, user, reset])

  const mutation = useMutation({
    mutationFn: (v: FormValues) =>
      user
        ? unwrap(
            api.PATCH('/api/v1/users/{user_id}', {
              params: { path: { user_id: user.id } },
              body: { full_name: v.full_name, phone: blankToNull(v.phone), roles: v.roles, is_active: v.is_active },
            }),
          )
        : unwrap(
            api.POST('/api/v1/users', {
              body: {
                username: v.username,
                full_name: v.full_name,
                phone: blankToNull(v.phone),
                password: v.password,
                roles: v.roles,
              },
            }),
          ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY })
      toast.success(user ? t('common.saved') : t('common.created'))
      onOpenChange(false)
    },
  })

  const onSubmit = handleSubmit(async (v) => {
    try {
      await mutation.mutateAsync(v)
    } catch (error) {
      applyServerErrors(error, setError, FIELDS)
    }
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={user ? t('users.editTitle') : t('users.newTitle')}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="user-form" loading={isSubmitting}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="user-form" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        <Field label={t('users.username')} htmlFor="username" error={errors.username?.message}>
          <Input id="username" autoComplete="off" disabled={!!user} aria-invalid={!!errors.username} {...register('username')} />
        </Field>
        <Field label={t('users.fullName')} htmlFor="full_name" error={errors.full_name?.message}>
          <Input id="full_name" aria-invalid={!!errors.full_name} {...register('full_name')} />
        </Field>
        <Field label={t('common.phone')} htmlFor="phone" optional optionalLabel={t('common.optional')}>
          <Input id="phone" type="tel" {...register('phone')} />
        </Field>
        {!user && (
          <Field label={t('users.password')} htmlFor="password" hint={t('users.passwordHint')} error={errors.password?.message}>
            <Input id="password" type="password" autoComplete="new-password" aria-invalid={!!errors.password} {...register('password')} />
          </Field>
        )}
        <div className="sm:col-span-2">
          <Field label={t('users.roles')} error={errors.roles?.message}>
            <Controller
              control={control}
              name="roles"
              render={({ field }) => (
                <RolePicker roles={roles.data ?? []} value={field.value ?? []} onChange={field.onChange} />
              )}
            />
          </Field>
        </div>
        {user && <Checkbox label={t('common.active')} {...register('is_active')} />}
      </form>
    </Dialog>
  )
}

function ResetPasswordDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [error, setErrorText] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/users/{user_id}/reset-password', {
          params: { path: { user_id: user!.id } },
          body: { new_password: password },
        }),
      ),
    onSuccess: () => {
      toast.success(t('users.passwordReset'))
      onClose()
    },
    onError: (e) => setErrorText(e.message),
  })

  return (
    <Dialog
      open={!!user}
      onOpenChange={(o) => !o && onClose()}
      title={t('users.resetTitle', { name: user?.full_name ?? '' })}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            loading={mutation.isPending}
            onClick={() => {
              if (password.length < 8) {
                setErrorText(t('users.passwordHint'))
                return
              }
              mutation.mutate()
            }}
          >
            {t('users.resetPassword')}
          </Button>
        </>
      }
    >
      <Field label={t('users.newPassword')} htmlFor="new_password" hint={t('users.passwordHint')} error={error ?? undefined}>
        <Input
          id="new_password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
    </Dialog>
  )
}

export function UsersPage() {
  const { t } = useTranslation()
  const { lang } = useLang()
  const { can, user: me } = useAuth()
  const canWrite = can('users:write')
  const [tab, setTab] = useState<'users' | 'roles' | 'invitations'>('users')
  const [inviting, setInviting] = useState(false)
  const roles = useRoles()
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<User | null>(null)
  const [open, setOpen] = useState(false)
  const [resetting, setResetting] = useState<User | null>(null)

  const query = useQuery({
    queryKey: [...KEY, { q, page }],
    queryFn: () => unwrap(api.GET('/api/v1/users', { params: { query: { q: q || undefined, page, page_size: PAGE_SIZE } } })),
    placeholderData: keepPreviousData,
    enabled: tab === 'users',
  })

  const columns = useMemo<Column<User>[]>(() => {
    const cols: Column<User>[] = [
      {
        header: t('users.fullName'),
        cell: (u) => (
          <div>
            <div className="flex items-center gap-2 font-medium">
              {u.full_name}
              {u.id === me?.id && <Badge tone="primary">{t('users.you')}</Badge>}
            </div>
            <div className="text-xs text-muted">@{u.username}</div>
          </div>
        ),
      },
      {
        header: t('users.roles'),
        cell: (row) => (
          <div className="flex flex-wrap gap-1">
            {row.roles.map((r) => (
              <Badge key={r} tone="primary">
                {t(`roles.${r}`, r)}
              </Badge>
            ))}
          </div>
        ),
      },
      {
        header: t('users.lastLogin'),
        cell: (row) =>
          row.last_login_at ? formatDateTime(row.last_login_at, lang) : <span className="text-muted">{t('users.never')}</span>,
      },
      {
        header: t('common.status'),
        cell: (row) =>
          row.is_active ? <Badge tone="success">{t('common.active')}</Badge> : <Badge>{t('common.inactive')}</Badge>,
      },
    ]
    if (canWrite) {
      cols.push({
        id: 'actions',
        header: '',
        align: 'right',
        cell: (row) => (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="icon" aria-label={t('users.resetPassword')} title={t('users.resetPassword')} onClick={() => setResetting(row)}>
              <KeyRound className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('common.edit')}
              onClick={() => {
                setEditing(row)
                setOpen(true)
              }}
            >
              <Pencil className="size-4" />
            </Button>
          </div>
        ),
      })
    }
    return cols
  }, [t, lang, canWrite, me?.id])

  return (
    <>
      <PageHeader
        title={t('users.title')}
        description={t('users.subtitle')}
        actions={
          canWrite && (
            <>
              <Button variant="outline" onClick={() => setInviting(true)}>
                {t('invite.button')}
              </Button>
              <Button
                onClick={() => {
                  setEditing(null)
                  setOpen(true)
                }}
              >
                <Plus className="size-4" /> {t('users.new')}
              </Button>
            </>
          )
        }
      />
      <Card>
        <Toolbar>
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { value: 'users', label: t('users.usersTab') },
              { value: 'roles', label: t('users.rolesTab') },
              { value: 'invitations', label: t('invite.tab') },
            ]}
          />
          <div className="flex-1" />
          {tab === 'users' && (
            <SearchInput
              value={q}
              onChange={(v) => {
                setQ(v)
                setPage(1)
              }}
            />
          )}
        </Toolbar>
        {tab === 'invitations' ? (
          <InvitationList />
        ) : tab === 'roles' ? (
          <RolesEditor />
        ) : query.isError ? (
          <div className="p-4">
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          </div>
        ) : (
          <>
            <DataTable columns={columns} data={query.data?.items} loading={query.isPending} />
            <Pagination page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPageChange={setPage} />
          </>
        )}
      </Card>
      <UserDialog user={editing} open={open} onOpenChange={setOpen} />
      <InviteDialog open={inviting} onOpenChange={setInviting} roles={roles.data ?? []} />
      {/* key remounts the dialog so each user starts with an empty form */}
      <ResetPasswordDialog key={resetting?.id ?? 'none'} user={resetting} onClose={() => setResetting(null)} />
    </>
  )
}
