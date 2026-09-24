#!/usr/bin/env python3
"""gen_inputs.py — build the rev.3 inputs (seeded, deterministic).

A 2025 stock ledger per wine: opening cost layers (2024), receipts, sales, write-offs,
supplier returns and customer returns. The generator simulates FIFO exactly as WM-STD-1 rev.3
states it, then prices each wine's sales so that its realized margin lands close to its
category minimum. Any costing error (order, layer choice, returns, write-offs) then moves
the margin across the line or moves the cost-of-bottles-sold total.

Writes environment/input/{stock_movements.csv, opening_stock.csv, wine_manifest.csv,
supplier_terms.csv}. Run solution/compute_gold.py afterwards.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
rng = random.Random(20250925)
D = Decimal

TERMS = [  # supplier, freight, basis, case_size, incoterm
    ("Napa Ridge Imports", "1.50", "per bottle", 12, "FCA"),
    ("Napa Valley Co", "1.35", "per bottle", 12, "FCA"),
    ("Bordeaux Negoce", "26.40", "per case", 12, "EXW"),
    ("Left Bank Brokers", "2.40", "per bottle", 6, "EXW"),
    ("Loire Direct", "1.30", "per bottle", 12, "EXW"),
    ("Willamette Partners", "1.10", "per bottle", 12, "FCA"),
    ("Reims Cellars", "15.60", "per case", 6, "EXW"),
    ("Mosel Handel", "1.90", "per bottle", 12, "EXW"),
    ("Rioja Direct", "1.40", "per bottle", 12, "EXW"),
    ("Douro Trading", "1.75", "per bottle", 6, "EXW"),
    ("Barossa Exports", "14.40", "per case", 6, "FOB"),
    ("Tuscan Vines", "21.60", "per case", 12, "EXW"),
    ("Cape Winelands Ltd", "2.30", "per bottle", 6, "FOB"),
    ("Mendoza Andes SA", "27.00", "per case", 12, "FOB"),
]
FREIGHT = {s.casefold(): (D(f) / c if b == "per case" else D(f)) for s, f, b, c, _ in TERMS}
MIN = {"still": D("20"), "sparkling": D("25"), "fortified": D("18")}

WINES = [  # name, category, expected supplier
    ("Cabernet Sauvignon Oakville", "still", "Napa Ridge Imports"),
    ("Pauillac Grand Cru", "still", "Bordeaux Negoce"),
    ("Pinot Noir Dundee Hills", "still", "Willamette Partners"),
    ("Champagne Brut Reserve", "sparkling", "Reims Cellars"),
    ("Merlot Carneros", "still", "Napa Ridge Imports"),
    ("Riesling Kabinett", "still", "Mosel Handel"),
    ("Prosecco Superiore", "sparkling", "Tuscan Vines"),
    ("Rioja Reserva", "still", "Rioja Direct"),
    ("Tawny Port 10 Year", "fortified", "Douro Trading"),
    ("Shiraz Barossa", "still", "Barossa Exports"),
    ("Chianti Classico", "still", "Tuscan Vines"),
    ("Sancerre", "still", "Loire Direct"),
    ("Barolo", "still", "Tuscan Vines"),
    ("Vintage Port", "fortified", "Douro Trading"),
    ("Cava Brut", "sparkling", "Rioja Direct"),
    ("Gruner Veltliner", "still", "Mosel Handel"),
    ("Sauternes", "still", "Bordeaux Negoce"),
    ("Margaux", "still", "Bordeaux Negoce"),
    ("Pomerol", "still", "Bordeaux Negoce"),
    ("Sekt Riesling", "sparkling", "Mosel Handel"),
    ("Zinfandel Lodi", "still", "Napa Ridge Imports"),
    ("Malbec Reserve", "still", "Mendoza Andes SA"),
    ("Tempranillo Crianza", "still", "Rioja Direct"),
    ("Cremant de Loire", "sparkling", "Loire Direct"),
    ("Madeira 5 Year", "fortified", "Douro Trading"),
    ("Chenin Blanc Stellenbosch", "still", "Cape Winelands Ltd"),
    ("Brunello di Montalcino", "still", "Tuscan Vines"),
    ("Saint-Julien", "still", "Bordeaux Negoce"),
    ("Fino Sherry", "fortified", "Rioja Direct"),
    ("Pinotage Reserve", "still", "Cape Winelands Ltd"),
    ("Lambrusco Secco", "sparkling", "Tuscan Vines"),
    ("Syrah Paso Robles", "still", "Napa Ridge Imports"),
    ("Albarino Rias Baixas", "still", "Rioja Direct"),
    ("Hermitage", "still", "Bordeaux Negoce"),
    ("Vouvray Sec", "still", "Loire Direct"),
    ("Torrontes Salta", "still", "Mendoza Andes SA"),
]
# futures wines (index -> allocation, target offset); the rest are stock
FUTURES = {1: ("open", -6.0), 16: ("closed", -4.0), 17: ("open", 8.0), 26: ("Open", -2.5), 27: ("closed", 3.0),
           33: ("open", -1.2)}
ALTERNATE = {1: "Left Bank Brokers", 16: "Left Bank Brokers", 17: "Left Bank Brokers", 18: "Left Bank Brokers",
             27: "Left Bank Brokers", 33: "Left Bank Brokers"}
NV_ALLOWED = {3, 8, 13, 14, 19, 23, 24, 28, 30}
NO_MOVEMENT = {35}          # listed in the manifest, nothing on hand, nothing moved
OPENING_ONLY = {34}         # sells down opening stock only, no 2025 receipts
# receipt anomalies: index -> list of (receipt index, field, value)
SUPPLIER_ODD = {4: [(1, "supplier", "Napa Valley Co")], 12: [(0, "supplier", "Tuscan Vines ")],
                21: [(2, "supplier", "mendoza andes sa")], 22: [(1, "supplier", "Rioja Direct ")],
                18: [(1, "supplier", "Left Bank Brokers")], 17: [(1, "supplier", "Left Bank Brokers")],
                29: [(0, "supplier", "Barossa Exports")], 6: [(2, "supplier", "TUSCAN VINES")]}
VINTAGE_ODD = {13: [(0, "vintage", "1997")], 3: [(1, "vintage", "2026")], 14: [(2, "vintage", "NV")],
               10: [(1, "vintage", "NV")], 26: [(1, "vintage", "2026")], 33: [(0, "vintage", "2027")],
               24: [(0, "vintage", "NV")], 32: [(1, "vintage", "2000")], 20: [(2, "vintage", "2025")]}
FINE = {15: (D("19.955"), D("19.995"))}  # index -> realized-margin window (just under 20, rounds to 20.0)
OFFSETS = [-3.0, -1.4, -0.6, -0.2, -0.05, 0.05, 0.3, 0.8, 1.6, 3.0, 5.0, 9.0]


def d(s: str) -> date:
    return date.fromisoformat(s)


def money(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"))


def pick_dates(lo: date, hi: date, k: int, taken: set) -> list[date]:
    out = []
    while len(out) < k:
        c = lo + timedelta(days=rng.randint(0, (hi - lo).days))
        if c not in taken:
            taken.add(c)
            out.append(c)
    return sorted(out)


def simulate(opening, events, order_key):
    """FIFO per WM-STD-1 rev.3. Returns (ok, info) where info has per-sale draws and layer states."""
    layers = [dict(date=o["date"], cost=o["cost"], qty=o["qty"], src="OPENING")
              for o in sorted(opening, key=lambda x: x["date"])]
    sales, ok, cogs = {}, True, D(0)

    def take(q):
        nonlocal layers, ok
        draws, need = [], q
        for L in layers:
            if not need:
                break
            t = min(L["qty"], need)
            if t:
                L["qty"] -= t
                need -= t
                draws.append((t, L["cost"]))
        if need:
            ok = False
        layers = [L for L in layers if L["qty"] > 0]
        return draws

    for e in sorted(events, key=order_key):
        k = e["kind"]
        if k == "RECEIPT":
            layers.append(dict(date=e["date"], cost=e["price"] + FREIGHT[e["supplier"].strip().casefold()],
                               qty=e["qty"], src=e["uid"]))
        elif k == "SALE":
            dr = take(e["qty"])
            sales[e["uid"]] = dr
            cogs += sum(q * c for q, c in dr)
        elif k == "WRITE_OFF":
            take(e["qty"])
        elif k == "SUPPLIER_RETURN":
            need = e["qty"]
            for L in layers:
                if L["src"] == e["ref"] and need:
                    t = min(L["qty"], need)
                    L["qty"] -= t
                    need -= t
            if need:
                ok = False
            layers = [L for L in layers if L["qty"] > 0]
        elif k == "CUSTOMER_RETURN":
            if e["ref"] not in sales:
                ok = False
                continue
            dr = sales[e["ref"]]
            if not dr or dr[-1][0] < e["qty"]:
                ok = False  # the policy's matching must never span two costs
                continue
            c = dr[-1][1]
            cogs -= e["qty"] * c
            layers.append(dict(date=e["date"], cost=c, qty=e["qty"], src=e["uid"]))
    return ok, dict(sales=sales, cogs=cogs, stock=sum(L["qty"] for L in layers))


POSTED = lambda e: (e["date"], e["uid"])          # noqa: E731 — policy order (dates unique per wine)
ENTERED = lambda e: (e["entry"], e["date"], e["uid"])  # noqa: E731 — listed order


def build_wine(i: int):
    name, cat, exp = WINES[i]
    fut = FUTURES.get(i)
    c0 = D(rng.choice([7, 8, 9, 11, 12, 14, 16, 18, 21, 24, 28, 33, 38, 45, 55, 70])) + D(rng.choice(["0", "0.50", "0.25", "0.75"]))
    taken: set = set()
    uid = 0

    def nuid():
        nonlocal uid
        uid += 1
        return f"w{i}e{uid}"

    opening = []
    if i not in NO_MOVEMENT:
        n_open = 3 if i in OPENING_ONLY else rng.choices([0, 1, 2, 3], [15, 25, 35, 25])[0]
        for j, dt in enumerate(pick_dates(d("2024-07-01"), d("2024-12-20"), n_open, set())):
            opening.append(dict(date=dt.isoformat(), qty=rng.choice([6, 9, 12, 18, 24, 30]),
                                cost=money(c0 * (D("0.86") + D("0.035") * j) + D(rng.choice(["0", "0.40", "0.85"])))))
    if i in NO_MOVEMENT:
        return opening, [], None

    events = []
    n_rec = 0 if i in OPENING_ONLY else rng.choice([2, 3, 3, 4])
    rec_dates = pick_dates(d("2025-01-08"), d("2025-10-15"), n_rec, taken)
    if not opening and rec_dates:
        rec_dates[0] = min(rec_dates[0], d("2025-02-10"))
    for j, dt in enumerate(rec_dates):
        vint = "2026" if fut else ("NV" if i in NV_ALLOWED and rng.random() < 0.6 else str(rng.choice([2018, 2019, 2020, 2021, 2022, 2023])))
        events.append(dict(kind="RECEIPT", date=dt.isoformat(), qty=rng.choice([12, 18, 24, 36, 48]),
                           price=money(c0 * (D("1.0") + D("0.05") * j) + D(rng.choice(["0", "0.30", "0.65"]))),
                           supplier=exp, vintage=vint, uid=nuid(), ref=""))
    for j, field_, val in SUPPLIER_ODD.get(i, []) + VINTAGE_ODD.get(i, []):
        recs = [e for e in events if e["kind"] == "RECEIPT"]
        if j < len(recs):
            recs[j][field_] = val
    first_stock = d(opening[0]["date"]) + timedelta(days=1) if opening else d(events[0]["date"]) + timedelta(days=1)
    first_stock = max(first_stock, d("2025-01-03"))
    n_sales = rng.randint(5, 9)
    for dt in pick_dates(first_stock, d("2025-12-19"), n_sales, taken):
        events.append(dict(kind="SALE", date=dt.isoformat(), qty=0, price=D(0), uid=nuid(), ref=""))
    if rng.random() < 0.4:
        dt = pick_dates(first_stock, d("2025-11-30"), 1, taken)[0]
        events.append(dict(kind="WRITE_OFF", date=dt.isoformat(), qty=rng.randint(1, 4), uid=nuid(), ref=""))

    # size the sales so they eat most of the stock and cross layers
    stock_total = sum(o["qty"] for o in opening) + sum(e["qty"] for e in events if e["kind"] == "RECEIPT")
    for e in sorted(events, key=POSTED):
        if e["kind"] == "SALE":
            e["qty"] = rng.randint(3, 14)
    for _ in range(200):
        ok, info = simulate(opening, events, POSTED)
        if ok:
            break
        for e in events:  # shrink sales until feasible
            if e["kind"] == "SALE" and e["qty"] > 1:
                e["qty"] -= 1
    else:
        raise RuntimeError(f"infeasible wine {i}")
    # grow sales greedily towards ~80 % of stock
    for _ in range(400):
        sold = sum(e["qty"] for e in events if e["kind"] == "SALE")
        if sold >= stock_total * 0.8:
            break
        e = rng.choice([e for e in events if e["kind"] == "SALE"])
        e["qty"] += 1
        if not simulate(opening, events, POSTED)[0]:
            e["qty"] -= 1

    # supplier return against a later receipt
    recs = [e for e in events if e["kind"] == "RECEIPT"]
    if len(recs) >= 2 and rng.random() < 0.45:
        R = rng.choice(recs[1:])
        for _ in range(20):
            dt = pick_dates(d(R["date"]) + timedelta(days=2), min(d(R["date"]) + timedelta(days=50), d("2025-12-20")), 1, set())[0]
            if dt in taken:
                continue
            ev = dict(kind="SUPPLIER_RETURN", date=dt.isoformat(), qty=rng.randint(3, 6), uid=nuid(), ref=R["uid"])
            events.append(ev)
            if simulate(opening, events, POSTED)[0]:
                taken.add(dt)
                break
            events.remove(ev)

    # customer return against a sale that crossed layers
    ok, info = simulate(opening, events, POSTED)
    multi = [e for e in events if e["kind"] == "SALE" and len(info["sales"][e["uid"]]) >= 2]
    if multi and rng.random() < 0.65:
        S = rng.choice(multi)
        last_q = info["sales"][S["uid"]][-1][0]
        q = rng.randint(1, max(1, min(4, last_q)))
        for _ in range(20):
            dt = pick_dates(d(S["date"]) + timedelta(days=2), min(d(S["date"]) + timedelta(days=45), d("2025-12-22")), 1, set())[0]
            if dt in taken:
                continue
            ev = dict(kind="CUSTOMER_RETURN", date=dt.isoformat(), qty=q, uid=nuid(), ref=S["uid"])
            events.append(ev)
            if simulate(opening, events, POSTED)[0]:
                taken.add(dt)
                break
            events.remove(ev)

    ok, info = simulate(opening, events, POSTED)
    assert ok, i

    # price the sales: realized margin close to minimum + offset
    offset = D(str(fut[1])) if fut else (D("-0.025") if i in FINE else D(str(rng.choice(OFFSETS))))
    target = MIN[cat] + offset
    cogs = info["cogs"]
    want = cogs * (1 + target / 100)
    sale_evs = [e for e in events if e["kind"] == "SALE"]
    for e in sale_evs:
        dr = info["sales"][e["uid"]]
        unit = sum(q * c for q, c in dr) / e["qty"]
        e["price"] = money(unit * (1 + target / 100) * D(str(rng.uniform(0.975, 1.025))))

    def net_rev():
        r = sum(e["qty"] * e["price"] for e in sale_evs)
        for e in events:
            if e["kind"] == "CUSTOMER_RETURN":
                r -= e["qty"] * next(s["price"] for s in sale_evs if s["uid"] == e["ref"])
        return r

    k = want / net_rev()
    for e in sale_evs:
        e["price"] = money(e["price"] * k)
    returned = {e["ref"] for e in events if e["kind"] == "CUSTOMER_RETURN"}
    adj = max((e for e in sale_evs if e["uid"] not in returned), key=lambda e: e["qty"])
    adj["price"] = money(adj["price"] + (want - net_rev()) / adj["qty"])
    if i in FINE:
        lo, hi = FINE[i]
        for _ in range(400):
            m = (net_rev() - cogs) / cogs * 100
            if lo < m < hi:
                break
            adj["price"] += D("0.01") if m <= lo else D("-0.01")
        else:
            raise RuntimeError(f"could not place wine {i} in its margin window")

    # late entries: some receipts and write-offs are entered after their posted date
    for e in events:
        e["entry"] = e["date"]
    candidates = [e for e in events if e["kind"] in ("RECEIPT", "WRITE_OFF")]
    rng.shuffle(candidates)
    for e in candidates[:2]:
        if rng.random() < 0.55:
            old = e["entry"]
            e["entry"] = min(d(e["date"]) + timedelta(days=rng.randint(9, 40)), d("2025-12-31")).isoformat()
            ok_f, _ = simulate(opening, events, ENTERED)
            refs_ok = all(ENTERED(x) > ENTERED(next(y for y in events if y["uid"] == x["ref"]))
                          for x in events if x["ref"])
            if not ok_f or not refs_ok:
                e["entry"] = old
    return opening, events, fut


def main() -> int:
    manifest, opening_rows, ledger = [], [], []
    for i, (name, cat, exp) in enumerate(WINES):
        wid = f"W-{i + 1:02d}"
        opening, events, fut = build_wine(i)
        wtype = "futures" if fut else "stock"
        if fut and fut[0] == "Open":
            wtype = "Futures"
        manifest.append(dict(wine_id=wid, wine_name=name, category=cat, wine_type=wtype,
                             allocation=(fut[0].lower() if fut else ""), expected_supplier=exp,
                             alternate_supplier=ALTERNATE.get(i, ""), nv_allowed="Y" if i in NV_ALLOWED else "N"))
        for o in sorted(opening, key=lambda x: x["date"], reverse=True):  # the stock system lists newest first
            opening_rows.append(dict(wine_id=wid, layer_date=o["date"], bottles=o["qty"], unit_cost=f"{o['cost']:.2f}"))
        for e in events:
            ledger.append(dict(wid=wid, i=i, **e))

    # movement ids are issued in entry order across the whole ledger
    ledger.sort(key=lambda e: (e["entry"], e["date"], e["i"], e["uid"]))
    ids = {}
    for n, e in enumerate(ledger, start=1):
        ids[(e["wid"], e["uid"])] = f"M-{1000 + n * 3 + rng.randint(0, 2)}"
    out = []
    for e in ledger:
        out.append(dict(movement_id=ids[(e["wid"], e["uid"])], posted_date=e["date"], wine_id=e["wid"],
                        movement=e["kind"], bottles=e["qty"],
                        unit_price=(f"{e['price']:.2f}" if e["kind"] in ("RECEIPT", "SALE") else ""),
                        supplier=e.get("supplier", "") if e["kind"] == "RECEIPT" else "",
                        vintage=e.get("vintage", "") if e["kind"] == "RECEIPT" else "",
                        reference=ids[(e["wid"], e["ref"])] if e["ref"] else ""))

    def write(name, header, rows_):
        with (INPUT / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            for r in rows_:
                w.writerow([r[h] for h in header])

    write("stock_movements.csv", ["movement_id", "posted_date", "wine_id", "movement", "bottles", "unit_price",
                                  "supplier", "vintage", "reference"], out)
    write("opening_stock.csv", ["wine_id", "layer_date", "bottles", "unit_cost"], opening_rows)
    write("wine_manifest.csv", ["wine_id", "wine_name", "category", "wine_type", "allocation", "expected_supplier",
                                "alternate_supplier", "nv_allowed"], manifest)
    with (INPUT / "supplier_terms.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["supplier", "freight", "freight_basis", "case_size", "incoterm"])
        for t in TERMS:
            w.writerow(list(t))
    late = sum(1 for e in ledger if e["entry"] != e["date"])
    print(f"wrote {len(out)} movements ({late} entered late), {len(opening_rows)} opening layers, "
          f"{len(manifest)} wines, {len(TERMS)} suppliers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
