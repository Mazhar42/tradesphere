"""Load test for sales posting (plan §10). Standard library only, so it runs in any image.

Creates its own products, customers and opening stock (dated yesterday), then posts sales from
several threads at once, each sale = create invoice + post. Reports throughput, latency
percentiles and errors, then checks that stock, cost and the books still reconcile.

Run it against a stack you can afford to fill with test data, never against live data:

    docker run --rm --network tradesphere-prod_default -v "<repo>/backend:/app" tradesphere-api \
        python scripts/loadtest_sales.py --base-url http://api:8000 --user owner --password ...
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = token

    def call(
        self, method: str, path: str, body: Any = None, *, idem: bool = False
    ) -> tuple[int, Any, float]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idem:
            headers["Idempotency-Key"] = uuid.uuid4().hex
        data = json.dumps(body).encode() if body is not None else None
        # The base URL is operator-supplied (http or https), never user input.
        request = urllib.request.Request(  # noqa: S310
            self.base + path, data=data, headers=headers, method=method
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        elapsed = time.perf_counter() - started
        payload = json.loads(raw) if raw else None
        return status, payload, elapsed


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    sales: int = 0


def one_sale(client: ApiClient, customer_id: int, product_id: int, stats: Stats) -> None:
    status, invoice, t_create = client.call(
        "POST",
        "/sales-invoices",
        {
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "qty": "1"}],
            "paid_amount": "0",
        },
    )
    if status != 201:
        stats.failures[f"create {status}"] = stats.failures.get(f"create {status}", 0) + 1
        return
    status, body, t_post = client.call("POST", f"/sales-invoices/{invoice['id']}/post", idem=True)
    stats.latencies.append(t_create + t_post)
    if status == 200:
        stats.sales += 1
    else:
        key = f"post {status} {body.get('code') if isinstance(body, dict) else ''}".strip()
        stats.failures[key] = stats.failures.get(key, 0) + 1


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def receive_stock(client: ApiClient, run: str, product_ids: list[int]) -> bool:
    """Buy and receive stock for the run through a purchase order and a posted goods receipt.

    This repeats on every run (the opening-balance wizard is one-time per database).
    """
    status, supplier, _ = client.call("POST", "/suppliers", {"name": f"LT {run} supplier"})
    if status != 201:
        print(f"supplier create failed: {status} {supplier}", file=sys.stderr)
        return False
    lines = [{"product_id": pid, "qty": "1000000", "unit_price": "80"} for pid in product_ids]
    status, po, _ = client.call(
        "POST", "/purchase-orders", {"supplier_id": supplier["id"], "items": lines}
    )
    if status != 201:
        print(f"purchase order failed: {status} {po}", file=sys.stderr)
        return False
    status, po, _ = client.call("POST", f"/purchase-orders/{po['id']}/confirm")
    if status != 200:
        print(f"purchase order confirm failed: {status} {po}", file=sys.stderr)
        return False
    receipt_items = [
        {"po_item_id": item["id"], "received_qty_base": "1000000"} for item in po["items"]
    ]
    status, grn, _ = client.call(
        "POST", "/goods-receipts", {"po_id": po["id"], "items": receipt_items}
    )
    if status != 201:
        print(f"goods receipt failed: {status} {grn}", file=sys.stderr)
        return False
    status, grn, _ = client.call("POST", f"/goods-receipts/{grn['id']}/post", idem=True)
    if status != 200:
        print(f"goods receipt post failed: {status} {grn}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="owner")
    parser.add_argument("--password", required=True)
    parser.add_argument("--products", type=int, default=10)
    parser.add_argument("--customers", type=int, default=20)
    parser.add_argument("--sales", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    anon = ApiClient(args.base_url)
    status, login, _ = anon.call(
        "POST", "/auth/login", {"username": args.user, "password": args.password}
    )
    if status != 200:
        print(f"login failed: {status} {login}", file=sys.stderr)
        return 2
    client = ApiClient(args.base_url, login["access_token"])

    run = uuid.uuid4().hex[:6].upper()
    _, uoms, _ = client.call("GET", "/uoms")
    pcs = next(u["id"] for u in uoms if u["code"] == "PCS")

    product_ids: list[int] = []
    for index in range(args.products):
        status, product, _ = client.call(
            "POST",
            "/products",
            {
                "sku": f"LT{run}{index:02d}",
                "name_en": f"Load test {index}",
                "base_uom_id": pcs,
                "track_batch": False,
                "track_expiry": False,
                "dp": "100",
                "tp": "120",
            },
        )
        if status != 201:
            print(f"product create failed: {status} {product}", file=sys.stderr)
            return 2
        product_ids.append(product["id"])

    if not receive_stock(client, run, product_ids):
        return 2

    customer_ids: list[int] = []
    for index in range(args.customers):
        status, customer, _ = client.call(
            "POST", "/customers", {"name": f"LT {run} shop {index}", "credit_limit": "1000000000"}
        )
        customer_ids.append(customer["id"])

    stats = Stats()
    jobs = [
        (customer_ids[i % len(customer_ids)], product_ids[i % len(product_ids)])
        for i in range(args.sales)
    ]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one_sale, client, c, p, stats) for c, p in jobs]
        for future in futures:
            future.result()
    wall = time.perf_counter() - started

    _, reconcile, _ = client.call("GET", "/inventory/reconcile")
    print(
        f"run {run}: {args.sales} sales attempted, {stats.sales} posted, {args.concurrency} threads"
    )
    print(f"wall time {wall:.1f}s, throughput {stats.sales / wall:.1f} posted sales/s")
    if stats.latencies:
        print(
            "create+post latency ms: "
            f"p50 {percentile(stats.latencies, 50) * 1000:.0f}, "
            f"p95 {percentile(stats.latencies, 95) * 1000:.0f}, "
            f"max {max(stats.latencies) * 1000:.0f}, "
            f"mean {statistics.fmean(stats.latencies) * 1000:.0f}"
        )
    if stats.failures:
        print(f"failures: {stats.failures}")
    print(f"reconcile ok: {reconcile.get('ok')}")
    if reconcile.get("issues"):
        print(f"reconcile issues: {reconcile['issues'][:5]}")
    return 0 if not stats.failures and reconcile.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
