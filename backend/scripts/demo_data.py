# ruff: noqa: S311, PLR0912, PLR0915 - a data generator: seeded randomness, one long story
"""Fill an EMPTY local database with a made-up dealership, for showing the product.

Every figure goes through the public API, so stock, accounts and reports agree with each other.
All names, shops, companies and phone numbers are invented.

    docker compose -p tradesphere-demo exec api python -m app.cli create-owner \\
        --username demo.owner --full-name "Rahim Uddin" --password "Meghna-Demo-2026"
    docker compose -p tradesphere-demo exec api python scripts/demo_data.py

Refuses to run if there are already products. See docs/demo/demo-story.md.
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "Meghna-Demo-2026"  # noqa: S105 - local demo only
# DEMO_TODAY pretends to run on an earlier day (to test the script on other weekdays).
TODAY = date.fromisoformat(
    os.environ.get("DEMO_TODAY") or datetime.now(ZoneInfo("Asia/Dhaka")).date().isoformat()
)
DAYS = 84
random.seed(2026)

TOKEN = ""


def call(
    method: str, path: str, body: Any = None, *, raw: bytes | None = None, ctype: str = ""
) -> Any:
    request = urllib.request.Request(BASE + path, method=method)  # noqa: S310
    request.add_header("Content-Type", ctype or "application/json")
    request.add_header("Idempotency-Key", uuid.uuid4().hex)
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    data = raw if raw is not None else (json.dumps(body, default=str).encode() if body else None)
    try:
        with urllib.request.urlopen(request, data) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path}: {error.code} {error.read().decode()[:600]}") from None
    return json.loads(payload) if payload else None


def get(path: str, **params: Any) -> Any:
    return call("GET", path + ("?" + urllib.parse.urlencode(params) if params else ""))


def post(path: str, body: Any = None) -> Any:
    return call("POST", path, body)


def login(username: str) -> None:
    global TOKEN  # noqa: PLW0603
    TOKEN = ""
    TOKEN = post("/auth/login", {"username": username, "password": PASSWORD})["access_token"]


# --- What the dealership sells ------------------------------------------------------------------

SUPPLIERS = {
    "SHAPLA": ("Shapla Foods Ltd", "Nasir Ahmed", "01700000101"),
    "PADMA": ("Padma Consumer Care", "Farzana Haque", "01700000102"),
    "SUNDAR": ("Sundarban Beverages", "Tapan Das", "01700000103"),
}
CATEGORIES = {
    "SNK": ("Snacks", "স্ন্যাকস"),
    "BIS": ("Biscuits", "বিস্কুট"),
    "BEV": ("Beverages", "পানীয়"),
    "PC": ("Personal care", "ব্যক্তিগত যত্ন"),
    "HOME": ("Home care", "গৃহস্থালি"),
}
BRANDS = {"SHAPLA": ("Shapla", "শাপলা"), "PADMA": ("Padma", "পদ্মা"), "SUNDAR": ("Sundar", "সুন্দর")}
# sku, English, Bangla, category, supplier, pieces per carton, cost, trade price, MRP, daily cartons
PRODUCTS = [
    (
        "SF-CHN-150",
        "Shapla Chanachur 150g",
        "শাপলা চানাচুর ১৫০ গ্রাম",
        "SNK",
        "SHAPLA",
        48,
        34,
        38,
        45,
        2.6,
    ),
    (
        "SF-CHN-300",
        "Shapla Chanachur 300g",
        "শাপলা চানাচুর ৩০০ গ্রাম",
        "SNK",
        "SHAPLA",
        24,
        66,
        73,
        85,
        1.5,
    ),
    (
        "SF-NDL-8P",
        "Shapla Noodles 8-pack",
        "শাপলা নুডলস ৮ প্যাক",
        "SNK",
        "SHAPLA",
        24,
        118,
        128,
        145,
        2.2,
    ),
    (
        "SF-PUF-025",
        "Shapla Puffed Rice Snack 25g",
        "শাপলা মুড়ি স্ন্যাক ২৫ গ্রাম",
        "SNK",
        "SHAPLA",
        120,
        8,
        9,
        10,
        1.6,
    ),
    (
        "SF-BIS-TOS",
        "Shapla Toast Biscuit 300g",
        "শাপলা টোস্ট বিস্কুট ৩০০ গ্রাম",
        "BIS",
        "SHAPLA",
        24,
        52,
        58,
        65,
        1.8,
    ),
    (
        "SF-BIS-CRM",
        "Shapla Cream Biscuit 90g",
        "শাপলা ক্রিম বিস্কুট ৯০ গ্রাম",
        "BIS",
        "SHAPLA",
        72,
        17,
        19,
        22,
        2.4,
    ),
    (
        "SF-BIS-MLK",
        "Shapla Milk Biscuit 200g",
        "শাপলা মিল্ক বিস্কুট ২০০ গ্রাম",
        "BIS",
        "SHAPLA",
        36,
        41,
        45,
        52,
        1.4,
    ),
    (
        "PD-SOAP-100",
        "Padma Beauty Soap 100g",
        "পদ্মা বিউটি সাবান ১০০ গ্রাম",
        "PC",
        "PADMA",
        72,
        39,
        43,
        50,
        1.9,
    ),
    (
        "PD-SHM-180",
        "Padma Shampoo 180ml",
        "পদ্মা শ্যাম্পু ১৮০ মিলি",
        "PC",
        "PADMA",
        24,
        148,
        162,
        190,
        0.7,
    ),
    (
        "PD-TPS-100",
        "Padma Toothpaste 100g",
        "পদ্মা টুথপেস্ট ১০০ গ্রাম",
        "PC",
        "PADMA",
        48,
        68,
        75,
        85,
        1.1,
    ),
    (
        "PD-OIL-200",
        "Padma Coconut Oil 200ml",
        "পদ্মা নারিকেল তেল ২০০ মিলি",
        "PC",
        "PADMA",
        36,
        112,
        122,
        140,
        0.8,
    ),
    (
        "PD-DET-500",
        "Padma Detergent Powder 500g",
        "পদ্মা গুঁড়া সাবান ৫০০ গ্রাম",
        "HOME",
        "PADMA",
        24,
        72,
        79,
        90,
        2.1,
    ),
    (
        "PD-DSH-250",
        "Padma Dishwash Liquid 250ml",
        "পদ্মা বাসন ধোয়ার তরল ২৫০ মিলি",
        "HOME",
        "PADMA",
        36,
        62,
        69,
        80,
        0.9,
    ),
    (
        "SB-MNG-250",
        "Sundar Mango Juice 250ml",
        "সুন্দর আমের জুস ২৫০ মিলি",
        "BEV",
        "SUNDAR",
        24,
        21,
        23.5,
        28,
        3.4,
    ),
    (
        "SB-MNG-1L",
        "Sundar Mango Juice 1L",
        "সুন্দর আমের জুস ১ লিটার",
        "BEV",
        "SUNDAR",
        12,
        88,
        96,
        110,
        1.3,
    ),
    (
        "SB-LEM-250",
        "Sundar Lemon Drink 250ml",
        "সুন্দর লেবুর পানীয় ২৫০ মিলি",
        "BEV",
        "SUNDAR",
        24,
        19,
        21.5,
        25,
        2.0,
    ),
    (
        "SB-WTR-500",
        "Sundar Drinking Water 500ml",
        "সুন্দর খাবার পানি ৫০০ মিলি",
        "BEV",
        "SUNDAR",
        24,
        10,
        12,
        15,
        2.8,
    ),
    (
        "SB-TEA-200",
        "Sundar Black Tea 200g",
        "সুন্দর চা পাতা ২০০ গ্রাম",
        "BEV",
        "SUNDAR",
        48,
        92,
        101,
        115,
        0.9,
    ),
]
# code, shop, owner, area, credit limit, terms (days), size, usual days to pay (None: slow payer)
SHOPS = [
    ("C-001", "Maa Store", "Abdul Karim", "Chashara", 150000, 14, 1.4, 11),
    ("C-002", "Bhai Bhai Store", "Selim Mia", "Fatullah", 120000, 14, 1.3, None),
    ("C-003", "Janata General Store", "Rafiqul Islam", "Chashara", 150000, 14, 1.2, 9),
    ("C-004", "New Dhaka Varieties", "Mizanur Rahman", "Siddhirganj", 100000, 7, 1.0, 7),
    ("C-005", "Al-Amin Traders", "Al Amin", "Fatullah", 200000, 21, 1.6, 17),
    ("C-006", "Bismillah Store", "Kamal Hossain", "Bandar", 80000, 14, 0.8, 12),
    ("C-007", "Rupali Departmental", "Sharmin Akter", "Chashara", 200000, 14, 1.5, 10),
    ("C-008", "Sonali Store", "Jahangir Alam", "Siddhirganj", 80000, 7, 0.7, 8),
    ("C-009", "Meghna Ghat Store", "Habib Ullah", "Sonargaon", 90000, 14, 0.9, 15),
    ("C-010", "Shanta Enterprise", "Shanta Rani", "Bandar", 120000, 14, 1.0, 20),
    ("C-011", "Hazi Sons", "Hazi Yunus", "Sonargaon", 150000, 21, 1.1, 24),
    ("C-012", "Nipa Confectionery", "Nipa Begum", "Chashara", 60000, 7, 0.6, 6),
]
SHOP_BN = {
    "C-001": "মা স্টোর",
    "C-002": "ভাই ভাই স্টোর",
}


def main() -> None:
    login("demo.owner")
    if get("/products")["total"]:
        sys.exit("There are already products here. This script only fills an empty database.")

    call(
        "PUT",
        "/settings/business-profile",
        {
            "name": "Meghna Traders (Demo)",
            "name_bn": "মেঘনা ট্রেডার্স (ডেমো)",
            "address": "B.B. Road, Chashara, Narayanganj",
            "phone": "01700000000",
            "invoice_footer": "Thank you for your business. Demo data only.",
        },
    )
    for username, name, role in (
        ("demo.manager", "Shirin Sultana", "manager"),
        ("demo.cashier", "Jamal Hossain", "cashier"),
        ("demo.storekeeper", "Karim Sheikh", "storekeeper"),
    ):
        post(
            "/users",
            {"username": username, "full_name": name, "password": PASSWORD, "roles": [role]},
        )

    units = {u["code"]: u["id"] for u in get("/uoms")}
    accounts = {a["name"]: a["id"] for a in get("/payment-accounts")}
    cash, bkash = accounts["Cash in Hand"], accounts["bKash"]
    bank = post(
        "/payment-accounts",
        {
            "name": "City Bank (demo)",
            "kind": "bank",
            "gl_account_code": "1010",
            "account_no": "0000-DEMO",
        },
    )["id"]
    start = TODAY - timedelta(days=DAYS)
    for account, amount in ((bank, 2_300_000), (cash, 200_000)):
        post(
            "/owner-transactions",
            {
                "kind": "capital",
                "amount": amount,
                "payment_account_id": account,
                "business_date": start,
                "notes": "Opening capital",
            },
        )

    suppliers = {
        code: post(
            "/suppliers",
            {
                "code": code,
                "name": name,
                "contact_person": person,
                "phone": phone,
                "address": "Dhaka",
            },
        )["id"]
        for code, (name, person, phone) in SUPPLIERS.items()
    }
    categories = {
        k: post("/categories", {"name_en": en, "name_bn": bn})["id"]
        for k, (en, bn) in CATEGORIES.items()
    }
    brands = {
        k: post("/brands", {"name_en": en, "name_bn": bn})["id"] for k, (en, bn) in BRANDS.items()
    }

    products: dict[str, dict[str, Any]] = {}
    for n, (sku, en, bn, cat, sup, pack, cost, tp, mrp, daily) in enumerate(PRODUCTS, start=1):
        made = post(
            "/products",
            {
                "sku": sku,
                "barcode": f"29900000{n:05d}",
                "name_en": en,
                "name_bn": bn,
                "category_id": categories[cat],
                "brand_id": brands[sup],
                "base_uom_id": units["PCS"],
                "dp": str(cost),
                "tp": str(tp),
                "mrp": str(mrp),
                "reorder_level": str(pack * 4),
                "uoms": [
                    {
                        "uom_id": units["CTN"],
                        "factor_to_base": pack,
                        "is_purchase_default": True,
                        "is_sales_default": True,
                    }
                ],
            },
        )
        products[sku] = {
            "id": made["id"],
            "supplier": sup,
            "pack": pack,
            "cost": cost,
            "tp": tp,
            "daily": daily,
            "stock": 0,
        }

    shops = {}
    for code, shop, person, area, limit, terms, size, pays in SHOPS:
        made = post(
            "/customers",
            {
                "code": code,
                "name": shop,
                "area": area,
                "phone": f"018000002{int(code[-3:]):02d}",
                "address": f"{area}, Narayanganj",
                "credit_limit": limit,
                "payment_terms_days": terms,
                "notes": f"Proprietor: {person}"
                + (f" · {SHOP_BN[code]}" if code in SHOP_BN else ""),
            },
        )
        shops[code] = {"id": made["id"], "size": size, "pays": pays, "open": [], "limit": limit}
    walk_in = next(c["id"] for c in get("/customers", q="WALK-IN")["items"])

    batch_no = 0
    payables: list[tuple[date, str, int, float]] = []  # due date, supplier, po, amount

    def order(
        day: date, sup: str, lines: list[tuple[str, int]], *, free: dict[str, int] | None = None
    ) -> dict[str, Any]:
        po = post(
            "/purchase-orders",
            {
                "supplier_id": suppliers[sup],
                "business_date": day,
                "expected_date": day + timedelta(days=2),
                "items": [
                    {
                        "product_id": products[s]["id"],
                        "uom_id": units["CTN"],
                        "qty": q,
                        "free_qty": (free or {}).get(s, 0),
                        "unit_price": str(products[s]["cost"] * products[s]["pack"]),
                    }
                    for s, q in lines
                ],
            },
        )
        return post(f"/purchase-orders/{po['id']}/confirm")

    def receive(day: date, po: dict[str, Any], *, damaged: int = 0, shelf: int = 300) -> float:
        nonlocal batch_no
        by_id = {p["id"]: p for p in products.values()}
        items = []
        for position, item in enumerate(po["items"]):
            batch_no += 1
            product = by_id[item["product_id"]]
            items.append(
                {
                    "po_item_id": item["id"],
                    "received_qty_base": item["ordered_qty_base"],
                    "free_qty_base": item["free_qty_base"],
                    "damaged_qty_base": damaged if position == 0 else 0,
                    "batch_no": f"B{day:%y%m}-{batch_no:03d}",
                    "mfg_date": day - timedelta(days=12),
                    "expiry_date": day + timedelta(days=shelf),
                }
            )
            product["stock"] += (
                int(float(item["ordered_qty_base"]))
                + int(float(item["free_qty_base"]))
                - (damaged if position == 0 else 0)
            )
        grn = post(
            "/goods-receipts",
            {
                "po_id": po["id"],
                "business_date": day,
                "challan_no": f"CH-{random.randint(10000, 99999)}",
                "vehicle_no": f"DHAKA-METRO-TA-{random.randint(11, 99)}",
                "items": items,
            },
        )
        posted = post(f"/goods-receipts/{grn['id']}/post")
        return float(posted["total_value"])

    def restock(day: date, *, target_days: int, below_days: int) -> None:
        for sup in SUPPLIERS:
            lines = []
            for sku, p in products.items():
                if p["supplier"] != sup or p["stock"] >= p["daily"] * p["pack"] * below_days:
                    continue
                cartons = round(p["daily"] * target_days - p["stock"] / p["pack"])
                if cartons > 0:
                    lines.append((sku, cartons))
            if not lines:
                continue
            free = {lines[0][0]: 2} if sup == "SHAPLA" else None
            po = order(day, sup, lines, free=free)
            value = receive(day, po, damaged=6 if sup == "SUNDAR" and day.day <= 7 else 0)
            payables.append((day + timedelta(days=10), sup, po["id"], value))

    def sell(
        day: date, customer: int, lines: list[dict[str, Any]], *, paid_share: float, account: int
    ) -> dict[str, Any]:
        draft = post(
            "/sales-invoices", {"customer_id": customer, "business_date": day, "items": lines}
        )
        if paid_share:
            net = Decimal(draft["net_amount"])
            amount = (
                net if paid_share == 1 else (net * Decimal(str(paid_share))).quantize(Decimal("1"))
            )
            draft = call(
                "PATCH",
                f"/sales-invoices/{draft['id']}",
                {
                    "customer_id": customer,
                    "business_date": day,
                    "items": lines,
                    "paid_amount": str(min(amount, net)),
                    "payment_account_id": account,
                },
            )
        return post(f"/sales-invoices/{draft['id']}/post")

    def basket(size: float, day: date) -> list[dict[str, Any]]:
        weights = [p["daily"] for p in products.values()]
        chosen = set(random.choices(list(products), weights=weights, k=random.randint(3, 7)))
        lines = []
        for sku in sorted(chosen):
            p = products[sku]
            cartons = max(1, round(random.uniform(0.6, 2.4) * size))
            cartons = min(cartons, p["stock"] // p["pack"])
            if cartons <= 0:
                continue
            p["stock"] -= cartons * p["pack"]
            line: dict[str, Any] = {"product_id": p["id"], "uom_id": units["CTN"], "qty": cartons}
            # Company schemes: the discount and free goods are claimed back later.
            if p["supplier"] == "SHAPLA" and cartons >= 3 and random.random() < 0.5:
                line["company_discount"] = cartons * 20
                line["company_supplier_id"] = suppliers["SHAPLA"]
            if sku == "SB-MNG-250" and cartons >= 3 and p["stock"] >= p["pack"]:
                p["stock"] -= p["pack"] // 2
                line.update(
                    free_qty="0.5",
                    free_is_company_scheme=True,
                    company_supplier_id=suppliers["SUNDAR"],
                )
            if random.random() < 0.15:
                line["dealer_discount"] = cartons * 8
            lines.append(line)
        return lines

    def collect(
        day: date, code: str, amount: Decimal, account: int, reference: str | None = None
    ) -> None:
        post(
            "/customer-receipts",
            {
                "customer_id": shops[code]["id"],
                "amount": str(amount),
                "payment_account_id": account,
                "business_date": day,
                "reference": reference,
            },
        )

    def balances() -> dict[int, float]:
        return {a["id"]: float(a["balance"]) for a in get("/payment-accounts")}

    print("Opening stock ...")
    restock(start + timedelta(days=1), target_days=14, below_days=99)

    expense = {a["code"]: a["id"] for a in get("/accounts")}

    def spend(day: date, code: str, amount: int, account: int, payee: str) -> None:
        post(
            "/expenses",
            {
                "expense_account_id": expense[code],
                "amount": amount,
                "payment_account_id": account,
                "business_date": day,
                "payee": payee,
            },
        )

    expense_codes = [a for a in get("/accounts") if a["type"] == "expense" and a["is_postable"]]
    named = {a["name_en"].lower(): a["code"] for a in expense_codes}

    def code_for(*words: str) -> str:
        for word in words:
            for name, code in named.items():
                if word in name:
                    return code
        return expense_codes[-1]["code"]

    rent, salary = code_for("rent"), code_for("salar", "wage")
    transport, power = code_for("transport", "carriage", "delivery"), code_for("electric", "utilit")

    last_invoice: dict[str, dict[str, Any]] = {}
    shop_codes = list(shops)
    day = start + timedelta(days=2)
    while day <= TODAY:
        today = day == TODAY
        if day.weekday() == 4 and not today:  # Friday: closed
            day += timedelta(days=1)
            continue
        if day.weekday() == 5 and day < TODAY - timedelta(days=3):
            restock(day, target_days=15, below_days=9)
        elif any(
            p["stock"] < p["daily"] * p["pack"] * 2.5 for p in products.values()
        ) and day < TODAY - timedelta(days=3):
            restock(day, target_days=12, below_days=4)

        for due in [x for x in payables if x[0] == day]:
            amount = round(due[3], 2)
            if balances()[bank] > amount:
                post(
                    "/supplier-payments",
                    {
                        "supplier_id": suppliers[due[1]],
                        "amount": str(amount),
                        "payment_account_id": bank,
                        "business_date": day,
                        "reference": f"CHQ-{random.randint(100000, 999999)}",
                        "allocations": [{"po_id": due[2], "amount": str(amount)}],
                    },
                )

        # Shops pay what has been outstanding for their usual number of days.
        for code, shop in shops.items():
            pays = shop["pays"]
            if pays is None:  # Bhai Bhai paid at first, then stopped about six weeks ago.
                if day > TODAY - timedelta(days=45):
                    continue
                pays = 15
            due = [x for x in shop["open"] if (day - x[0]).days >= pays]
            if due:
                total = sum((x[1] for x in due), Decimal(0))
                by_phone = random.random() < 0.3
                collect(
                    day,
                    code,
                    total,
                    bkash if by_phone else cash,
                    f"TRX{random.randint(100000, 999999)}" if by_phone else None,
                )
                shop["open"] = [x for x in shop["open"] if x not in due]

        visits = random.sample(shop_codes, k=random.randint(4, 7))
        if today:
            visits = ["C-001", "C-007", "C-003"]
        if "C-002" not in visits and day.weekday() in (0, 3) and day < TODAY - timedelta(days=9):
            visits.append("C-002")
        for code in visits:
            shop = shops[code]
            lines = basket(shop["size"], day)
            if not lines:
                continue
            owed = sum((x[1] for x in shop["open"]), Decimal(0))
            room = Decimal(shop["limit"]) - owed
            estimate = sum(
                Decimal(
                    str(
                        next(
                            p["tp"] * p["pack"]
                            for p in products.values()
                            if p["id"] == line["product_id"]
                        )
                    )
                )
                * line["qty"]
                for line in lines
            )
            share = 0.0 if room > estimate * Decimal("1.1") else 1.0
            if code == "C-002" and share == 1.0:
                continue  # no more credit, and he is not paying cash
            if share == 0.0 and random.random() < 0.25:
                share = 0.5
            invoice = sell(day, shop["id"], lines, paid_share=share, account=cash)
            due_amount = Decimal(invoice["due_amount"])
            if due_amount > 0:
                shop["open"].append((day, due_amount))
            last_invoice[code] = invoice

        for _ in range(random.randint(1, 3)):  # counter sales
            sku = random.choice([s for s, p in products.items() if p["stock"] > 30])
            qty = random.randint(2, 18)
            products[sku]["stock"] -= qty
            sell(
                day,
                walk_in,
                [{"product_id": products[sku]["id"], "qty": qty}],
                paid_share=1.0,
                account=cash,
            )

        if day.day == 3:
            spend(day, rent, 28000, bank, "Godown and shop rent")
            spend(day, power, random.randint(6200, 8400), bank, "Electricity bill")
        if day.day == 5:
            spend(day, salary, 74000, bank, "Staff salaries")
        if day.weekday() in (6, 2) and not today:
            spend(day, transport, random.randint(1800, 3600), cash, "Delivery van fuel and loading")

        if day.weekday() == 3 and not today:  # Thursday: take the week's money to the bank
            money = balances()
            for source, keep in ((cash, 120000), (bkash, 15000)):
                spare = int((money[source] - keep) // 1000 * 1000)
                if spare > 0:
                    post(
                        "/fund-transfers",
                        {
                            "from_payment_account_id": source,
                            "to_payment_account_id": bank,
                            "amount": spare,
                            "business_date": day,
                            "charges": 25 if source == bkash else 0,
                        },
                    )
        if day.day == 1:
            print(f"  ... {day}")
        day += timedelta(days=1)

    print("Returns, claims and the loose ends ...")
    # A shop sends back damaged goods.
    invoice = last_invoice["C-003"]
    returnable = get(f"/sales-invoices/{invoice['id']}/returnable")
    line = next(r for r in returnable if float(r["returnable_qty_base"]) >= 6)
    back = post(
        "/sales-returns",
        {
            "invoice_id": invoice["id"],
            "refund_method": "credit",
            "notes": "Cartons crushed in transport",
            "items": [
                {
                    "invoice_item_id": line["invoice_item_id"],
                    "batch_id": line["batch_id"],
                    "condition": "damaged",
                    "qty_base": 6,
                }
            ],
        },
    )
    post(f"/sales-returns/{back['id']}/post")

    # Shapla: one claim for the older items, partly paid. Newer items stay unclaimed.
    old_enough = TODAY - timedelta(days=30)
    waiting = get("/claimable-items", supplier_id=suppliers["SHAPLA"], page_size=100)["items"]
    older = [i["id"] for i in waiting if date.fromisoformat(i["business_date"]) <= old_enough]
    if older:
        claim = post(
            "/company-claims",
            {
                "supplier_id": suppliers["SHAPLA"],
                "claimable_item_ids": older,
                "business_date": old_enough,
                "notes": "Trade scheme claim",
            },
        )
        claim = post(f"/company-claims/{claim['id']}/submit", {"company_ref": "SF/CLM/2026/118"})
        part = (Decimal(claim["total_claimed"]) * Decimal("0.6")).quantize(Decimal("1"))
        post(
            f"/company-claims/{claim['id']}/settlements",
            {
                "method": "bank",
                "amount": str(part),
                "payment_account_id": bank,
                "business_date": TODAY - timedelta(days=12),
                "reference": "Bank advice 5521",
            },
        )
    post(
        "/commission-payments",
        {
            "customer_id": shops["C-007"]["id"],
            "amount": 2500,
            "method": "credit_to_account",
            "scheme_ref": "Quarter bonus",
            "business_date": TODAY - timedelta(days=6),
        },
    )
    post(
        "/owner-transactions",
        {
            "kind": "drawing",
            "amount": 40000,
            "payment_account_id": bank,
            "business_date": TODAY - timedelta(days=9),
            "notes": "Household",
        },
    )

    # A late delivery of juice that was already close to expiry: the dashboard should warn.
    near = order(TODAY - timedelta(days=1), "SUNDAR", [("SB-MNG-1L", 10)])
    receive(TODAY - timedelta(days=1), near, shelf=19)
    # A bulk cash buyer clears out two fast movers: they show as low stock.
    on_hand = {r["sku"]: r for r in get("/inventory/stock", page_size=100)["items"]}
    for sku in ("PD-DET-500", "PD-SOAP-100"):
        qty = int(float(on_hand[sku]["qty_sellable"])) - products[sku]["pack"]
        if qty > 0:
            sell(
                TODAY,
                walk_in,
                [{"product_id": products[sku]["id"], "qty": qty}],
                paid_share=1.0,
                account=cash,
            )
    # Waiting to be received during the demo.
    pending = order(TODAY - timedelta(days=2), "PADMA", [("PD-DET-500", 40), ("PD-SOAP-100", 25)])

    # Padma's own price list, one line not yet matched to anything we sell.
    price_list = (
        "supplier_code,supplier_sku,description,price,pack_factor,sku\n"
        "PADMA,PCC-1001,Padma Beauty Soap 100g,39,72,PD-SOAP-100\n"
        "PADMA,PCC-2005,Padma Detergent Powder 500g,72,24,PD-DET-500\n"
        "PADMA,PCC-3010,Padma Herbal Hand Wash 200ml,85,24,\n"
    )
    boundary = uuid.uuid4().hex
    disposition = "Content-Disposition: form-data; name="
    parts = [
        f'--{boundary}\r\n{disposition}"entity"\r\n\r\nsupplier_products\r\n',
        f'--{boundary}\r\n{disposition}"file"; filename="padma-prices.csv"\r\n',
        f"Content-Type: text/csv\r\n\r\n{price_list}\r\n--{boundary}--\r\n",
    ]
    job = call(
        "POST",
        "/imports",
        raw="".join(parts).encode(),
        ctype=f"multipart/form-data; boundary={boundary}",
    )
    post(f"/imports/{job['id']}/approve", {"dry_run_digest": job["dry_run_digest"]})
    for _ in range(30):
        if get(f"/imports/{job['id']}")["status"] == "completed":
            break
        time.sleep(1)
    else:
        print("WARNING: the price list is not imported yet; is the worker running?")

    # For the story: Bhai Bhai has under 2,000 taka of credit left, whatever day this runs.
    bhai = shops["C-002"]["id"]
    aging = get("/reports/customer-aging")["rows"]
    owed = Decimal(next(r["total"] for r in aging if r["customer_id"] == bhai))
    limit = (owed // 1000 + 2) * 1000
    call("PATCH", f"/customers/{bhai}", {"credit_limit": str(limit)})

    dash = get("/reports/dashboard")
    month = TODAY.replace(day=1)
    previous = (month - timedelta(days=1)).replace(day=1)
    print("\nDone. Figures for the story:")
    print(f"  Bhai Bhai Store owes {owed:,.0f} of a {limit:,.0f} limit")
    print(
        f"  Customer dues {dash['customer_dues']}, cash and bank {dash['cash_total']}, "
        f"claims outstanding {dash['claims_outstanding']}"
    )
    print(f"  Low stock {dash['low_stock_count']}, expiring soon {dash['near_expiry_count']}")
    for first, last in ((previous, month - timedelta(days=1)), (month, TODAY)):
        report = get("/reports/profit-loss", date_from=first, date_to=last)
        print(f"  {first:%B}: net sales {report['net_sales']}, net profit {report['net_profit']}")
    print(f"  Delivery waiting to be received: {pending['doc_no']}")


if __name__ == "__main__":
    main()
