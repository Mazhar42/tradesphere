import { api, type Schemas, unwrap } from '@/api/client'
import { useLang } from '@/i18n'

import { AsyncPicker } from './AsyncPicker'

type Product = Schemas['ProductRead']
type Customer = Schemas['CustomerRead']

interface PickerProps<T> {
  value: T | null
  onChange: (value: T | null) => void
  disabled?: boolean
  invalid?: boolean
  id?: string
  className?: string
}

export function ProductPicker(props: PickerProps<Product>) {
  const { pick } = useLang()
  return (
    <AsyncPicker<Product>
      {...props}
      queryKey="products"
      fetcher={async (q) =>
        (
          await unwrap(
            api.GET('/api/v1/products', {
              params: { query: { q: q || undefined, is_active: true, page_size: 20 } },
            }),
          )
        ).items
      }
      getKey={(p) => p.id}
      label={(p) => `${p.sku} · ${pick(p.name_en, p.name_bn)}`}
      option={(p) => (
        <div className="flex items-center justify-between gap-3">
          <span className="truncate">{pick(p.name_en, p.name_bn)}</span>
          <span className="shrink-0 text-xs text-muted">{p.sku}</span>
        </div>
      )}
    />
  )
}

export function CustomerPicker(props: PickerProps<Customer>) {
  return (
    <AsyncPicker<Customer>
      {...props}
      queryKey="customers"
      fetcher={async (q) =>
        (
          await unwrap(
            api.GET('/api/v1/customers', {
              params: { query: { q: q || undefined, is_active: true, page_size: 20 } },
            }),
          )
        ).items
      }
      getKey={(c) => c.id}
      label={(c) => (c.shop_name ? `${c.name} · ${c.shop_name}` : c.name)}
      option={(c) => (
        <div className="flex items-center justify-between gap-3">
          <span className="truncate">{c.shop_name ? `${c.name} · ${c.shop_name}` : c.name}</span>
          <span className="shrink-0 text-xs text-muted">{c.code}</span>
        </div>
      )}
    />
  )
}
