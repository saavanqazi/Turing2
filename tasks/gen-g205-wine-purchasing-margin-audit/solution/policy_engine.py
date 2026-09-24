#!/usr/bin/env python3
"""policy_engine.py — WM-STD-1 as code. One source of truth shared by compute_gold.py
(the gold) and tools/probes.py (deliberately broken variants via `opts`).

Implements margin_policy.md S0, WM1–WM3 and the derived figures verbatim. Stdlib only.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REVIEW_YEAR = 2025
REVIEW_END = f"{REVIEW_YEAR}-12-31"
VINTAGE_MIN = 2000
MIN_MARGIN = {"still": Decimal("20"), "sparkling": Decimal("25"), "fortified": Decimal("18")}
CODES = ["MARGIN_TOO_LOW", "VINTAGE_INVALID", "SUPPLIER_MISMATCH"]
CENT = Decimal("0.01")


def norm(value: str | None) -> str:
    """S0: trim surrounding whitespace, ignore letter case. Nothing else."""
    return (value or "").strip().casefold()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# ---- purchasing_notes.md as data (the notes are prose; NOTES mirrors them; keep in step) --
# kind: freight (supplier, amount, basis, case_size) | allocation (wine, state)
#       alias (invoiced, same_as) | alternate_withdrawn (alternate)
NOTES: list[dict] = [
    {"date": "2025-02-02", "kind": "freight", "supplier": "Reims Cellars", "amount": "15.60", "basis": "per case", "case_size": "6"},
    {"date": "2025-03-15", "kind": "allocation", "wine": "W-17", "state": "open"},
    {"date": "2025-04-20", "kind": "alias", "invoiced": "Rioja Direct SL", "same_as": "Rioja Direct"},
    {"date": "2025-05-12", "kind": "freight", "supplier": "Douro Trading", "amount": "2.05", "basis": "per bottle", "case_size": "6"},
    {"date": "2025-06-01", "kind": "freight", "supplier": "Tuscan Vines", "amount": "21.60", "basis": "per case", "case_size": "6"},
    {"date": "2025-07-08", "kind": "alternate_withdrawn", "alternate": "Left Bank Brokers"},
    {"date": "2025-08-03", "kind": "allocation", "wine": "W-120", "state": "closed"},
    {"date": "2025-09-01", "kind": "freight", "supplier": "Tuscan Vines", "amount": "21.60", "basis": "per case", "case_size": "12"},
    {"date": "2025-10-22", "kind": "alias", "invoiced": "Barossa Exports Pty", "same_as": "Barossa Exports"},
]


@dataclass
class Opts:
    """Every flag is a shortcut a rule-to-code script might take. All False = the policy."""
    flat20: bool = False
    no_freight: bool = False
    expected_freight: bool = False
    first_line_wins: bool = False
    last_line_wins: bool = False
    per_line: bool = False
    count_lines: bool = False
    all_futures_exempt: bool = False
    alt_on_stock: bool = False
    round1dp: bool = False
    nv_invalid: bool = False
    case_sensitive: bool = False
    no_futures_year: bool = False
    float_round: bool = False
    pack_ignored: bool = False
    basis_ignored: bool = False
    manifest_last: bool = False
    manifest_first: bool = False
    future_row_honoured: bool = False
    notes_ignored: bool = False
    notes_global: bool = False
    notes_latest_only: bool = False      # only the last note per subject applied (windows ignored)
    active_first_wins: bool = False      # two active lines: first listed instead of latest po_date
    unreviewed_listed: bool = False      # wines with no active line still listed as compliant
    inactive_lines_dated: bool = False   # note dates read against any line, not the governing one


@dataclass
class Wine:
    wine_id: str
    name: str
    category: str
    landed: Decimal
    margin: Decimal
    minimum: Decimal | None
    futures: bool
    exempt: bool
    low: bool
    findings: list[str] = field(default_factory=list)
    shortfall: Decimal = Decimal("0")
    reasons: list[str] = field(default_factory=list)
    po_date: str = ""
    vintage: str = ""
    supplier: str = ""
    expected: str = ""

    @property
    def finding(self) -> str:
        return "|".join(self.findings) if self.findings else "compliant"


def evaluate(input_dir: Path, opts: Opts | None = None, notes: list[dict] | None = None) -> list[Wine]:
    o = opts or Opts()
    notes = NOTES if notes is None else notes
    n = (lambda s: (s or "").strip()) if o.case_sensitive else norm

    # ---- manifest (S0: latest effective_from on or before REVIEW_END governs) -----------
    manifest: dict[str, dict[str, str]] = {}
    for r in rows(input_dir / "wine_manifest.csv"):
        k = n(r["wine_id"])
        eff = r["effective_from"].strip()
        if o.manifest_last:
            manifest[k] = r
        elif o.manifest_first:
            manifest.setdefault(k, r)
        else:
            if eff > REVIEW_END and not o.future_row_honoured:
                continue
            if k not in manifest or eff > manifest[k]["effective_from"].strip():
                manifest[k] = r

    # ---- supplier terms (freight on the stated basis) ----------------------------------
    terms: dict[str, Decimal] = {}
    for r in rows(input_dir / "supplier_terms.csv"):
        amt = Decimal(r["freight"])
        if n(r["freight_basis"]) == "per case" and not o.basis_ignored:
            amt = amt / Decimal(r["case_size"])
        terms[n(r["supplier"])] = amt

    # ---- purchasing lines (S0: active line governs; several active → latest po_date) -----
    lines = rows(input_dir / "wine_purchases.csv")
    order: list[str] = []
    seen: set[str] = set()
    for r in lines:
        k = n(r["wine_id"])
        if k not in seen:
            seen.add(k)
            order.append(k)
    governing: dict[str, dict[str, str]] = {}
    any_line: dict[str, dict[str, str]] = {}
    if o.per_line:
        chosen = [(n(r["wine_id"]), r) for r in lines if n(r["po_status"]) == "active"]
    else:
        for r in lines:
            k = n(r["wine_id"])
            any_line.setdefault(k, r)
            if o.first_line_wins:
                governing.setdefault(k, r)
            elif o.last_line_wins:
                governing[k] = r
            elif n(r["po_status"]) == "active":
                if k not in governing:
                    governing[k] = r
                elif not o.active_first_wins and r["po_date"].strip() > governing[k]["po_date"].strip():
                    governing[k] = r
        chosen = []
        for k in order:
            if k in governing:
                chosen.append((k, governing[k]))
            elif o.unreviewed_listed:
                chosen.append((k, any_line[k]))
            # else: S0 — a wine with no active line is outside the review

    # ---- notes -----------------------------------------------------------------------
    def note_state(kind: str, subject_key: str, subject_val: str, po_date: str):
        """The latest note of `kind` for the subject that is in effect on po_date, or None."""
        if o.notes_ignored:
            return None
        hits = [nt for nt in notes if nt["kind"] == kind and n(nt[subject_key]) == subject_val]
        if o.notes_latest_only:
            hits = hits[-1:]
        if not o.notes_global:
            hits = [nt for nt in hits if po_date >= nt["date"]]
        return hits[-1] if hits else None

    out: list[Wine] = []
    for k, r in chosen:
        wid = r["wine_id"].strip()
        m = manifest.get(k)
        po_date = r["po_date"].strip()
        if o.inactive_lines_dated:
            po_date = max(x["po_date"].strip() for x in lines if n(x["wine_id"]) == k)
        bottles = Decimal(1) if o.pack_ignored else Decimal(re.fullmatch(r"(\d+)x\d+cl", r["pack"].strip()).group(1))
        purchase = Decimal(r["purchase_price"]) / bottles
        selling = Decimal(r["selling_price"]) / bottles
        supplier = n(r["supplier"])
        futures = n(r["wine_type"]) == "futures"
        category = n(m["category"]) if m else ""
        allocation = n(m["allocation"]) if m else ""
        vintage_text = r["vintage"].strip()

        # freight per bottle: terms, then any freight note in effect for this line
        fr_key = n(m["expected_supplier"]) if (o.expected_freight and m) else supplier
        per_bottle = Decimal("0") if o.no_freight else terms.get(fr_key, Decimal("0"))
        nt = note_state("freight", "supplier", fr_key, po_date)
        if nt and not o.no_freight:
            amt = Decimal(nt["amount"])
            per_bottle = amt / Decimal(nt["case_size"]) if (nt["basis"] == "per case" and not o.basis_ignored) else amt
        nt = note_state("allocation", "wine", k, po_date)
        if nt:
            allocation = nt["state"]
        supplier_for_match = supplier
        nt = note_state("alias", "invoiced", supplier, po_date)
        if nt:
            supplier_for_match = n(nt["same_as"])

        # WM1
        landed = purchase + per_bottle
        margin = (selling - landed) / landed * 100
        if o.round1dp:
            margin = margin.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        minimum = Decimal("20") if o.flat20 else (MIN_MARGIN.get(category) if category else None)
        exempt = futures and (True if o.all_futures_exempt else allocation == "open")
        low = minimum is not None and margin < minimum

        # WM2
        if vintage_text.upper() == "NV":
            vintage_ok = (not o.nv_invalid) and bool(m) and n(m["nv_allowed"]) == "y"
        elif re.fullmatch(r"\d{4}", vintage_text):
            vintage_ok = VINTAGE_MIN <= int(vintage_text) <= REVIEW_YEAR + (0 if o.no_futures_year else (1 if futures else 0))
        else:
            vintage_ok = False

        # WM3
        if m is None:
            supplier_ok = False
        else:
            acceptable = {n(m["expected_supplier"])}
            alt = n(m["alternate_supplier"])
            withdrawn = alt and note_state("alternate_withdrawn", "alternate", alt, po_date) is not None
            if (futures or o.alt_on_stock) and alt and not withdrawn:
                acceptable.add(alt)
            supplier_ok = supplier_for_match in acceptable

        w = Wine(wid, r["wine_name"].strip(), category, landed, margin, minimum, futures, exempt, low,
                 po_date=po_date, vintage=vintage_text, supplier=r["supplier"].strip(),
                 expected=(m["expected_supplier"].strip() if m else ""))
        if low and not exempt:
            w.findings.append("MARGIN_TOO_LOW")
            raw = landed * (1 + minimum / 100) - selling
            w.shortfall = Decimal(str(round(float(raw), 2))) if o.float_round else cents(raw)
            w.reasons.append(f"{margin:.2f} pct on a landed cost of {landed:.2f} per bottle against the "
                             f"{category} minimum of {minimum} pct (line dated {po_date}); shortfall {w.shortfall:.2f}")
        if not vintage_ok:
            w.findings.append("VINTAGE_INVALID")
            limit = REVIEW_YEAR + (1 if futures else 0)
            w.reasons.append("NV where the manifest does not allow non-vintage" if vintage_text.upper() == "NV"
                             else f"vintage {vintage_text} outside {VINTAGE_MIN}-{limit}")
        if not supplier_ok:
            w.findings.append("SUPPLIER_MISMATCH")
            w.reasons.append(f"supplier {w.supplier} where the manifest "
                             + (f"expects {w.expected}" if m else "has no entry for this wine"))
        out.append(w)

    if o.count_lines:
        out.append(Wine("__lines__", "", "", Decimal(0), Decimal(0), None, False, False, False))
        out[-1].findings = [str(len(lines))]
    return out


def results_of(wines: list[Wine]) -> dict:
    real = [w for w in wines if w.wine_id != "__lines__"]
    line_count = next((int(w.findings[0]) for w in wines if w.wine_id == "__lines__"), None)
    res = {"wine_count": line_count if line_count is not None else len(real)}
    for code, key in zip(CODES, ["margin_too_low_count", "vintage_invalid_count", "supplier_mismatch_count"]):
        res[key] = sum(1 for w in real if code in w.findings)
    res["compliant_count"] = sum(1 for w in real if not w.findings)
    res["margin_shortfall_total"] = float(sum((w.shortfall for w in real), Decimal("0")))
    return res
