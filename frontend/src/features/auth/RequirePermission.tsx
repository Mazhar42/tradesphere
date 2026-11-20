import type { ReactNode } from 'react'

import { Forbidden } from '@/components/layout/page'

import { useAuth } from './auth-context'

export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(permission) ? <>{children}</> : <Forbidden />
}
