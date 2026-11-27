# Business impact of the new reversals and checks (phase 6, ADR-012)

For the owner and the accountant to review before these reach a dealership with real books.

## Rules common to every reversal
- Nothing is edited or deleted. A mirror journal entry is posted **dated today**, and the document is marked *reversed*.
- Reversal is refused if either the document's date or today is inside the locked period.
- A document is reversed once. The action is in the audit log.
- Profit for the **original** month does not change; the correction appears in the current month. A month already reported stays as reported.

## Per document

| Document | What the reversal does | Refused when | Permission |
|---|---|---|---|
| Expense | Takes the cost out of expenses and puts the money back in the payment account | — | `finance:reverse` |
| Fund transfer | Moves the money and the charges back | — | `finance:reverse` |
| Owner capital or drawing | Mirrors it | — | `finance:reverse` |
| Commission | Returns cash or removes the customer credit; cancels the company claim item | The claim item has been submitted to the company | `claims:reverse` |
| Claim settlement or rejection | Reopens the claimed lines and removes the money, supplier credit or write-off | A later settlement of the same claim exists (undo newest first) | `claims:reverse` |
| Sales return | Takes the returned units out of stock again, restores what the customer owes or the refunded cash, restores a company-funded discount | The product has moved since; the discount claim has been submitted | `sales:reverse` |
| Purchase return | Brings the units back at the value they left at; restores the supplier balance; cancels its claim item | The product has moved since; its claim has been submitted | `purchasing:reverse` |

Owners get the two new permissions; managers too. Cashiers and storekeepers do not.

## One limitation to know about
Stock documents can only be reversed while no later movement exists for their products (this protects the moving average cost). A return **and its reversal** are both later movements, so after reversing a sales return the original invoice still cannot be reversed. Correct it with a new return instead.

## Reconciliation
Four checks are new: customer balances, company balances, company claims, and cost of goods sold, each against the documents. On a database with history, **a difference reported by a new check may be old data, not a new fault**: investigate before correcting. Corrections are made with documents, never by editing the database.

The reconcile screen now shows the last stored result (run nightly by the worker); "check now" runs it again.

## Rounding (TS-19, milestone 6.6) — documented, not changed
- Money (2 decimals) rounds **half up**.
- Unit cost, average cost and unit price (4 decimals) use Python's default, **half even**.
The difference is at the fourth decimal of a unit cost. Changing it would make new figures differ from how history was computed, so it stays unless the accountant asks for it.

## Not covered
Stock adjustments and transfers are corrected by an opposite document, as before. There is no manual journal.
