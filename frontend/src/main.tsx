import './index.css'
import './i18n'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Toaster } from 'sonner'

import { ApiError } from '@/api/client'
import { App } from '@/App'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { applyTheme, initialTheme } from '@/components/layout/theme'
import { AuthProvider } from '@/features/auth/AuthProvider'

applyTheme(initialTheme())

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // Don't retry client errors (validation, permission, not found).
      retry: (count, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <App />
          <Toaster richColors position="top-right" closeButton />
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
