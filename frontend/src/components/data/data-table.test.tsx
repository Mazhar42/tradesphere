import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import '@/i18n'

import { type Column, DataTable } from './data-table'

interface Row {
  id: number
  name: string
  price: string
}

const columns: Column<Row>[] = [
  { header: 'Name', cell: (r) => r.name },
  { header: 'Price', cell: (r) => `৳${r.price}`, align: 'right' },
]

describe('DataTable', () => {
  it('renders headers and rows', () => {
    render(
      <DataTable
        columns={columns}
        data={[
          { id: 1, name: 'Fruit Drink', price: '23.00' },
          { id: 2, name: 'Biscuit', price: '10.00' },
        ]}
      />,
    )
    expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Fruit Drink' })).toBeInTheDocument()
    const price = screen.getByRole('cell', { name: '৳10.00' })
    expect(price).toHaveClass('text-right', 'num')
  })

  it('shows the empty state', () => {
    render(<DataTable columns={columns} data={[]} />)
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
  })

  it('calls onRowClick with the row', async () => {
    const onRowClick = vi.fn()
    render(
      <DataTable columns={columns} data={[{ id: 7, name: 'Soap', price: '35.00' }]} onRowClick={onRowClick} />,
    )
    await userEvent.click(screen.getByRole('cell', { name: 'Soap' }))
    expect(onRowClick).toHaveBeenCalledWith({ id: 7, name: 'Soap', price: '35.00' })
  })
})
