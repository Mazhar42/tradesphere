"""Consistency checks between documents, ledgers and projections.

Everything is aggregated inside PostgreSQL, so memory does not grow with history (TS-24).
Row-level security limits every query to the current dealership. Each check returns at most
`SHOWN` differences.

Run `python -m app.cli reconcile` (exit code 1 on drift); the worker also runs it nightly.
"""

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.modules.integrity.models import ReconcileRun
from app.modules.inventory.schemas import ReconcileIssue, ReconcileRead

SHOWN = 20
KEEP_RUNS = 60

# 1. stock_balances == sum of movements per (product, batch, location)
STOCK_BALANCES = """
WITH moved AS (
  SELECT product_id, coalesce(batch_id, 0) AS batch, location_id, sum(qty_base) AS qty
  FROM inventory_movements GROUP BY 1, 2, 3),
held AS (
  SELECT product_id, coalesce(batch_id, 0) AS batch, location_id, qty_base AS qty
  FROM stock_balances)
SELECT format('product/batch/location (%s, %s, %s): movements %s != balance %s',
              product_id, batch, location_id, coalesce(m.qty, 0), coalesce(h.qty, 0))
FROM moved m FULL JOIN held h USING (product_id, batch, location_id)
WHERE coalesce(m.qty, 0) <> coalesce(h.qty, 0)
"""

# 2. product_costs quantity and value == sum of movements per product
PRODUCT_COSTS = """
WITH moved AS (
  SELECT product_id, sum(qty_base) AS qty, sum(value) AS value
  FROM inventory_movements GROUP BY 1)
SELECT format('product %s: movements (%s, %s) != cost row (%s, %s)', product_id,
              coalesce(m.qty, 0), coalesce(m.value, 0),
              coalesce(c.qty_on_hand, 0), coalesce(c.total_value, 0))
FROM moved m FULL JOIN product_costs c USING (product_id)
WHERE coalesce(m.qty, 0) <> coalesce(c.qty_on_hand, 0)
   OR coalesce(m.value, 0) <> coalesce(c.total_value, 0)
"""

BALANCE_OF = """
SELECT coalesce(sum(l.debit - l.credit), 0) FROM journal_lines l
JOIN accounts a ON a.id = l.account_id WHERE a.code = '{code}'
"""

# 3. Inventory control account == total stock value
INVENTORY_ACCOUNT = f"""
SELECT format('Inventory account %s != stock value %s', gl, stock) FROM (
  SELECT ({BALANCE_OF.format(code="1200")}) AS gl,
         (SELECT coalesce(sum(total_value), 0) FROM product_costs) AS stock) x
WHERE gl <> stock
"""

# 4. the books balance
TRIAL_BALANCE = """
SELECT format('debits %s != credits %s', debit, credit) FROM (
  SELECT coalesce(sum(debit), 0) AS debit, coalesce(sum(credit), 0) AS credit
  FROM journal_lines) x
WHERE debit <> credit
"""

# 5. the daily sales projection == the posted invoices and returns it summarises
DAILY_FIELDS = (
    "qty_base",
    "free_qty_base",
    "gross_amount",
    "dealer_discount",
    "company_discount",
    "net_amount",
    "cogs",
    "returns_amount",
)
_SUMS = ", ".join(f"sum({f}) AS {f}" for f in DAILY_FIELDS)
_WANT = ", ".join(f"coalesce(e.{f}, 0)" for f in DAILY_FIELDS)
_HAVE = ", ".join(f"coalesce(s.{f}, 0)" for f in DAILY_FIELDS)
DAILY_SALES = f"""
WITH documents AS (
  SELECT i.business_date, l.product_id, l.qty_base, l.free_qty_base, l.gross_amount,
         l.dealer_discount, l.company_discount, l.line_net AS net_amount, l.cogs,
         0 AS returns_amount
  FROM sales_invoices i JOIN sales_invoice_items l ON l.invoice_id = i.id
  WHERE i.status = 'posted'
  UNION ALL
  SELECT r.business_date, l.product_id, -l.qty_base, 0, 0, -l.dealer_discount,
         -l.company_discount, -l.net_amount, -l.cogs, l.gross_amount
  FROM sales_returns r JOIN sales_return_items l ON l.return_id = r.id
  WHERE r.status = 'posted'),
e AS (SELECT business_date, product_id, {_SUMS} FROM documents GROUP BY 1, 2)
SELECT format('daily sales %s product %s: documents and projection differ',
              business_date, product_id)
FROM e FULL JOIN daily_sales_summary s USING (business_date, product_id)
WHERE ({_WANT}) IS DISTINCT FROM ({_HAVE})
"""


def _party_check(label: str, code: str, party: str, sign: int, documents: str) -> str:
    """Per party: the control account (debit positive times `sign`) equals what the documents,
    plus the journal lines of opening balances, say."""
    return f"""
WITH ledger AS (
  SELECT l.party_id, {sign} * sum(l.debit - l.credit) AS amount
  FROM journal_lines l JOIN accounts a ON a.id = l.account_id
  WHERE a.code = '{code}' AND l.party_type = '{party}' GROUP BY 1),
expected AS (
  SELECT party_id, sum(amount) AS amount FROM (
    {documents}
    UNION ALL
    SELECT l.party_id, {sign} * (l.debit - l.credit)
    FROM journal_lines l JOIN accounts a ON a.id = l.account_id
    JOIN journal_entries j ON j.id = l.entry_id
    WHERE a.code = '{code}' AND l.party_type = '{party}'
      AND j.source_doc_type IN ('OPENING', 'opening_load')
  ) d GROUP BY 1)
SELECT format('{label} %s: account %s != documents %s', party_id,
              coalesce(ledger.amount, 0), coalesce(expected.amount, 0))
FROM ledger FULL JOIN expected USING (party_id)
WHERE coalesce(ledger.amount, 0) <> coalesce(expected.amount, 0)
"""


# 7. what each customer owes = invoices - receipts - credit notes - commissions credited
RECEIVABLES = _party_check(
    "customer",
    "1100",
    "customer",
    1,
    """
    SELECT customer_id AS party_id, net_amount AS amount FROM sales_invoices
      WHERE status = 'posted'
    UNION ALL SELECT customer_id, -amount FROM customer_receipts WHERE status = 'posted'
    UNION ALL SELECT customer_id, -net_amount FROM sales_returns
      WHERE status = 'posted' AND refund_method = 'credit'
    UNION ALL SELECT customer_id, -amount FROM commission_payments
      WHERE status = 'posted' AND method = 'credit_to_account'
    """,
)

# 8. what is owed to each company = receipts of goods - payments - returns - claim offsets
PAYABLES = _party_check(
    "company",
    "2000",
    "supplier",
    -1,
    """
    SELECT g.supplier_id AS party_id, l.value AS amount
      FROM goods_receipts g JOIN goods_receipt_items l ON l.grn_id = g.id
      WHERE g.status = 'posted'
    UNION ALL SELECT supplier_id, -amount FROM supplier_payments WHERE status = 'posted'
    UNION ALL SELECT r.supplier_id, -l.value
      FROM purchase_returns r JOIN purchase_return_items l ON l.return_id = r.id
      WHERE r.status = 'posted' AND l.settlement <> 'claim'
    UNION ALL SELECT c.supplier_id, -s.amount
      FROM claim_settlements s JOIN company_claims c ON c.id = s.claim_id
      WHERE s.status = 'posted' AND s.method = 'supplier_credit'
    """,
)

# 9. the claims account = what is still open on claimable items
CLAIMS = """
WITH ledger AS (
  SELECT l.party_id, sum(l.debit - l.credit) AS amount
  FROM journal_lines l JOIN accounts a ON a.id = l.account_id
  WHERE a.code = '1300' AND l.party_type = 'supplier' GROUP BY 1),
expected AS (
  SELECT party_id, sum(amount) AS amount FROM (
    SELECT supplier_id AS party_id, book_amount AS amount FROM claimable_items
      WHERE status = 'open'
    UNION ALL
    SELECT i.supplier_id, l.book_amount - l.book_cleared
      FROM claimable_items i
      JOIN company_claim_lines l ON l.claimable_item_id = i.id
      JOIN company_claims c ON c.id = l.claim_id
      WHERE i.status = 'in_claim' AND c.status IN ('submitted', 'partially_settled')
    UNION ALL
    -- opening claims are journal lines only: they have no claim item
    SELECT l.party_id, l.debit - l.credit
      FROM journal_lines l JOIN accounts a ON a.id = l.account_id
      JOIN journal_entries j ON j.id = l.entry_id
      WHERE a.code = '1300' AND j.source_doc_type IN ('OPENING', 'opening_load')
  ) d GROUP BY 1)
SELECT format('company %s: claims account %s != open claim items %s', party_id,
              coalesce(ledger.amount, 0), coalesce(expected.amount, 0))
FROM ledger FULL JOIN expected USING (party_id)
WHERE coalesce(ledger.amount, 0) <> coalesce(expected.amount, 0)
"""

# 10. cost of goods sold = the cost on posted sales less returned cost
COGS = f"""
SELECT format('Cost of goods sold account %s != documents %s', gl, docs) FROM (
  SELECT ({BALANCE_OF.format(code="5000")}) AS gl,
    (SELECT coalesce(sum(l.cogs), 0) FROM sales_invoice_items l
       JOIN sales_invoices i ON i.id = l.invoice_id WHERE i.status = 'posted')
    - (SELECT coalesce(sum(l.cogs), 0) FROM sales_return_items l
       JOIN sales_returns r ON r.id = l.return_id WHERE r.status = 'posted') AS docs) x
WHERE gl <> docs
"""

# source_doc_type -> table of the document. Other types are not checked.
SOURCE_TABLES = {
    "sales_invoice": "sales_invoices",
    "customer_receipt": "customer_receipts",
    "sales_return": "sales_returns",
    "GRN": "goods_receipts",
    "SPAY": "supplier_payments",
    "purchase_return": "purchase_returns",
    "OPENING": "opening_balances",
    "stock_adjustment": "stock_adjustments",
    "stock_transfer": "stock_transfers",
    "claim_settlement": "claim_settlements",
    "commission_payment": "commission_payments",
    "expense": "expenses",
    "fund_transfer": "fund_transfers",
    "owner_transaction": "owner_transactions",
}

# 6. every ledger row's source document exists in this dealership (these references cannot be
#    foreign keys). Names come from the constants above, never from input.
MISSING_SOURCE = " UNION ALL ".join(
    f"SELECT format('%s {ledger} rows name a {doc_type} that does not exist', n) FROM ("
    f"SELECT count(*) AS n FROM {ledger} l WHERE l.source_doc_type = '{doc_type}' "
    f"AND NOT EXISTS (SELECT 1 FROM {table} d WHERE d.id = l.source_doc_id "
    "AND d.tenant_id = l.tenant_id)) x WHERE n > 0"
    for ledger in ("journal_entries", "inventory_movements", "claimable_items")
    for doc_type, table in SOURCE_TABLES.items()
)

CHECKS: tuple[tuple[str, str], ...] = (
    ("stock_balance", STOCK_BALANCES),
    ("product_cost", PRODUCT_COSTS),
    ("inventory_account", INVENTORY_ACCOUNT),
    ("trial_balance", TRIAL_BALANCE),
    ("daily_sales", DAILY_SALES),
    ("missing_source", MISSING_SOURCE),
    ("receivables", RECEIVABLES),
    ("payables", PAYABLES),
    ("claims", CLAIMS),
    ("cost_of_goods_sold", COGS),
)


def reconcile(session: Session) -> ReconcileRead:
    session.flush()
    issues: list[ReconcileIssue] = []
    for name, query in CHECKS:
        rows = session.scalars(text(f"SELECT * FROM ({query}) q LIMIT {SHOWN}"))
        issues.extend(ReconcileIssue(check=name, detail=detail) for detail in rows)
    return ReconcileRead(ok=not issues, issues=issues)


def run_and_store(session: Session) -> ReconcileRead:
    result = reconcile(session)
    session.add(ReconcileRun(ok=result.ok, issues=[i.model_dump() for i in result.issues]))
    session.flush()
    keep = select(ReconcileRun.id).order_by(ReconcileRun.id.desc()).limit(KEEP_RUNS)
    session.execute(delete(ReconcileRun).where(ReconcileRun.id.not_in(keep)))
    return result


def last_result(session: Session) -> ReconcileRead | None:
    run = session.scalar(select(ReconcileRun).order_by(ReconcileRun.id.desc()).limit(1))
    if run is None:
        return None
    return ReconcileRead(
        ok=run.ok, issues=[ReconcileIssue(**i) for i in run.issues], ran_at=run.ran_at
    )
