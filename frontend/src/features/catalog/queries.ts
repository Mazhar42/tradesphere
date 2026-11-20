import { useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/api/client'

export const catalogKeys = {
  categories: ['categories'] as const,
  brands: ['brands'] as const,
  uoms: ['uoms'] as const,
  products: ['products'] as const,
}

export function useCategories() {
  return useQuery({
    queryKey: catalogKeys.categories,
    queryFn: () => unwrap(api.GET('/api/v1/categories')),
    staleTime: 60_000,
  })
}

export function useBrands() {
  return useQuery({
    queryKey: catalogKeys.brands,
    queryFn: () => unwrap(api.GET('/api/v1/brands')),
    staleTime: 60_000,
  })
}

export function useUoms() {
  return useQuery({
    queryKey: catalogKeys.uoms,
    queryFn: () => unwrap(api.GET('/api/v1/uoms')),
    staleTime: 60_000,
  })
}
