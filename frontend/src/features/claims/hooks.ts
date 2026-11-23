import type { QueryClient } from '@tanstack/react-query'

export const claimKeys = {
  items: ['claimable-items'] as const,
  claims: ['company-claims'] as const,
  claim: (id: number) => ['company-claim', id] as const,
  commissions: ['commission-payments'] as const,
}

/** Refresh everything a claim, settlement or commission touches. */
export function invalidateClaims(queryClient: QueryClient, claimId?: number) {
  const keys: (readonly unknown[])[] = [
    claimKeys.items,
    claimKeys.claims,
    claimKeys.commissions,
    ['payment-accounts'],
    ['supplier-summary'],
    ['supplier-ledger'],
    ['customer-summary'],
    ['customer-ledger'],
  ]
  if (claimId !== undefined) keys.push(claimKeys.claim(claimId))
  for (const key of keys) void queryClient.invalidateQueries({ queryKey: key })
}
