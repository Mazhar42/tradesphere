# Impact note: opening receivable items (milestone 7.6) — **awaiting approval, not implemented**

## Today
Customer dues are opened as amounts per customer (optionally with a reference). They are part of the customer's balance and ageing shows them as *on account*, with no invoice date. A receipt is allocated automatically to the oldest posted **sales invoices** only; money beyond those stays as customer credit.

## Proposed
Store open invoices from the old system (number, date, amount) as *opening items*. They are not sales invoices: no stock, no sales figures. `receipt_allocations` gains `opening_item_id`, with exactly one of invoice or opening item set.

## What changes for a dealership
| Behaviour | Now | After |
|---|---|---|
| Automatic allocation of a receipt | Oldest TradeSphere invoice first | Oldest **item or invoice** first, so old dues are paid off before new sales |
| Customer ageing | Old dues undated | Aged by their original date; some customers will show as more overdue than today |
| Invoice payment status | Unchanged invoices may be marked paid by a receipt | The same receipt may now go to an old item instead, leaving that invoice open |
| Credit limit | Unchanged (balance based) | Unchanged |
| Accounting entries | — | Unchanged: allocation never posts a journal entry |

## Risks
- Existing receipts are not re-allocated. Receipts recorded after the change behave differently from earlier ones.
- Ageing and "who owes what" figures move. Tell staff before enabling it.
- A reversal of a receipt must free the opening item as it frees an invoice today.

## Decision needed
1. Approve the new allocation order, or choose invoices first and old items after.
2. Should it apply to all dealerships, or only to those that load item-level dues?
