import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from '@tanstack/react-router'
import type { RouteComponent } from '@tanstack/react-router'
import { z } from 'zod'

import { AppLayout } from '@/components/layout/AppLayout'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequirePermission } from '@/features/auth/RequirePermission'

// Pages are code-split: each route downloads its own chunk on first visit.
const DashboardPage = lazyRouteComponent(() => import('@/features/dashboard/DashboardPage'), 'DashboardPage')
const ProductsPage = lazyRouteComponent(() => import('@/features/products/ProductsPage'), 'ProductsPage')
const CatalogPage = lazyRouteComponent(() => import('@/features/catalog/CatalogPage'), 'CatalogPage')
const CustomersPage = lazyRouteComponent(() => import('@/features/parties/CustomersPage'), 'CustomersPage')
const SuppliersPage = lazyRouteComponent(() => import('@/features/parties/SuppliersPage'), 'SuppliersPage')
const UsersPage = lazyRouteComponent(() => import('@/features/users/UsersPage'), 'UsersPage')
const SettingsPage = lazyRouteComponent(() => import('@/features/settings/SettingsPage'), 'SettingsPage')
const AuditPage = lazyRouteComponent(() => import('@/features/audit/AuditPage'), 'AuditPage')

export interface RouterContext {
  auth: AuthContextValue
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  validateSearch: z.object({ redirect: z.string().optional() }),
  // After a successful login the auth state flips, the router re-validates and this
  // guard sends the user on to where they were going.
  beforeLoad: ({ context, search }) => {
    if (context.auth.status === 'authenticated') {
      const target = search.redirect?.startsWith('/') ? search.redirect : '/'
      throw redirect({ to: target })
    }
  },
  component: LoginPage,
})

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  beforeLoad: ({ context, location }) => {
    if (context.auth.status !== 'authenticated') {
      throw redirect({ to: '/login', search: { redirect: location.href } })
    }
  },
  component: AppLayout,
})

function guarded(path: string, permission: string | null, Page: RouteComponent) {
  return createRoute({
    getParentRoute: () => appRoute,
    path,
    component: permission
      ? () => (
          <RequirePermission permission={permission}>
            <Page />
          </RequirePermission>
        )
      : Page,
  })
}

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    guarded('/', null, DashboardPage),
    guarded('/products', 'catalog:read', ProductsPage),
    guarded('/catalog', 'catalog:read', CatalogPage),
    guarded('/customers', 'customers:read', CustomersPage),
    guarded('/suppliers', 'suppliers:read', SuppliersPage),
    guarded('/users', 'users:read', UsersPage),
    guarded('/settings', 'settings:read', SettingsPage),
    guarded('/audit', 'audit:read', AuditPage),
  ]),
])

export const router = createRouter({
  routeTree,
  context: { auth: undefined! },
  defaultPreload: 'intent',
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
