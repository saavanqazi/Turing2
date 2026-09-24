#!/usr/bin/env python3
"""gen_inputs.py — build the round-4 inputs: the 43 hand-designed wines (kept verbatim as
W-01..W-43) plus ~110 generated wines whose margins cluster around the category minimums so
that any freight, pack, note or dedupe error flips several rows. Deterministic (seeded).

Writes environment/input/{wine_purchases.csv, wine_manifest.csv, supplier_terms.csv,
purchasing_notes.md}. Run compute_gold.py afterwards.
"""
from __future__ import annotations

import csv
import random
from decimal import Decimal
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
rng = random.Random(20250924)

# ---------------------------------------------------------------- suppliers / terms
TERMS = [  # supplier, freight, basis, case_size, incoterm
    ("Napa Ridge Imports", "1.50", "per bottle", 12, "FCA"),
    ("Napa Valley Co", "1.50", "per bottle", 12, "FCA"),
    ("Bordeaux Negoce", "26.40", "per case", 12, "EXW"),
    ("Left Bank Brokers", "2.20", "per bottle", 6, "EXW"),
    ("Loire Direct", "1.30", "per bottle", 12, "EXW"),
    ("Willamette Partners", "1.10", "per bottle", 12, "FCA"),
    ("Finger Lakes Co", "0.90", "per bottle", 12, "FCA"),
    ("Reims Cellars", "2.60", "per bottle", 6, "EXW"),
    ("Mosel Handel", "1.90", "per bottle", 12, "EXW"),
    ("Rioja Direct", "1.40", "per bottle", 12, "EXW"),
    ("Rioja Direct SL", "1.40", "per bottle", 12, "EXW"),
    ("Douro Trading", "1.75", "per bottle", 6, "EXW"),
    ("Barossa Exports", "14.40", "per case", 6, "FOB"),
    ("Barossa Exports Pty", "14.40", "per case", 6, "FOB"),
    ("Tuscan Vines", "21.60", "per case", 12, "EXW"),
    ("Cape Winelands Ltd", "2.30", "per bottle", 6, "FOB"),
    ("Mendoza Andes SA", "27.00", "per case", 12, "FOB"),
    ("Wachau Kellerei", "1.95", "per bottle", 12, "EXW"),
]
FREIGHT = {s: (Decimal(f) / c if b == "per case" else Decimal(f)) for s, f, b, c, _ in TERMS}
# expected suppliers by category (the manifest names one of these per wine)
POOL = {
    "still": ["Napa Ridge Imports", "Bordeaux Negoce", "Willamette Partners", "Mosel Handel", "Rioja Direct",
              "Tuscan Vines", "Barossa Exports", "Cape Winelands Ltd", "Mendoza Andes SA", "Wachau Kellerei",
              "Finger Lakes Co", "Loire Direct"],
    "sparkling": ["Reims Cellars", "Tuscan Vines", "Rioja Direct", "Mosel Handel", "Loire Direct"],
    "fortified": ["Douro Trading", "Rioja Direct", "Cape Winelands Ltd"],
}
NAMES = {
    "still": ["Cabernet Franc", "Grenache Blanc", "Carmenere", "Petit Verdot", "Viognier", "Mourvedre", "Semillon",
              "Pinotage", "Blaufrankisch", "Aglianico", "Verdejo", "Torrontes", "Cinsault", "Marsanne", "Roussanne",
              "Bonarda", "Dolcetto", "Vernaccia", "Furmint", "Xinomavro", "Assyrtiko", "Tannat", "Zweigelt",
              "Chardonnay", "Sauvignon Blanc", "Cabernet Sauvignon", "Pinot Gris", "Gamay", "Sangiovese", "Montepulciano",
              "Nero d'Avola", "Primitivo", "Trebbiano", "Vermentino di Gallura", "Carignan", "Melon de Bourgogne",
              "Chenin Blanc Reserve", "Barbera d'Asti", "Corvina", "Nerello Mascalese"],
    "sparkling": ["Franciacorta", "Cremant d'Alsace", "Blanquette de Limoux", "Trento DOC", "Cava Reserva",
                  "Asti Spumante", "Champagne Rose", "Cap Classique", "Sekt Brut", "Prosecco Rose", "Cremant de Bourgogne",
                  "Champagne Blanc de Blancs", "Lambrusco Secco", "Cava Gran Reserva"],
    "fortified": ["Ruby Port", "Oloroso Sherry", "Amontillado", "Pedro Ximenez", "Late Bottled Vintage Port",
                  "Marsala Superiore", "Rutherglen Muscat", "Palo Cortado", "Bual Madeira", "Sercial Madeira",
                  "White Port", "Cream Sherry"],
}
REGIONS = ["Reserve", "Estate", "Old Vines", "Single Vineyard", "Classic", "Hillside", "Coastal", "Village", "Cru", "Selection"]
MIN = {"still": Decimal("20"), "sparkling": Decimal("25"), "fortified": Decimal("18")}


def landed_of(purchase: Decimal, freight: Decimal) -> Decimal:
    return purchase + freight


def money(x: Decimal) -> str:
    return f"{x.quantize(Decimal('0.01')):.2f}"


def date_in(lo="2025-01-06", hi="2025-11-28") -> str:
    from datetime import date, timedelta
    a, b = date.fromisoformat(lo), date.fromisoformat(hi)
    return (a + timedelta(days=rng.randint(0, (b - a).days))).isoformat()


def main() -> int:
    # keep the 43 hand-designed wines verbatim
    base_p = []
    for r in csv.DictReader((INPUT / "wine_purchases.csv").open(newline="", encoding="utf-8")):
        if int(r["wine_id"].split("-")[1]) <= 43 and r.get("po_status", "active") == "active":
            base_p.append({k: v for k, v in r.items() if k != "po_status"})
    base_m = [r for r in csv.DictReader((INPUT / "wine_manifest.csv").open(newline="", encoding="utf-8"))
              if int(r["wine_id"].split("-")[1]) <= 43]
    purchases, manifest = list(base_p), list(base_m)
    used_names = set()

    def new_name(cat):
        while True:
            nm = rng.choice(NAMES[cat]) + " " + rng.choice(REGIONS)
            if nm not in used_names:
                used_names.add(nm)
                return nm

    def add_manifest(wid, cat, exp, alt="", alloc="", nv="N", eff="2024-01-01"):
        manifest.append({"wine_id": wid, "effective_from": eff, "category": cat, "expected_supplier": exp,
                         "alternate_supplier": alt, "allocation": alloc, "nv_allowed": nv})

    def add_line(wid, name, pack, purchase, selling, vintage, supplier, wtype, po_date, status="active"):
        if status != "active":
            return  # r5: no status column; superseded/void lines no longer exist
        purchases.append({"wine_id": wid, "wine_name": name, "pack": pack, "purchase_price": purchase,
                          "selling_price": selling, "vintage": vintage, "supplier": supplier, "wine_type": wtype,
                          "po_date": po_date})

    def price_pair(landed_target_margin_pct: Decimal, freight: Decimal, base_purchase: Decimal):
        """purchase, selling per bottle such that the landed margin is exactly the target."""
        landed = base_purchase + freight
        selling = (landed * (1 + landed_target_margin_pct / 100)).quantize(Decimal("0.01"))
        return base_purchase, selling

    wid_n = 44
    # ---- special wines first (fixed ids referenced by notes / design) -------------------
    # W-120: futures, open in manifest, allocation closed by the 3 Aug note; active line in Sep, low margin
    specials = {}
    for _ in range(76):
        wid = f"W-{wid_n}"
        cat = rng.choices(["still", "sparkling", "fortified"], [0.66, 0.2, 0.14])[0]
        name = new_name(cat)
        exp = rng.choice(POOL[cat])
        futures = cat == "still" and rng.random() < 0.16
        alloc = (rng.choice(["open", "open", "closed"]) if futures else "")
        alt = ("Left Bank Brokers" if futures and exp == "Bordeaux Negoce" and rng.random() < 0.7 else "")
        nv_allowed = "Y" if (cat != "still" and rng.random() < 0.6) else "N"
        add_manifest(wid, cat, exp, alt, alloc, nv_allowed)
        supplier = exp
        roll = rng.random()
        if roll < 0.07:                       # wrong supplier (a real other supplier)
            supplier = rng.choice([s for s, *_ in TERMS if s != exp and s not in ("Rioja Direct SL", "Barossa Exports Pty")])
        elif roll < 0.10 and alt:             # alternate on a futures wine
            supplier = alt
        elif roll < 0.13:                     # case / whitespace variants of the right name
            supplier = rng.choice([exp.lower(), exp.upper(), exp + " ", " " + exp])
        po_date = date_in()
        freight = FREIGHT[supplier.strip().casefold() and next(s for s, *_ in TERMS if s.casefold() == supplier.strip().casefold())]
        # margins cluster near the minimum: -3..+3 points, with exact hits and 0.04 misses
        target = MIN[cat] + Decimal(rng.choice([-2.5, -1.2, -0.6, -0.04, 0, 0.04, 0.5, 1.1, 2.4, 4.0, 7.5, 12.0]))
        base = Decimal(rng.choice([8, 9, 11, 12, 13, 14, 16, 18, 21, 24, 27, 32, 38, 45, 60])) + Decimal(rng.choice([0, 0.5, 0.25]))
        purchase, selling = price_pair(target, freight, base)
        pack_n = rng.choices([1, 6, 12, 3], [0.72, 0.16, 0.08, 0.04])[0]
        pack = f"{pack_n}x75cl"
        purchase_s, selling_s = money(purchase * pack_n), money(selling * pack_n)
        if cat == "still":
            vintage = str(rng.choice([2015, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2025, 2000, 1999, 2026, 2026, 2027]))
            if not futures and vintage in ("2026", "2027") and rng.random() < 0.5:
                vintage = "2021"
        else:
            vintage = rng.choice(["NV", "NV", "2019", "2020", "2022", "2016"])
        wtype = rng.choice(["futures", "Futures", "FUTURES"]) if futures else rng.choice(["stock", "stock", "stock", "Stock"])
        add_line(wid, name, pack, purchase_s, selling_s, vintage, supplier, wtype, po_date)
        if rng.random() < 0.05:   # the export repeats the line verbatim, usually adjacent
            add_line(wid, name, pack, purchase_s, selling_s, vintage, supplier, wtype, po_date)
        wid_n += 1

    # ---- designed note-sensitive and scope wines -----------------------------------------
    # Douro freight rises to 2.05 from 12 May: a fortified wine at exactly 18.0 on 1.75 (po after) drops below
    add_manifest("W-120", "still", "Tuscan Vines", "", "open")
    add_line("W-120", "Chianti Riserva Futures", "6x75cl", "150.00", "198.00", "2026", "Tuscan Vines", "futures", "2025-07-14", "superseded")
    add_line("W-120", "Chianti Riserva Futures", "6x75cl", "150.00", "192.00", "2026", "Tuscan Vines", "futures", "2025-09-10", "active")
    add_manifest("W-121", "fortified", "Douro Trading", "", "", "Y")
    add_line("W-121", "Colheita Port", "1x75cl", "23.25", "29.50", "NV", "Douro Trading", "stock", "2025-05-12", "active")  # on the note date
    add_manifest("W-122", "fortified", "Douro Trading", "", "", "Y")
    add_line("W-122", "Crusted Port", "1x75cl", "23.25", "29.50", "NV", "Douro Trading", "stock", "2025-05-11", "active")  # day before
    add_line("W-122", "Crusted Port", "1x75cl", "23.25", "29.50", "NV", "Douro Trading", "stock", "2025-06-02", "superseded")  # later, not read
    add_manifest("W-133", "still", "Tuscan Vines")
    add_line("W-133", "Bolgheri Rosso", "1x75cl", "12.00", "16.60", "2022", "Tuscan Vines", "stock", "2025-08-20")  # 6-case window: low
    add_manifest("W-123", "still", "Tuscan Vines")
    add_line("W-123", "Morellino di Scansano", "1x75cl", "12.00", "16.60", "2022", "Tuscan Vines", "stock", "2025-09-01", "active")  # 12-case again
    add_manifest("W-124", "still", "Tuscan Vines")
    add_line("W-124", "Rosso di Montalcino", "1x75cl", "12.00", "16.60", "2022", "Tuscan Vines", "stock", "2025-08-31", "active")  # 6-case window
    add_manifest("W-125", "still", "Barossa Exports")
    add_line("W-125", "Grenache Barossa", "1x75cl", "11.00", "16.20", "2021", "Barossa Exports Pty", "stock", "2025-11-03", "active")  # alias ok
    add_manifest("W-126", "still", "Barossa Exports")
    add_line("W-126", "Mataro Barossa", "1x75cl", "11.00", "16.20", "2021", "Barossa Exports Pty", "stock", "2025-09-29", "active")  # too early
    add_manifest("W-127", "still", "Bordeaux Negoce", "Left Bank Brokers", "open")
    add_line("W-127", "Saint-Estephe", "6x75cl", "330.00", "450.00", "2026", "Left Bank Brokers", "futures", "2025-07-08", "active")  # on the date
    add_manifest("W-128", "still", "Rioja Direct")
    add_line("W-128", "Rioja Gran Reserva", "1x75cl", "26.00", "33.00", "2015", "Rioja Direct SL", "stock", "2025-04-20", "active")  # alias on date
    # wines with no active line at all: outside the review
    # missing from the manifest entirely
    add_line("W-131", "Zibibbo", "1x75cl", "9.00", "13.00", "2023", "Tuscan Vines", "stock", "2025-05-05", "active")
    add_line("W-132", "Kerner", "1x75cl", "10.00", "13.90", "2022", "Wachau Kellerei", "stock", "2025-06-15", "active")
    # stale manifest rows at scale (newer first / older first / future-dated)
    for wid, cat, old, new, order_new_first in [("W-50", "still", "Napa Ridge Imports", "Cape Winelands Ltd", True),
                                                 ("W-61", "still", "Mosel Handel", "Wachau Kellerei", False),
                                                 ("W-72", "sparkling", "Loire Direct", "Reims Cellars", True)]:
        idx = next(i for i, m in enumerate(manifest) if m["wine_id"] == wid)
        cur = manifest[idx]
        newer = {**cur, "expected_supplier": new, "effective_from": "2025-03-01"}
        older = {**cur, "expected_supplier": old, "effective_from": "2023-05-01"}
        manifest[idx:idx + 1] = [newer, older] if order_new_first else [older, newer]
        # make the purchase line use the NEW expected supplier so a stale read = false mismatch
        for p in purchases:
            if p["wine_id"] == wid:
                p["supplier"] = new
    for wid in ["W-55", "W-88"]:
        idx = next(i for i, m in enumerate(manifest) if m["wine_id"] == wid)
        manifest.insert(idx + 1, {**manifest[idx], "expected_supplier": "Finger Lakes Co", "effective_from": "2026-02-01"})

    # ---- write ---------------------------------------------------------------------------
    ph = ["wine_id", "wine_name", "pack", "purchase_price", "selling_price", "vintage", "supplier", "wine_type", "po_date"]
    # two of the repeated lines sit apart from their twin, as a re-exported batch would
    dup_idx = [i for i in range(1, len(purchases)) if purchases[i] == purchases[i - 1]]
    for i in dup_idx[:2]:
        purchases.append(purchases.pop(i))
    with (INPUT / "wine_purchases.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(ph); [w.writerow([r[h] for h in ph]) for r in purchases]
    mh = ["wine_id", "effective_from", "category", "expected_supplier", "alternate_supplier", "allocation", "nv_allowed"]
    with (INPUT / "wine_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(mh); [w.writerow([r[h] for h in mh]) for r in manifest]
    with (INPUT / "supplier_terms.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["supplier", "freight", "freight_basis", "case_size", "incoterm"])
        [w.writerow(list(t)) for t in TERMS]
    (INPUT / "purchasing_notes.md").write_text("""# Purchasing notes — 2025

Running notes kept by the buyer during the year. Each entry records a change to a supplier's
terms or to the manifest that had not yet reached the master files when the export was taken.

**2 February 2025.** Reims Cellars have moved to quoting freight per case of 6 bottles at 15.60
the case. The terms sheet still shows their old per-bottle figure.

**15 March 2025.** The allocation on the Sauternes (W-17) has been reopened by the négociant.
The manifest still shows it closed; treat it as open from today.

**20 April 2025.** Rioja Direct now trade as Rioja Direct SL. Invoices under the new name are
the same supplier as the manifest's Rioja Direct from today. Earlier invoices under the new
name were raised before the change was confirmed and stand as they are.

**12 May 2025.** Douro Trading's freight goes up to 2.05 a bottle.

**1 June 2025.** Tuscan Vines have switched to 6-bottle cases. Their case freight is unchanged.

**8 July 2025.** Left Bank Brokers are no longer an approved alternate for any futures wine.
The manifest has not been updated.

**3 August 2025.** The Chianti Riserva futures allocation (W-120) is now closed; the manifest
still shows it open.

**1 September 2025.** Tuscan Vines are back on 12-bottle cases. Case freight still unchanged.

**22 October 2025.** Barossa Exports have reincorporated as Barossa Exports Pty. Invoices under
the new name are the manifest's Barossa Exports from today; the two earlier ones under the new
name were issued before the paperwork went through and stand as they are.
""", encoding="utf-8")
    print(f"wrote {len(purchases)} purchase lines, {len(manifest)} manifest rows, {len(TERMS)} suppliers, 9 notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
