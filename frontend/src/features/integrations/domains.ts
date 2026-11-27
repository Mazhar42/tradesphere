import { useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/api/client'

export function useDomains() {
  return useQuery({
    queryKey: ['domains'],
    queryFn: () => unwrap(api.GET('/api/v1/integrations/domains')),
    staleTime: 60_000,
  })
}

/** Which domain's documents a screen enters. */
export const SCREEN_DOMAINS: [string, string][] = [
  ['/sales', 'sales'],
  ['/returns', 'sales'],
  ['/purchases', 'purchasing'],
  ['/stock', 'stock'],
]
