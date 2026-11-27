import { RouterProvider } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import bn from '@/i18n/bn.json'
import en from '@/i18n/en.json'
import { router } from '@/router'

import { ErrorBoundary } from './ErrorBoundary'
import { CrashPage } from './errors'

function Broken(): never {
  throw new Error('internal detail')
}

describe('error and not-found screens', () => {
  afterEach(async () => {
    vi.restoreAllMocks()
    await i18n.changeLanguage('en')
  })

  it('shows a not-found page for an unknown address', async () => {
    window.history.replaceState(null, '', '/no-such-page')
    await router.load()

    render(<RouterProvider router={router} context={{ auth: undefined! }} />)

    expect(await screen.findByRole('heading', { name: en.notFound.title })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: en.notFound.home })).toHaveAttribute('href', '/')
  })

  it('replaces a crashed screen with a message that hides the error text', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(en.crash.title)
    expect(screen.getByRole('button', { name: en.crash.reload })).toBeInTheDocument()
    expect(screen.queryByText(/internal detail/)).not.toBeInTheDocument()
  })

  it('is translated to Bangla and used by the router for route errors', async () => {
    await i18n.changeLanguage('bn')

    render(<CrashPage />)

    expect(screen.getByRole('alert')).toHaveTextContent(bn.crash.title)
    expect(router.options.defaultErrorComponent).toBe(CrashPage)
  })
})
