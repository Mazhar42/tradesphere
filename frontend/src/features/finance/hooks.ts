import { type QueryClient, useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/api/client'

export const financeKeys = {
  expenses: ['expenses'] as const,
  transfers: ['fund-transfers'] as const,
  owner: ['owner-transactions'] as const,
}

/** A posting changes balances, the ledger, the reports and the dashboard. */
export function invalidateFinance(queryClient: QueryClient) {
  const keys: (readonly unknown[])[] = [
    financeKeys.expenses,
    financeKeys.transfers,
    financeKeys.owner,
    ['reports'],
    ['dashboard'],
    ['accounts'],
    ['payment-accounts'],
    ['sales-payment-accounts'],
  ]
  for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey })
}

/** Postable expense accounts (type expense) for the expense form. */
export function useExpenseAccounts() {
  return useQuery({
    queryKey: ['accounts', 'expense-choices'],
    queryFn: async () =>
      (await unwrap(api.GET('/api/v1/accounts'))).filter(
        (a) => a.type === 'expense' && a.is_postable && a.is_active,
      ),
    staleTime: 60_000,
  })
}
