# Task

The AC cord on my laptop's power brick — the detachable figure-8 one — has a break near the plug end, so it charges for a few seconds and then quits. I need a ten-foot replacement in my hands today, delivered or ready to collect, from Kestrel near me in Phenix City. I have saved the brick's data sheet, Kestrel's cord listings, the same-day offers the stores are showing, the store directory, the stock ledger the stores share, the notices the stores put up this morning, the sales-tax table for the towns round here, their same-day terms and their standard delivery page. It is 2:40 pm on Friday 25 September here right now, Central time. Work through every offer and give me the sheet: each one marked eligible or not, the reason code for the ones that are out, and what the eligible ones land at once tax and any fee are on. Then tell me how many offers are eligible, which one I should take, and what it totals. File layout is in `input/submission_format.md`.

---
Save your deliverables into your current working directory using exactly these filenames:
    - `offer_evaluation.csv` — One row per same-day offer: whether it is eligible, why not where it is not, and what it lands at where it is.
    - `results.json` — a JSON object with the keys `eligible_offer_count`, `chosen_offer_id`, `chosen_total_usd`
- The exact headers, key sets, allowed values and worked examples are specified in `input/submission_format.md` — follow it precisely.
- Writing those files is the required deliverable and must be your final action; confirm each one exists before you answer.

---

## Working environment

- Your current working directory is `/app`, and it is writable.
- The read-only attachments referred to as `input/` are at `/app/input`.
- Write every deliverable into `/app`, at the exact filenames listed above.
