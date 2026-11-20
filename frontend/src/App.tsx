import { RouterProvider } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useEffect } from 'react'

import { useAuth } from '@/features/auth/auth-context'
import { router } from '@/router'

export function App() {
  const auth = useAuth()

  // Re-run route guards whenever the auth state changes (login, logout, expiry).
  useEffect(() => {
    if (auth.status !== 'loading') void router.invalidate()
  }, [auth.status])

  if (auth.status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-primary" aria-label="Loading" />
      </div>
    )
  }
  return <RouterProvider router={router} context={{ auth }} />
}
