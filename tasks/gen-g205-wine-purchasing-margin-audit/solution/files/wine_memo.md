# Wine purchasing and margin audit — review year 2025

40 wines reviewed against WM-STD-1 (one per wine_id; only the active purchasing line read). 22 carry a finding and 18 are compliant. Margin shortfall total 14.37.

| Wine | Finding | Why |
|---|---|---|
| `W-03` | MARGIN_TOO_LOW | 5.59 pct on a landed cost of 16.10 (still minimum 20 pct), shortfall 2.32 |
| `W-04` | VINTAGE_INVALID | vintage 2026 outside 2000-2025 |
| `W-07` | MARGIN_TOO_LOW | 22.22 pct on a landed cost of 10.80 (sparkling minimum 25 pct), shortfall 0.30 |
| `W-10` | MARGIN_TOO_LOW | 18.90 pct on a landed cost of 16.40 (still minimum 20 pct), shortfall 0.18 |
| `W-13` | MARGIN_TOO_LOW | 11.11 pct on a landed cost of 46.80 (still minimum 20 pct), shortfall 4.16 |
| `W-14` | VINTAGE_INVALID | vintage 1997 outside 2000-2025 |
| `W-15` | VINTAGE_INVALID | NV where the manifest does not allow non-vintage |
| `W-16` | SUPPLIER_MISMATCH | supplier Mosel Handel where the manifest has no entry for this wine |
| `W-17` | MARGIN_TOO_LOW | 7.53 pct on a landed cost of 37.20 (still minimum 20 pct), shortfall 4.64 |
| `W-19` | SUPPLIER_MISMATCH | supplier Left Bank Brokers where the manifest expects Bordeaux Negoce |
| `W-20` | MARGIN_TOO_LOW | 24.37 pct on a landed cost of 11.90 (sparkling minimum 25 pct), shortfall 0.08 |
| `W-21` | MARGIN_TOO_LOW | 19.96 pct on a landed cost of 25.00 (still minimum 20 pct), shortfall 0.01 |
| `W-22` | VINTAGE_INVALID|SUPPLIER_MISMATCH | vintage 2026 outside 2000-2025; supplier Finger Lakes Co where the manifest expects Willamette Partners |
| `W-23` | MARGIN_TOO_LOW|SUPPLIER_MISMATCH | 14.04 pct on a landed cost of 11.40 (still minimum 20 pct), shortfall 0.68; supplier Rioja Direct SL where the manifest expects Rioja Direct |
| `W-25` | MARGIN_TOO_LOW|VINTAGE_INVALID | 17.65 pct on a landed cost of 10.20 (still minimum 20 pct), shortfall 0.24; vintage 1999 outside 2000-2025 |
| `W-28` | MARGIN_TOO_LOW | 17.12 pct on a landed cost of 27.75 (fortified minimum 18 pct), shortfall 0.25 |
| `W-29` | VINTAGE_INVALID | vintage 2027 outside 2000-2026 |
| `W-32` | MARGIN_TOO_LOW | 15.67 pct on a landed cost of 26.80 (still minimum 20 pct), shortfall 1.16 |
| `W-35` | MARGIN_TOO_LOW | 23.86 pct on a landed cost of 8.80 (sparkling minimum 25 pct), shortfall 0.10 |
| `W-36` | MARGIN_TOO_LOW | 17.07 pct on a landed cost of 16.40 (fortified minimum 18 pct), shortfall 0.15 |
| `W-38` | SUPPLIER_MISMATCH | supplier Loire Direct where the manifest expects Bordeaux Negoce |
| `W-40` | MARGIN_TOO_LOW | 18.95 pct on a landed cost of 9.50 (still minimum 20 pct), shortfall 0.10 |

## Wines a plain margin check would flag that the policy does not

- `W-02` (Pauillac Grand Cru): 5.4 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation, which WM1 exempts from the margin minimum. The exemption covers WM1 only.
- `W-29` (Hermitage): -0.3 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation, which WM1 exempts from the margin minimum. The exemption covers WM1 only.
- `W-30` (Grenache Gris): 5.8 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation, which WM1 exempts from the margin minimum. The exemption covers WM1 only.
