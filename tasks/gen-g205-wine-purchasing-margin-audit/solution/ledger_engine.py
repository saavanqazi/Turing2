#!/usr/bin/env python3
"""ledger_engine.py — WM-STD-1 (rev. 3) as code: FIFO realized margin from the 2025 stock ledger.

One source of truth shared by compute_gold.py (the gold, Opts() all default) and
tools/probes.py (each probe flips one shortcut flag). Stdlib only; Decimal throughout.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, getcontext
from pathlib import Path

getcontext().prec = 40
REVIEW_YEAR = 2025
VINTAGE_MIN = 2000
MIN_MARGIN = {"still": Decimal("20"), "sparkling": Decimal("25"), "fortified": Decimal("18")}
CODES = ["MARGIN_TOO_LOW", "VINTAGE_INVALID", "SUPPLIER_MISMATCH"]
CENT = Decimal("0.01")


def norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def mnum(movement_id: str) -> int:
    return int(re.sub(r"\D", "", movement_id))


@dataclass
class Opts:
    """Every flag is a shortcut a script might take. All defaults = the policy."""
    costing: str = "fifo"             # fifo | lifo | average
    file_order: bool = False          # process the ledger in listed (movement_id) order
    opening_file_order: bool = False  # opening layers consumed in listed order (newest first)
    writeoff_ignored: bool = False
    writeoff_in_cogs: bool = False
    rts_fifo: bool = False            # supplier return taken oldest-first, not from its receipt's layer
    rts_ignored: bool = False
    cr_ignored: bool = False
    cr_cost_first: bool = False       # returned bottles costed at the sale's first-drawn layer
    cr_cost_avg: bool = False         # returned bottles costed at the sale's average cost
    cr_front: bool = False            # returned bottles put back at the front of the queue
    cr_revenue_only: bool = False     # revenue reversed, cost of the returned bottles not
    basis_ignored: bool = False
    no_freight: bool = False
    flat20: bool = False
    all_futures_exempt: bool = False
    no_futures_exempt: bool = False
    case_sensitive: bool = False
    round1dp: bool = False
    alt_on_stock: bool = False
    nv_invalid: bool = False
    no_futures_year: bool = False
    float_round: bool = False


@dataclass
class Wine:
    wine_id: str
    name: str
    category: str
    futures: bool
    exempt: bool
    revenue: Decimal = Decimal("0")
    cogs: Decimal = Decimal("0")
    net_bottles: int = 0
    margin: Decimal | None = None
    minimum: Decimal | None = None
    low: bool = False
    findings: list[str] = field(default_factory=list)
    shortfall: Decimal = Decimal("0")
    reasons: list[str] = field(default_factory=list)
    oversold: int = 0

    @property
    def finding(self) -> str:
        return "|".join(self.findings) if self.findings else "compliant"


class Layer:
    __slots__ = ("date", "cost", "qty", "src")

    def __init__(self, date, cost, qty, src):
        self.date, self.cost, self.qty, self.src = date, cost, qty, src


def evaluate(input_dir: Path, opts: Opts | None = None) -> list[Wine]:
    o = opts or Opts()
    n = (lambda s: (s or "").strip()) if o.case_sensitive else norm

    terms: dict[str, Decimal] = {}
    for r in rows(input_dir / "supplier_terms.csv"):
        amt = Decimal(r["freight"])
        if n(r["freight_basis"]) == "per case" and not o.basis_ignored:
            amt = amt / Decimal(r["case_size"])
        terms[n(r["supplier"])] = amt

    manifest = rows(input_dir / "wine_manifest.csv")
    opening: dict[str, list[dict]] = {}
    for r in rows(input_dir / "opening_stock.csv"):
        opening.setdefault(n(r["wine_id"]), []).append(r)
    ledger: dict[str, list[dict]] = {}
    for r in rows(input_dir / "stock_movements.csv"):
        ledger.setdefault(n(r["wine_id"]), []).append(r)

    out: list[Wine] = []
    for m in manifest:
        k = n(m["wine_id"])
        category = n(m["category"])
        futures = n(m["wine_type"]) == "futures"
        allocation = n(m["allocation"])
        if o.all_futures_exempt:
            exempt = futures
        elif o.no_futures_exempt:
            exempt = False
        else:
            exempt = futures and allocation == "open"
        w = Wine(m["wine_id"].strip(), m["wine_name"].strip(), category, futures, exempt)

        # ---- cost layers --------------------------------------------------------------
        layers: list[Layer] = []
        op = opening.get(k, [])
        if not o.opening_file_order:
            op = sorted(op, key=lambda r: r["layer_date"].strip())
        for r in op:
            layers.append(Layer(r["layer_date"].strip(), Decimal(r["unit_cost"]), int(r["bottles"]), "OPENING"))

        def take(qty: int):
            """Remove qty bottles; return draws [(layer, qty, unit_cost)] in draw order."""
            nonlocal layers
            if o.costing == "average" and layers:
                total_q = sum(L.qty for L in layers)
                if total_q > 0:
                    avg = sum(L.cost * L.qty for L in layers) / total_q
                    layers = [Layer(layers[0].date, avg, total_q, "AVG")]
            draws = []
            need = qty
            seq = layers if o.costing != "lifo" else list(reversed(layers))
            for L in seq:
                if need == 0:
                    break
                if L.qty == 0:
                    continue
                q = min(L.qty, need)
                L.qty -= q
                need -= q
                draws.append((L, q, L.cost))
            if need:
                w.oversold += need
                draws.append((None, need, Decimal("0")))
            layers = [L for L in layers if L.qty > 0]
            return draws

        mv = ledger.get(k, [])
        if o.file_order:
            mv = sorted(mv, key=lambda r: mnum(r["movement_id"]))
        else:
            mv = sorted(mv, key=lambda r: (r["posted_date"].strip(), mnum(r["movement_id"])))

        sales: dict[str, tuple[Decimal, list]] = {}
        receipt_suppliers: list[str] = []
        receipt_vintages: list[str] = []
        for r in mv:
            kind = norm(r["movement"])
            qty = int(r["bottles"])
            date = r["posted_date"].strip()
            mid = n(r["movement_id"])
            if kind == "receipt":
                sup = n(r["supplier"])
                freight = Decimal("0") if o.no_freight else terms.get(sup, Decimal("0"))
                layers.append(Layer(date, Decimal(r["unit_price"]) + freight, qty, mid))
                receipt_suppliers.append(sup)
                receipt_vintages.append(r["vintage"].strip())
            elif kind == "sale":
                price = Decimal(r["unit_price"])
                draws = take(qty)
                w.cogs += sum(q * c for _, q, c in draws)
                w.revenue += qty * price
                w.net_bottles += qty
                sales[mid] = (price, draws)
            elif kind == "write_off":
                if o.writeoff_ignored:
                    continue
                draws = take(qty)
                if o.writeoff_in_cogs:
                    w.cogs += sum(q * c for _, q, c in draws)
            elif kind == "supplier_return":
                if o.rts_ignored:
                    continue
                if o.rts_fifo:
                    take(qty)
                    continue
                ref = n(r["reference"])
                need = qty
                for L in layers:
                    if L.src == ref and need:
                        q = min(L.qty, need)
                        L.qty -= q
                        need -= q
                if need:
                    w.oversold += need
                layers = [L for L in layers if L.qty > 0]
            elif kind == "customer_return":
                if o.cr_ignored:
                    continue
                price, draws = sales[n(r["reference"])]
                w.revenue -= qty * price
                w.net_bottles -= qty
                seq = draws if o.cr_cost_first else list(reversed(draws))
                if o.cr_cost_avg:
                    sold_q = sum(q for _, q, _ in draws)
                    avg = sum(q * c for _, q, c in draws) / sold_q
                    back = [(qty, avg)]
                else:
                    back, need = [], qty
                    for _, q, c in seq:
                        if need == 0:
                            break
                        t = min(q, need)
                        back.append((t, c))
                        need -= t
                if not o.cr_revenue_only:
                    w.cogs -= sum(q * c for q, c in back)
                new = [Layer(date, c, q, mid) for q, c in back]
                layers = (new + layers) if o.cr_front else (layers + new)
            else:
                raise ValueError(f"unknown movement {r['movement']!r}")

        # ---- WM1 realized margin ----------------------------------------------------------
        w.minimum = Decimal("20") if o.flat20 else MIN_MARGIN[category]
        if w.net_bottles > 0 and w.cogs > 0:
            w.margin = (w.revenue - w.cogs) / w.cogs * 100
            cmp = w.margin.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if o.round1dp else w.margin
            w.low = cmp < w.minimum
        if w.low and not w.exempt:
            w.findings.append("MARGIN_TOO_LOW")
            raw = w.cogs * (1 + w.minimum / 100) - w.revenue
            w.shortfall = Decimal(str(round(float(raw), 2))) if o.float_round else cents(raw)
            w.reasons.append(f"realized margin {w.margin:.2f} pct on net revenue {w.revenue:.2f} against a FIFO "
                             f"cost of bottles sold of {w.cogs:.2f} ({category} minimum {w.minimum} pct); "
                             f"shortfall {w.shortfall:.2f}")

        # ---- WM2 vintage of every 2025 receipt ------------------------------------------------
        bad_v = []
        for v in receipt_vintages:
            if v.upper() == "NV":
                ok = (not o.nv_invalid) and n(m["nv_allowed"]) == "y"
            elif re.fullmatch(r"\d{4}", v):
                ok = VINTAGE_MIN <= int(v) <= REVIEW_YEAR + (0 if o.no_futures_year else (1 if futures else 0))
            else:
                ok = False
            if not ok:
                bad_v.append(v)
        if bad_v:
            w.findings.append("VINTAGE_INVALID")
            w.reasons.append(f"received vintage {', '.join(sorted(set(bad_v)))} outside what WM2 allows")

        # ---- WM3 supplier of every 2025 receipt ----------------------------------------------
        acceptable = {n(m["expected_supplier"])}
        if (futures or o.alt_on_stock) and n(m["alternate_supplier"]):
            acceptable.add(n(m["alternate_supplier"]))
        bad_s = [s for s in receipt_suppliers if s not in acceptable]
        if bad_s:
            w.findings.append("SUPPLIER_MISMATCH")
            w.reasons.append(f"received from {', '.join(sorted(set(bad_s)))} where the manifest expects "
                             f"{m['expected_supplier'].strip()}")
        out.append(w)
    return out


def results_of(wines: list[Wine]) -> dict:
    res = {"wine_count": len(wines)}
    for code, key in zip(CODES, ["margin_too_low_count", "vintage_invalid_count", "supplier_mismatch_count"]):
        res[key] = sum(1 for w in wines if code in w.findings)
    res["compliant_count"] = sum(1 for w in wines if not w.findings)
    res["margin_shortfall_total"] = float(sum((w.shortfall for w in wines), Decimal("0")))
    res["cost_of_bottles_sold_total"] = float(cents(sum((w.cogs for w in wines), Decimal("0"))))
    return res
