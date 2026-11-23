import { useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/api/client'
import { useLang } from '@/i18n'
import { cmpMoney, subMoney } from '@/lib/decimal'
import { formatQty, type UnitFactor } from '@/lib/units'

export const purchaseKeys = {
  orders: ['purchase-orders'] as const,
  receipts: ['goods-receipts'] as const,
  payments: ['supplier-payments'] as const,
}

/** Base quantity shown as "2 CTN 5 PCS" using the line's entry unit. */
export function useQtyFormatter() {
  const { lang } = useLang()
  return (baseQty: string, unitCode: string, factor: string, baseCode: string): string => {
    const units: UnitFactor[] = Number(factor) > 1 ? [{ code: unitCode, factor }] : []
    return formatQty(baseQty, units, baseCode, lang)
  }
}

export function useActiveSuppliers() {
  return useQuery({
    queryKey: ['suppliers', 'all-active'],
    queryFn: async () =>
      (await unwrap(api.GET('/api/v1/suppliers', { params: { query: { is_active: true, page_size: 100 } } }))).items,
    staleTime: 60_000,
  })
}

export function usePaymentAccounts() {
  return useQuery({
    queryKey: ['payment-accounts'],
    queryFn: () => unwrap(api.GET('/api/v1/payment-accounts')),
    staleTime: 10_000,
  })
}

const PAYABLE = ['confirmed', 'partially_received', 'received', 'closed_short']

/** What can still be allocated to an order: ordered minus cancelled minus already paid. */
export function useAllocatableOrders(supplierId: number | null) {
  return useQuery({
    queryKey: ['purchase-orders', 'allocatable', supplierId],
    enabled: supplierId !== null,
    queryFn: async () => {
      const page = await unwrap(
        api.GET('/api/v1/purchase-orders', { params: { query: { supplier_id: supplierId!, page_size: 100 } } }),
      )
      return page.items
        .filter((po) => PAYABLE.includes(po.status))
        .map((po) => ({
          ...po,
          room: subMoney(subMoney(po.summary.ordered_value, po.summary.cancelled_value), po.summary.paid_value),
        }))
        .filter((po) => cmpMoney(po.room, '0') > 0)
    },
  })
}
