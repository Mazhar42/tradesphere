import { useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import {
  api,
  type Me,
  refreshSession,
  setAccessToken,
  setSessionExpiredHandler,
  setTabTenant,
  unwrap,
} from '@/api/client'

import { AuthContext, type AuthContextValue, type AuthStatus } from './auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<Me | null>(null)

  // Restore the session from the refresh cookie on first load.
  useEffect(() => {
    let cancelled = false
    void refreshSession().then((session) => {
      if (cancelled) return
      setUser(session?.user ?? null)
      setStatus(session ? 'authenticated' : 'anonymous')
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    setSessionExpiredHandler(() => {
      setAccessToken(null)
      setUser(null)
      setStatus('anonymous')
      queryClient.clear()
      toast.warning(t('auth.sessionExpired'))
    })
    return () => setSessionExpiredHandler(null)
  }, [queryClient, t])

  const reload = useCallback(async () => {
    const session = await refreshSession()
    if (session) setUser(session.user)
  }, [])

  const login = useCallback(async (username: string, password: string, code?: string) => {
    const session = await unwrap(
      api.POST('/api/v1/auth/login', { body: { username, password, code: code || null } }),
    )
    setAccessToken(session.access_token)
    setTabTenant(session.user.tenant?.id ?? null)
    setUser(session.user)
    setStatus('authenticated')
  }, [])

  const switchTenant = useCallback(
    async (tenantId: number) => {
      setTabTenant(tenantId)
      const session = await refreshSession()
      // Nothing fetched for the previous dealership may stay on screen.
      queryClient.clear()
      if (session) setUser(session.user)
    },
    [queryClient],
  )

  const logout = useCallback(async () => {
    try {
      await api.POST('/api/v1/auth/logout')
    } finally {
      setAccessToken(null)
      setUser(null)
      setStatus('anonymous')
      queryClient.clear()
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      login,
      logout,
      switchTenant,
      reload,
      can: (permission) => user?.permissions.includes(permission) ?? false,
    }),
    [status, user, login, logout, switchTenant, reload],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
