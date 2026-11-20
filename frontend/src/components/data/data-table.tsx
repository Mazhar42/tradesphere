import { type ColumnDef, type RowData, tableFeatures, useTable } from '@tanstack/react-table'
import { Inbox } from 'lucide-react'
import { type ReactNode, useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

/**
 * App-level column definition. Pages describe columns with this small API;
 * TanStack Table (v9) is an implementation detail of <DataTable>, so library
 * upgrades stay contained in this file. Sorting/visibility features can be
 * registered here later without touching pages.
 */
export interface Column<T> {
  id?: string
  header: ReactNode
  cell: (row: T) => ReactNode
  align?: 'right' | 'center'
  className?: string
}

const features = tableFeatures({})
type Features = typeof features
const EMPTY: never[] = []

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[] | undefined
  loading?: boolean
  onRowClick?: (row: T) => void
  emptyAction?: ReactNode
}

export function DataTable<T extends RowData>({
  columns,
  data,
  loading,
  onRowClick,
  emptyAction,
}: DataTableProps<T>) {
  const { t } = useTranslation()

  const defs = useMemo(
    () =>
      columns.map(
        (col, index): ColumnDef<Features, T, unknown> => ({
          id: col.id ?? `col-${index}`,
          header: () => col.header,
          cell: ({ row }) => col.cell(row.original),
        }),
      ),
    [columns],
  )
  const byId = useMemo(
    () => new Map(columns.map((col, index) => [col.id ?? `col-${index}`, col])),
    [columns],
  )
  const rows = data ?? (EMPTY as T[])
  const table = useTable({ features, columns: defs, data: rows })

  const alignClass = (col: Column<T> | undefined) =>
    cn(col?.align === 'right' && 'text-right', col?.align === 'center' && 'text-center')

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id} className="border-b border-border bg-surface-muted/60">
              {group.headers.map((header) => {
                const col = byId.get(header.column.id)
                return (
                  <th
                    key={header.id}
                    className={cn(
                      'px-4 py-2.5 text-left text-xs font-semibold tracking-wide text-muted uppercase',
                      alignClass(col),
                      col?.className,
                    )}
                  >
                    {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 5 }).map((_, i) => (
              <tr key={`skeleton-${i}`} className="border-b border-border last:border-0">
                {columns.map((_c, j) => (
                  <td key={j} className="px-4 py-3">
                    <div className="h-4 animate-pulse rounded bg-surface-muted" />
                  </td>
                ))}
              </tr>
            ))}
          {!loading &&
            table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                className={cn(
                  'border-b border-border last:border-0',
                  onRowClick && 'cursor-pointer hover:bg-surface-muted/60',
                )}
              >
                {row.getAllCells().map((cell) => {
                  const col = byId.get(cell.column.id)
                  return (
                    <td
                      key={cell.id}
                      className={cn(
                        'px-4 py-3 align-middle',
                        col?.align === 'right' && 'num',
                        alignClass(col),
                        col?.className,
                      )}
                    >
                      <table.FlexRender cell={cell} />
                    </td>
                  )
                })}
              </tr>
            ))}
        </tbody>
      </table>
      {!loading && rows.length === 0 && (
        <div className="flex flex-col items-center gap-2 px-4 py-14 text-center">
          <Inbox className="size-8 text-muted" aria-hidden />
          <p className="font-medium">{t('common.noResults')}</p>
          <p className="text-sm text-muted">{t('common.noResultsHint')}</p>
          {emptyAction && <div className="mt-2">{emptyAction}</div>}
        </div>
      )}
    </div>
  )
}
