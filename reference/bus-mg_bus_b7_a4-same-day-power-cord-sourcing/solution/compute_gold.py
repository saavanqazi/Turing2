#!/usr/bin/env python3
"""compute_gold.py — derive the gold deliverables for this task from environment/input/.

Single source of truth for the answer. Run it after ANY change to the inputs or the
terms; it rewrites
  solution/files/offer_evaluation.csv
  solution/files/results.json
  solution/golden_trajectory.json      (heredoc replay of the gold files)
  tests/manifest.json                  (expected rows / row_set / results keys only)
and prints the derivation per offer so the answer can be checked by hand.

Rules implemented are same_day_terms.md S1–S6 verbatim. Nothing here is heuristic.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
FILES = TASK / "solution" / "files"
# The graders load tests/verifier.json; the delivery format also wants tests/manifest.json.
# Both are written with identical content (the accepted bundles ship both).
SPEC_PATH = TASK / "tests" / "verifier.json"
MIRROR_PATH = TASK / "tests" / "manifest.json"

# ---- scenario constants (from same_day_terms.md) --------------------------------
ORDER_DATE = "2026-09-25"  # a Friday; the date only anchors DST, the terms give the time
BUYER_TZ = ZoneInfo("America/Chicago")
ORDER_LOCAL = datetime.fromisoformat(f"{ORDER_DATE}T14:40:00").replace(tzinfo=BUYER_TZ)
CUTOFF = "15:00"
# store_notices.md, mirrored here (the notices are prose; keep both in step)
NOTICE_CUTOFF = {("ST-08", "PICKUP"): "16:00"}        # collection accepted until 16:00 at Columbus
NOTICE_PAUSED = {("ST-52", "DELIVERY")}               # Smiths Station delivery van off the road
ADDS = {"OPENING", "RECEIPT", "VOID"}
REMOVES = {"SALE", "HOLD", "TRANSFER_OUT"}
DELIVERY_FEE = Decimal("9.99")
RADIUS_MI = Decimal("10")
BUYER_CITY, BUYER_STATE = "Phenix City", "AL"
LEN_MIN, LEN_MAX = Decimal("10"), Decimal("15")  # feet: supplied cord (data sheet) .. buyer's ceiling
FT_PER_M = Decimal("3.28084")


def length_ft(text: str) -> Decimal:
    """Catalogue `length` is given with its unit ('12 ft', '3.7 m')."""
    value, unit = text.split()
    value = Decimal(value)
    return value if unit == "ft" else value * FT_PER_M


def gauge_ok(length: Decimal, awg: int) -> bool:
    """power_supply_spec.md conductor tiers: <=6 ft any; >6..15 ft needs <=18 AWG; >15 ft needs <=16 AWG."""
    if length <= 6:
        return True
    if length <= 15:
        return awg <= 18
    return awg <= 16
PRECEDENCE = ["FIT_CONNECTOR", "FIT_POLARIZED", "FIT_GAUGE", "FIT_LENGTH", "NO_STOCK",
              "STOCK_RESERVE", "SAME_DAY_SUSPENDED", "CUTOFF_PASSED", "OUT_OF_RADIUS"]


def rows(name):
    with (INPUT / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> int:
    cat = {r["sku"]: r for r in rows("cord_catalogue.csv")}
    seen, offers_dedup = set(), []
    for o in rows("same_day_offers.csv"):  # an export may repeat a line; one offer = one offer_id
        if o["offer_id"] not in seen:
            seen.add(o["offer_id"]); offers_dedup.append(o)
    stores = {}
    for r in rows("store_directory.csv"):  # latest effective_from on or before the order date wins
        if r["effective_from"] <= ORDER_DATE and (
                r["store_id"] not in stores or r["effective_from"] > stores[r["store_id"]]["effective_from"]):
            stores[r["store_id"]] = r
    offers = offers_dedup
    ledger = rows("stock_ledger.csv")
    tax = {(r["city"], r["state"]): Decimal(r["combined_sales_tax_pct"]) for r in rows("tax_jurisdictions.csv")}
    dest_rate = tax[(BUYER_CITY, BUYER_STATE)]

    # order instant on each store's clock (S1)
    order_at = {sid: ORDER_LOCAL.astimezone(ZoneInfo(s["timezone"])) for sid, s in stores.items()}

    # effective stock per (store, sku) (S2): day's OPENING + adds - removes, up to the order instant
    alias = {c["supplier_part"].upper(): sku for sku, c in cat.items()}
    alias.update({sku.upper(): sku for sku in cat})
    stock: dict[tuple[str, str], int] = {}
    for line in ledger:
        sid, sku = line["store_id"].upper(), alias[line["item_code"].upper()]  # case is not significant
        posted = datetime.fromisoformat(line["posted_local"])
        if posted.date().isoformat() != ORDER_DATE:
            continue  # before the day's OPENING (already included in it) or another day
        if posted.time() > order_at[sid].time():
            continue  # not happened yet on the store's clock
        sign = 1 if line["event"] in ADDS else -1 if line["event"] in REMOVES else None
        assert sign is not None, line
        stock[(sid, sku)] = stock.get((sid, sku), 0) + sign * int(line["qty"])

    out, eligible = [], []
    for o in offers:
        sku, sid, method = o["sku"], o["store_id"], o["method"]
        c, s = cat[sku], stores[sid]
        fails = set()
        if c["connector"] != "C7":
            fails.add("FIT_CONNECTOR")
        if c["polarized"] == "Y":
            fails.add("FIT_POLARIZED")
        L = length_ft(c["length"])
        if not gauge_ok(L, int(c["conductor_awg"])):
            fails.add("FIT_GAUGE")
        if not (LEN_MIN <= L <= LEN_MAX):
            fails.add("FIT_LENGTH")
        eff = stock.get((sid, sku), 0)
        if eff < 1:
            fails.add("NO_STOCK")
        if method == "DELIVERY" and eff < 2:
            fails.add("STOCK_RESERVE")
        if s["same_day_suspended"] == "Y" or (sid, method) in NOTICE_PAUSED:
            fails.add("SAME_DAY_SUSPENDED")
        if order_at[sid].strftime("%H:%M") >= NOTICE_CUTOFF.get((sid, method), CUTOFF):
            fails.add("CUTOFF_PASSED")
        if method == "DELIVERY" and Decimal(s["distance_mi"]) > RADIUS_MI:
            fails.add("OUT_OF_RADIUS")
        reason = next((r for r in PRECEDENCE if r in fails), "NONE")

        landed = ""
        if reason == "NONE":
            rate = dest_rate if method == "DELIVERY" else Decimal(s["sales_tax_pct"])
            cost = cents(Decimal(c["price_usd"]) * (1 + rate / 100))
            if method == "DELIVERY":
                cost += DELIVERY_FEE
            landed = f"{cost:.2f}"
            ready = (order_at[sid] + timedelta(hours=2)) if method == "PICKUP" else \
                order_at[sid].replace(hour=20, minute=0)
            eligible.append((cost, ready.astimezone(BUYER_TZ), Decimal(s["distance_mi"]), o["offer_id"]))
        out.append({"offer_id": o["offer_id"], "decision": "ELIGIBLE" if reason == "NONE" else "INELIGIBLE",
                    "reason_code": reason, "landed_cost_usd": landed})
        print(f"{o['offer_id']}: {sid} {sku} {method:8s} L={L:.2f}ft order@{order_at[sid].strftime('%H:%M')} "
              f"stock={eff} fails={sorted(fails, key=PRECEDENCE.index) or '-'} -> {reason} {landed}")

    eligible.sort()
    chosen_cost, _, _, chosen = eligible[0]
    ties = [e for e in eligible if e[0] == chosen_cost]
    print(f"\neligible={len(eligible)} chosen={chosen} total={chosen_cost} (tied at that cost: {[e[3] for e in ties]})")

    # ---- write gold files ---------------------------------------------------------
    FILES.mkdir(parents=True, exist_ok=True)
    header = ["offer_id", "decision", "reason_code", "landed_cost_usd"]
    csv_text = ",".join(header) + "\n" + "".join(",".join(r[h] for h in header) + "\n" for r in out)
    (FILES / "offer_evaluation.csv").write_text(csv_text, encoding="utf-8")
    results = {"eligible_offer_count": len(eligible), "chosen_offer_id": chosen,
               "chosen_total_usd": float(chosen_cost)}
    json_text = json.dumps(results, indent=2) + "\n"
    (FILES / "results.json").write_text(json_text, encoding="utf-8")

    # ---- golden trajectory (heredoc replay) ---------------------------------------
    reads = ["cord_catalogue.csv", "power_supply_spec.md", "same_day_offers.csv", "stock_ledger.csv",
             "store_directory.csv", "store_notices.md", "tax_jurisdictions.csv", "same_day_terms.md",
             "standard_delivery.md", "submission_format.md"]
    steps = [{"name": "bash", "server": "local", "arguments": {"command": f"cat input/{f}"}} for f in reads]
    steps.append({"name": "bash", "server": "local", "arguments": {
        "command": "cat > offer_evaluation.csv << 'OFFEREVALUATIONEOF'\n" + csv_text + "OFFEREVALUATIONEOF"}})
    steps.append({"name": "bash", "server": "local", "arguments": {
        "command": "cat > results.json << 'RESULTSEOF'\n" + json_text + "RESULTSEOF"}})
    steps.append({"name": "bash", "server": "local", "arguments": {"command": "ls -la offer_evaluation.csv results.json"}})
    (TASK / "solution" / "golden_trajectory.json").write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")

    # ---- verifier expected values -------------------------------------------------
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    by_id = {r["offer_id"]: {k: r[k] for k in header[1:]} for r in out}
    all_ids = [r["offer_id"] for r in out]
    for v in spec["verifiers"]:
        exp = v["assertion"].get("expected")
        if v["name"] == "evaluation_table_trap_of01":
            exp["rows"] = {"OF-01": by_id["OF-01"]}
        elif v["name"] == "evaluation_table":
            exp["rows"] = {i: by_id[i] for i in all_ids if i != "OF-01"}
            exp["row_set"] = all_ids
            v["metadata"]["how_justification"] = v["metadata"]["how_justification"].replace(
                "every graded cell of 10 rows plus the full 11-row population",
                f"every graded cell of {len(all_ids) - 1} rows plus the full {len(all_ids)}-row population")
            v["metadata"]["how_justification"] = re.sub(r"every graded cell of \d+ rows plus the full \d+-row population", f"every graded cell of {len(all_ids) - 1} rows plus the full {len(all_ids)}-row population", v["metadata"]["how_justification"])
        elif v["name"] == "results_figures":
            exp["keys"]["eligible_offer_count"]["value"] = len(eligible)
            exp["keys"]["chosen_offer_id"]["value"] = chosen
            exp["keys"]["chosen_total_usd"]["value"] = float(chosen_cost)
    text = json.dumps(spec, indent=2) + "\n"
    SPEC_PATH.write_text(text, encoding="utf-8")
    MIRROR_PATH.write_text(text, encoding="utf-8")
    print(f"\nwrote {FILES}, golden_trajectory.json, {SPEC_PATH.name} + {MIRROR_PATH.name} (identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
