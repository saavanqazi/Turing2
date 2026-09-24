# Wine purchasing and margin audit — review year 2025

131 wines reviewed against WM-STD-1 (one per wine_id on its governing active line; wines with no active line are outside the review). 64 carry a finding and 67 are compliant. Margin shortfall total 27.79.

## Findings

- `W-03` (Pinot Noir Dundee Hills) — MARGIN_TOO_LOW: 5.59 pct on a landed cost of 16.10 per bottle against the still minimum of 20 pct (line dated 2025-01-21); shortfall 2.32
- `W-04` (Champagne Brut Reserve) — VINTAGE_INVALID: vintage 2026 outside 2000-2025
- `W-07` (Prosecco Superiore) — MARGIN_TOO_LOW: 4.76 pct on a landed cost of 12.60 per bottle against the sparkling minimum of 25 pct (line dated 2025-06-02); shortfall 2.55
- `W-10` (Shiraz Barossa) — MARGIN_TOO_LOW: 18.90 pct on a landed cost of 16.40 per bottle against the still minimum of 20 pct (line dated 2025-05-27); shortfall 0.18
- `W-13` (Barolo) — MARGIN_TOO_LOW: 7.00 pct on a landed cost of 48.60 per bottle against the still minimum of 20 pct (line dated 2025-06-16); shortfall 6.32
- `W-14` (Vintage Port) — VINTAGE_INVALID: vintage 1997 outside 2000-2025
- `W-15` (Cava Brut) — VINTAGE_INVALID: NV where the manifest does not allow non-vintage
- `W-16` (Gruner Veltliner) — SUPPLIER_MISMATCH: supplier Mosel Handel where the manifest has no entry for this wine
- `W-19` (Pomerol) — SUPPLIER_MISMATCH: supplier Left Bank Brokers where the manifest expects Bordeaux Negoce
- `W-20` (Sekt Riesling) — MARGIN_TOO_LOW: 24.37 pct on a landed cost of 11.90 per bottle against the sparkling minimum of 25 pct (line dated 2025-04-01); shortfall 0.08
- `W-21` (Zinfandel Lodi) — MARGIN_TOO_LOW: 19.96 pct on a landed cost of 25.00 per bottle against the still minimum of 20 pct (line dated 2025-02-06); shortfall 0.01
- `W-22` (Chenin Blanc) — VINTAGE_INVALID|SUPPLIER_MISMATCH: vintage 2026 outside 2000-2025; supplier Finger Lakes Co where the manifest expects Willamette Partners
- `W-23` (Tempranillo Crianza) — MARGIN_TOO_LOW|SUPPLIER_MISMATCH: 14.04 pct on a landed cost of 11.40 per bottle against the still minimum of 20 pct (line dated 2025-03-24); shortfall 0.68; supplier Rioja Direct SL where the manifest expects Rioja Direct
- `W-25` (Muscadet) — MARGIN_TOO_LOW|VINTAGE_INVALID: 17.65 pct on a landed cost of 10.20 per bottle against the still minimum of 20 pct (line dated 2025-01-14); shortfall 0.24; vintage 1999 outside 2000-2025
- `W-28` (Madeira 5 Year) — MARGIN_TOO_LOW: 17.12 pct on a landed cost of 27.75 per bottle against the fortified minimum of 18 pct (line dated 2025-05-06); shortfall 0.25
- `W-29` (Hermitage) — VINTAGE_INVALID: vintage 2027 outside 2000-2026
- `W-32` (Nebbiolo Langhe) — MARGIN_TOO_LOW: 15.67 pct on a landed cost of 26.80 per bottle against the still minimum of 20 pct (line dated 2025-04-07); shortfall 1.16
- `W-35` (Lambrusco) — MARGIN_TOO_LOW: 23.86 pct on a landed cost of 8.80 per bottle against the sparkling minimum of 25 pct (line dated 2025-05-20); shortfall 0.10
- `W-36` (Fino Sherry) — MARGIN_TOO_LOW: 17.07 pct on a landed cost of 16.40 per bottle against the fortified minimum of 18 pct (line dated 2025-01-27); shortfall 0.15
- `W-38` (Vouvray) — SUPPLIER_MISMATCH: supplier Loire Direct where the manifest expects Bordeaux Negoce
- `W-40` (Rose de Provence) — MARGIN_TOO_LOW: 18.95 pct on a landed cost of 9.50 per bottle against the still minimum of 20 pct (line dated 2025-05-28); shortfall 0.10
- `W-41` (Vermentino) — MARGIN_TOO_LOW: 6.62 pct on a landed cost of 13.60 per bottle against the still minimum of 20 pct (line dated 2025-06-20); shortfall 1.82
- `W-42` (Garnacha Old Vines) — MARGIN_TOO_LOW: 18.35 pct on a landed cost of 10.90 per bottle against the still minimum of 20 pct (line dated 2025-05-14); shortfall 0.18
- `W-43` (Saint-Julien) — SUPPLIER_MISMATCH: supplier Left Bank Brokers where the manifest expects Bordeaux Negoce
- `W-45` (Marsala Superiore Hillside) — MARGIN_TOO_LOW: 17.95 pct on a landed cost of 25.90 per bottle against the fortified minimum of 18 pct (line dated 2025-03-29); shortfall 0.01
- `W-46` (Torrontes Estate) — MARGIN_TOO_LOW: 18.80 pct on a landed cost of 20.90 per bottle against the still minimum of 20 pct (line dated 2025-06-25); shortfall 0.25
- `W-51` (Amontillado Old Vines) — MARGIN_TOO_LOW: 17.21 pct on a landed cost of 47.30 per bottle against the fortified minimum of 18 pct (line dated 2025-05-31); shortfall 0.37
- `W-54` (Mourvedre Village) — MARGIN_TOO_LOW: 17.49 pct on a landed cost of 29.50 per bottle against the still minimum of 20 pct (line dated 2025-04-21); shortfall 0.74
- `W-57` (Nero d'Avola Village) — MARGIN_TOO_LOW: 19.97 pct on a landed cost of 29.50 per bottle against the still minimum of 20 pct (line dated 2025-11-03); shortfall 0.01
- `W-58` (Zweigelt Single Vineyard) — SUPPLIER_MISMATCH: supplier Willamette Partners where the manifest expects Cape Winelands Ltd
- `W-63` (Petit Verdot Estate) — VINTAGE_INVALID: vintage 2026 outside 2000-2025
- `W-65` (Melon de Bourgogne Selection) — SUPPLIER_MISMATCH: supplier Cape Winelands Ltd where the manifest expects Barossa Exports
- `W-69` (Palo Cortado Classic) — MARGIN_TOO_LOW: 15.52 pct on a landed cost of 20.30 per bottle against the fortified minimum of 18 pct (line dated 2025-07-16); shortfall 0.50
- `W-71` (Corvina Hillside) — MARGIN_TOO_LOW: 17.49 pct on a landed cost of 33.90 per bottle against the still minimum of 20 pct (line dated 2025-08-20); shortfall 0.85
- `W-72` (Assyrtiko Estate) — MARGIN_TOO_LOW|VINTAGE_INVALID: 16.42 pct on a landed cost of 10.60 per bottle against the still minimum of 20 pct (line dated 2025-05-28); shortfall 0.38; vintage 2027 outside 2000-2025
- `W-75` (Aglianico Classic) — MARGIN_TOO_LOW: 17.47 pct on a landed cost of 12.65 per bottle against the still minimum of 20 pct (line dated 2025-07-01); shortfall 0.32
- `W-78` (Melon de Bourgogne Reserve) — MARGIN_TOO_LOW: 19.40 pct on a landed cost of 34.75 per bottle against the still minimum of 20 pct (line dated 2025-10-13); shortfall 0.21
- `W-79` (Carignan Hillside) — VINTAGE_INVALID: vintage 2026 outside 2000-2025
- `W-80` (Champagne Rose Single Vineyard) — MARGIN_TOO_LOW: 23.78 pct on a landed cost of 9.80 per bottle against the sparkling minimum of 25 pct (line dated 2025-04-29); shortfall 0.12
- `W-87` (Late Bottled Vintage Port Reserve) — VINTAGE_INVALID: NV where the manifest does not allow non-vintage
- `W-90` (Lambrusco Secco Reserve) — MARGIN_TOO_LOW: 23.79 pct on a landed cost of 25.30 per bottle against the sparkling minimum of 25 pct (line dated 2025-11-10); shortfall 0.31
- `W-92` (Nero d'Avola Old Vines) — MARGIN_TOO_LOW|SUPPLIER_MISMATCH: 19.97 pct on a landed cost of 33.65 per bottle against the still minimum of 20 pct (line dated 2025-05-06); shortfall 0.01; supplier Rioja Direct where the manifest expects Loire Direct
- `W-94` (Barbera d'Asti Classic) — VINTAGE_INVALID: vintage 1999 outside 2000-2025
- `W-95` (Vermentino di Gallura Cru) — MARGIN_TOO_LOW: 19.93 pct on a landed cost of 14.65 per bottle against the still minimum of 20 pct (line dated 2025-04-20); shortfall 0.01
- `W-97` (Semillon Classic) — MARGIN_TOO_LOW: 19.38 pct on a landed cost of 14.60 per bottle against the still minimum of 20 pct (line dated 2025-04-30); shortfall 0.09
- `W-98` (Cap Classique Selection) — MARGIN_TOO_LOW: 24.39 pct on a landed cost of 34.15 per bottle against the sparkling minimum of 25 pct (line dated 2025-07-10); shortfall 0.21
- `W-99` (Vernaccia Estate) — VINTAGE_INVALID: vintage 2026 outside 2000-2025
- `W-102` (Champagne Rose Classic) — VINTAGE_INVALID: NV where the manifest does not allow non-vintage
- `W-103` (Torrontes Village) — MARGIN_TOO_LOW: 18.79 pct on a landed cost of 12.35 per bottle against the still minimum of 20 pct (line dated 2025-09-14); shortfall 0.15
- `W-106` (Bual Madeira Classic) — MARGIN_TOO_LOW: 14.17 pct on a landed cost of 26.05 per bottle against the fortified minimum of 18 pct (line dated 2025-07-07); shortfall 1.00
- `W-107` (Semillon Old Vines) — MARGIN_TOO_LOW: 19.40 pct on a landed cost of 10.00 per bottle against the still minimum of 20 pct (line dated 2025-06-20); shortfall 0.06
- `W-108` (Cava Gran Reserva Coastal) — MARGIN_TOO_LOW: 23.80 pct on a landed cost of 62.60 per bottle against the sparkling minimum of 25 pct (line dated 2025-05-18); shortfall 0.75
- `W-109` (Sauvignon Blanc Cru) — VINTAGE_INVALID: vintage 1999 outside 2000-2025
- `W-110` (Montepulciano Coastal) — MARGIN_TOO_LOW: 17.52 pct on a landed cost of 12.90 per bottle against the still minimum of 20 pct (line dated 2025-09-21); shortfall 0.32
- `W-114` (Cabernet Sauvignon Single Vineyard) — MARGIN_TOO_LOW: 19.41 pct on a landed cost of 39.15 per bottle against the still minimum of 20 pct (line dated 2025-08-20); shortfall 0.23
- `W-117` (Sangiovese Village) — VINTAGE_INVALID|SUPPLIER_MISMATCH: vintage 1999 outside 2000-2025; supplier Wachau Kellerei where the manifest expects Loire Direct
- `W-120` (Chianti Riserva Futures) — MARGIN_TOO_LOW: 19.40 pct on a landed cost of 26.80 per bottle against the still minimum of 20 pct (line dated 2025-09-10); shortfall 0.16
- `W-121` (Colheita Port) — MARGIN_TOO_LOW: 16.60 pct on a landed cost of 25.30 per bottle against the fortified minimum of 18 pct (line dated 2025-05-12); shortfall 0.35
- `W-133` (Bolgheri Rosso) — MARGIN_TOO_LOW: 6.41 pct on a landed cost of 15.60 per bottle against the still minimum of 20 pct (line dated 2025-08-20); shortfall 2.12
- `W-124` (Rosso di Montalcino) — MARGIN_TOO_LOW: 6.41 pct on a landed cost of 15.60 per bottle against the still minimum of 20 pct (line dated 2025-08-31); shortfall 2.12
- `W-126` (Mataro Barossa) — SUPPLIER_MISMATCH: supplier Barossa Exports Pty where the manifest expects Barossa Exports
- `W-127` (Saint-Estephe) — SUPPLIER_MISMATCH: supplier Left Bank Brokers where the manifest expects Bordeaux Negoce
- `W-131` (Zibibbo) — SUPPLIER_MISMATCH: supplier Tuscan Vines where the manifest has no entry for this wine
- `W-132` (Kerner) — SUPPLIER_MISMATCH: supplier Wachau Kellerei where the manifest has no entry for this wine

## Wines a plain margin check would flag that the policy does not

- `W-02` (Pauillac Grand Cru): 5.4 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation on its line date (2025-05-12), which WM1 exempts from the margin minimum. The exemption covers WM1 only.
- `W-17` (Sauternes): 7.5 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation on its line date (2025-06-09), which WM1 exempts from the margin minimum. The exemption covers WM1 only.
- `W-29` (Hermitage): -0.3 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation on its line date (2025-06-30), which WM1 exempts from the margin minimum. The exemption covers WM1 only.
- `W-30` (Grenache Gris): 5.8 pct against a 20 pct minimum, but it is a futures (pre-arrival) wine with an open allocation on its line date (2025-06-11), which WM1 exempts from the margin minimum. The exemption covers WM1 only.
