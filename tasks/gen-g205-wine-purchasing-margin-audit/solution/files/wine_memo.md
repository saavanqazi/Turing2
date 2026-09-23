# Wine purchasing and margin audit

6 wines reviewed against WM-STD-1. 3 carry a finding and 3 are compliant.

| Wine | Code | Why |
|---|---|---|
| `W-3` | MARGIN_TOO_LOW | Pinot at 13.3 pct margin, below the 20 pct minimum |
| `W-4` | VINTAGE_INVALID | 2026 vintage outside 2000-2025 |
| `W-5` | SUPPLIER_MISMATCH | supplier WrongCo where the manifest expects NapaCo |

## Wines that look low-margin and are not findings

`W-2` (Bordeaux) is a futures wine at 10.0 pct margin. WM1 exempts futures (pre-arrival) wines from the margin minimum, so it is compliant. Reporting it would overstate the finding count.
