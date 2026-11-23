import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Link, Outlet } from '@tanstack/react-router'
import {
  BarChart3,
  Boxes,
  Building2,
  ChevronDown,
  Flag,
  HandCoins,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Receipt,
  ScrollText,
  Settings,
  ShoppingCart,
  Sun,
  Tags,
  Truck,
  Undo2,
  Users,
  UserCog,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/auth-context'
import { cn, safeStorage } from '@/lib/utils'

import { LanguageSwitch } from './LanguageSwitch'
import { Logo } from './Logo'
import { applyTheme, initialTheme, type Theme } from './theme'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  permission?: string
  soon?: boolean
}

interface NavSection {
  label: string
  items: NavItem[]
}

const SECTIONS: NavSection[] = [
  {
    label: 'nav.sectionOperations',
    items: [
      { to: '/', label: 'nav.dashboard', icon: LayoutDashboard },
      { to: '/stock', label: 'nav.stock', icon: Boxes, permission: 'inventory:read' },
      { to: '/purchases', label: 'nav.purchases', icon: Truck, permission: 'purchasing:read' },
      { to: '/sales', label: 'nav.sales', icon: ShoppingCart, permission: 'sales:read' },
      { to: '/returns', label: 'nav.returns', icon: Undo2, permission: 'sales:read' },
      { to: '/claims', label: 'nav.claims', icon: HandCoins, permission: 'claims:read' },
      { to: '/finance', label: 'nav.finance', icon: Wallet, permission: 'finance:read' },
      { to: '/accounts', label: 'nav.accounts', icon: Receipt, permission: 'finance:read' },
      { to: '/reports', label: 'nav.reports', icon: BarChart3, permission: 'reports:read' },
    ],
  },
  {
    label: 'nav.sectionMasterData',
    items: [
      { to: '/products', label: 'nav.products', icon: Package, permission: 'catalog:read' },
      { to: '/catalog', label: 'nav.catalog', icon: Tags, permission: 'catalog:read' },
      { to: '/customers', label: 'nav.customers', icon: Users, permission: 'customers:read' },
      { to: '/suppliers', label: 'nav.suppliers', icon: Building2, permission: 'suppliers:read' },
    ],
  },
  {
    label: 'nav.sectionAdmin',
    items: [
      { to: '/users', label: 'nav.users', icon: UserCog, permission: 'users:read' },
      { to: '/opening-balances', label: 'nav.opening', icon: Flag, permission: 'finance:opening' },
      { to: '/settings', label: 'nav.settings', icon: Settings, permission: 'settings:read' },
      { to: '/audit', label: 'nav.audit', icon: ScrollText, permission: 'audit:read' },
    ],
  },
]

const COLLAPSE_KEY = 'ts.sidebar.collapsed'

function Sidebar({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { t } = useTranslation()
  const { can } = useAuth()

  return (
    <nav className="flex h-full flex-col gap-6 overflow-y-auto px-3 py-4">
      <div className={cn('px-2', collapsed && 'flex justify-center px-0')}>
        <Logo inverted compact={collapsed} />
      </div>
      {SECTIONS.map((section) => {
        const items = section.items.filter((i) => !i.permission || can(i.permission))
        if (items.length === 0) return null
        return (
          <div key={section.label} className="flex flex-col gap-0.5">
            {!collapsed && (
              <p className="px-3 pb-1 text-[11px] font-semibold tracking-wider text-sidebar-muted uppercase">
                {t(section.label)}
              </p>
            )}
            {items.map((item) => {
              const Icon = item.icon
              const content = (
                <>
                  <Icon className="size-[18px] shrink-0" aria-hidden />
                  {!collapsed && <span className="flex-1 truncate">{t(item.label)}</span>}
                  {!collapsed && item.soon && (
                    <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-sidebar-muted">
                      {t('common.comingSoon')}
                    </span>
                  )}
                </>
              )
              const base = cn(
                'flex items-center gap-3 rounded-lg px-3 py-2 text-sm',
                collapsed && 'justify-center px-0',
              )
              if (item.soon) {
                return (
                  <span
                    key={item.to}
                    title={t(item.label)}
                    aria-disabled
                    className={cn(base, 'cursor-not-allowed opacity-50')}
                  >
                    {content}
                  </span>
                )
              }
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  title={collapsed ? t(item.label) : undefined}
                  onClick={onNavigate}
                  activeOptions={{ exact: item.to === '/' }}
                  className={cn(base, 'transition-colors hover:bg-sidebar-active hover:text-white')}
                  activeProps={{ className: 'bg-sidebar-active text-white font-medium' }}
                >
                  {content}
                </Link>
              )
            })}
          </div>
        )
      })}
    </nav>
  )
}

function UserMenu() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  if (!user) return null
  const initials = user.full_name
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-muted"
        >
          <span className="flex size-8 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary">
            {initials}
          </span>
          <span className="hidden text-left leading-tight sm:block">
            <span className="block font-medium">{user.full_name}</span>
            <span className="block text-xs text-muted">
              {user.roles.map((r) => t(`roles.${r}`)).join(', ')}
            </span>
          </span>
          <ChevronDown className="size-4 text-muted" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-48 rounded-lg border border-border bg-surface p-1 shadow-lg"
        >
          <div className="px-2 py-1.5 text-xs text-muted">@{user.username}</div>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item
            onSelect={() => void logout()}
            className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-muted"
          >
            <LogOut className="size-4" /> {t('auth.signOut')}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

export function AppLayout() {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState(() => safeStorage.get(COLLAPSE_KEY) === '1')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(initialTheme)

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      safeStorage.set(COLLAPSE_KEY, c ? '0' : '1')
      return !c
    })
  }
  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside
        className={cn(
          'sticky top-0 hidden h-screen shrink-0 bg-sidebar text-sidebar-foreground transition-[width] lg:block',
          collapsed ? 'w-[72px]' : 'w-64',
        )}
      >
        <Sidebar collapsed={collapsed} />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label={t('common.close')}
            className="absolute inset-0 bg-black/40"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-64 bg-sidebar text-sidebar-foreground shadow-xl">
            <Sidebar collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-surface/90 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Menu"
          >
            <Menu className="size-5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="hidden lg:inline-flex"
            onClick={toggleCollapsed}
            aria-label="Toggle sidebar"
          >
            {collapsed ? <PanelLeftOpen className="size-5" /> : <PanelLeftClose className="size-5" />}
          </Button>
          <div className="flex-1" />
          <LanguageSwitch />
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={t('theme.toggle')}>
            {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
          </Button>
          <UserMenu />
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
