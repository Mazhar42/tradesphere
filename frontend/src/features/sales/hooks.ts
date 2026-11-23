import { type QueryClient, useQuery } from '@tanstack/react-query'

import { ApiError, api, unwrap } from '@/api/client'

export const salesKeys = {
  invoices: ['sales-invoices'] as const,
  invoice: (id: number) => ['sales-invoice', id] as const,
  receipts: ['customer-receipts'] as const,
  paymentAccounts: ['sales-payment-accounts'] as const,
}

/** Accounts money can be taken into at the counter (no balances are exposed). */
export function useSalesPaymentAccounts() {
  return useQuery({
    queryKey: salesKeys.paymentAccounts,
    queryFn: () => unwrap(api.GET('/api/v1/sales-payment-accounts')),
    staleTime: 60_000,
  })
}

/** Refresh everything a sale or receipt touches: lists, the invoice, balances, aging and stock. */
export function invalidateSales(queryClient: QueryClient, invoiceId?: number) {
  const keys: (readonly unknown[])[] = [
    salesKeys.invoices,
    salesKeys.receipts,
    ['customer-summary'],
    ['customer-ledger'],
    ['customer-aging'],
    ['sales-summary'],
    ['stock'],
  ]
  if (invoiceId !== undefined) keys.push(salesKeys.invoice(invoiceId))
  for (const key of keys) void queryClient.invalidateQueries({ queryKey: key })
}

/**
 * Open the printable invoice. The API needs the bearer token, so the page is fetched here and
 * written into a new window (a plain link would not carry the token).
 */
export async function openInvoicePrint(invoiceId: number, paper: 'a4' | 'thermal80'): Promise<void> {
  const result = await api.GET('/api/v1/sales-invoices/{invoice_id}/print', {
    params: { path: { invoice_id: invoiceId }, query: { paper } },
    parseAs: 'text',
  })
  if (!result.response.ok) {
    throw new ApiError(result.response.status, { detail: 'Could not load the invoice.' })
  }
  const html = result.data as unknown as string
  const win = window.open('', '_blank')
  if (!win) throw new ApiError(0, { detail: 'Allow pop-ups to print the invoice.' })
  win.document.open()
  win.document.write(html)
  win.document.close()
  win.focus()
  win.print()
}
