# Wine purchasing and margin audit

6 wines reviewed against WM-STD-1. 3 carry a finding and 3 are compliant.

| Wine | Code | Why |
|---|---|---|
| `W-3` | MARGIN_TOO_LOW | Pinot at 13.3 pct margin below the 20 pct minimum |
| `W-4` | VINTAGE_INVALID | 2026 vintage outside the 2000-2025 range |
| `W-5` | SUPPLIER_MISMATCH | WrongCo where the manifest requires NapaCo |

## The wine that looks low-margin and is not

`W-2` is a futures Bordeaux with a 10 pct margin. WM1 makes futures wines exempt from the margin rule, so the low margin is compliant. Reporting it would overstate the finding count.
