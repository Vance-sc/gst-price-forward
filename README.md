# GST Forward-Buy Signal Board

A daily dashboard that reads USDA boxed-beef prices and gives a
**30- and 60-day "lock" signal** to help decide when to price-fix product with
a vendor (Cargill, Zant, etc.).

- **Green / LOCK** — market under upward pressure; locking looks favorable
- **Amber / SPLIT** — mixed; consider locking part of the volume
- **Red / HOLD** — soft or softening; little urgency to lock now

## Official call (one rule)

**Production = Lock Score v2** from `generate.py`: the live dashboard,
`data.json`, and `board.pdf` (`make_board_pdf.py`). Green LOCK / Amber SPLIT /
Red HOLD for 30- and 60-day horizons.

`call_board.py` is **experimental / local only** (momentum override). It is
not run in CI and is not the vendor call — do not treat it as the daily board.

The site also deploys to the password-protected subdomain
`board.gstmeat.com` (SiteGround; see the FTPS step in `update.yml` —
requires `SG_FTP_HOST`/`SG_FTP_USER`/`SG_FTP_PASS` repo secrets).

Each of GST's top 5 beef products is pinned to **one exact USDA item and
grade** (no averaging across cuts or grades):

| GST product | USDA item (verbatim) | Grade |
|---|---|---|
| Diesmillo | `Chuck, roll, lxl, neck/off (116A  3)` | Choice |
| Fajita de Res | `Loin, bottom sirloin, flap (185A  4)` | Choice |
| Espaldia | `Chuck, shoulder clod, trmd (114A  3)` | Choice |
| Costilla | `Chuck, short rib (130  4)` | Choice |
| Milanesa | `Round, knuckle, peeled (167A  4)` | Choice |

To remap a product, replace its `item` string in `PRODUCTS` (in
`generate.py`) with another **verbatim** `item_description` from the API —
note USDA uses double spaces before the trailing spec number. Costilla was
verified against 2026-Q2 vendor invoices (Zant "CHUCK SHORT RIBS 5-BONE"):
GST buys chuck 130s, and paid prices correlate negatively with plate 123A —
don't switch it back without invoice evidence. After any remap, re-run
`python backtest.py` and refresh `VALIDATION`.

---

## What it is (and isn't)

The 0–100 **Lock Score (v2+seas)** blends five signals, each z-scored against
its own trailing history (seasonality uses same ISO-week priors):

1. **Relative value (35%)** — cut price ÷ Choice cutout vs its own norm.
   Cheap vs the cutout = high score. The strongest validated signal.
2. **Momentum (22%, contrarian)** — these cuts mean-revert over 30–60 days,
   so run-ups lower the score, dips raise it. (The v1 trend-following
   version backtested *inverted* and was replaced — don't restore it.)
3. **Volume (18%)** — heavier-than-usual negotiated volume has preceded
   price strength.
4. **Choice/Select spread (13%)** — an unusually wide spread has preceded
   softness.
5. **Seasonality (12%)** — cut/cutout vs the same ISO week in prior years
   only (no lookahead). Cheap-for-this-week = high score.

**Validation:** expanding-window walk-forward over 2018–2026. At every
historical decision day, LOCK/HOLD thresholds were recalibrated as the
70th/30th percentile of *prior* pooled scores only — no lookahead anywhere.
Pooled test (n≈580–790 per bucket): 30d LOCK days +2.35% avg forward move
vs HOLD −1.07%; 60d LOCK +3.94% vs HOLD −2.38%. The edge held in all three
eras (2018–20, 2021–23, 2024–26). The production build recalibrates its
thresholds by the same procedure on every run, so the live board is exactly
what was validated. Each card shows the validated hit rate for its bucket —
that is the Confidence figure. **It is still not a forecast**: supply
shocks, packer margins, and demand swings can override any signal. Re-run
`python backtest.py` after any model change and update `VALIDATION` in
`generate.py`; if the pooled test fails the printed pass criterion, don't
ship the change.

**Basis note:** USDA quotes are the *packer→wholesale* price. Your vendor
cost tracks them with a lag and a spread — read the **direction**, not the
dollar figure.

---

## Data source: USDA LMR DataMart (no API key)

Boxed-beef LMR data is **not** on the MARS API. It lives on the LMR
DataMart, which is keyless:

```
https://mpr.datamart.ams.usda.gov/services/v1.1/reports/2453/<Section>
```

- `2453` = report LM_XB403 (National Daily Boxed Beef Cutout & Cuts, PM)
- Sections used: `Choice Cuts` (product quotes) and `Current Cutout Values`
  (the Choice/Select cutout index shown in the header)
- Date filter: `?q=report_date=MM/DD/YYYY:MM/DD/YYYY` (the date column is
  `report_date`; `report_begin_date` is rejected for this report)

**Hard-won rules, encoded in `generate.py` — do not undo them:**

1. **Always use the date-range filter.** An unfiltered pull is silently
   capped at 100,000 rows (~91 MB, newest-first), so the start of history
   slides forward every day.
2. **No-trade rows** are published with `weighted_average: ".00"` or null.
   They are not zero prices; the parser discards them.
3. **Match items exactly** (`item_description` + section). Substring
   matching once pooled chuck flap into fajita, flat iron into clod, and
   averaged Choice with Select — producing garbage.
4. Duplicate rows for one item+date are volume-weighted by `total_pounds`.

**Holidays:** USDA skips reports on federal holidays (the report narrative
announces them). The board shows the newest **market date** next to the
build time, and displays a warning if data is more than 4 business days old.

---

## Files

| File | Purpose |
|------|---------|
| `generate.py` | Fetches USDA data, computes **official v2** signals, writes `index.html` + `data.json` |
| `dashboard_template.py` | The dashboard HTML/CSS/JS template |
| `make_board_pdf.py` | Official one-page PDF (`board.pdf`) — same v2 calls as the dashboard |
| `call_board.py` | **Experimental / local only** — momentum override PDF; not CI |
| `backtest.py` | Walk-forward backtest of the Lock Score (run locally) |
| `.github/workflows/update.yml` | Weekday CI → GitHub Pages (+ optional SiteGround FTPS) |
| `gst_private.py` | **Local only, gitignored** — confidential sales/margin/cost overlay |

`generate.py` is standard-library only. PDF scripts need `matplotlib` (CI
installs it). Charts on the HTML board use Chart.js from a CDN.

---

## Run it locally

```bash
python generate.py            # LIVE fetch by default (no key needed)
FORCE_DEMO=1 python generate.py   # synthetic sample data, clearly banners
# open index.html in a browser
```

If `gst_private.py` is present locally, the dashboard adds GST's dollar
figures. The public CI build sets `PUBLIC_BUILD=1`, which strips all dollar
data at the data level — the published `data.json`/`index.html` contain
pounds only.

## Backtest before trusting the thresholds

```bash
python backtest.py            # 6 years of live history
python backtest.py --demo     # harness self-check on synthetic data
```

It walks forward through history with no lookahead and reports, per product
and horizon, the mean forward price move on LOCK vs SPLIT vs HOLD days and
by score quintile, against an all-days baseline. The signal has skill only
if LOCK days clearly beat the baseline. If they don't, adjust `WEIGHTS` /
thresholds in `generate.py`, or treat the board as a price monitor.

---

## Deploying (GitHub Pages)

1. Push this folder to a GitHub repo (keep `.github/workflows/` intact).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. Actions tab → "Update forward-buy dashboard" → **Run workflow**.
4. Live at `https://<username>.github.io/<repo>/`. It refreshes itself each
   weekday at 22:00 UTC (after the PM report). No secrets are required —
   if a `USDA_API_KEY` secret exists from an earlier version, delete it.

### Custom subdomain / `board.gstmeat.com` (SiteGround)

Preferred staff URL: password-protected `board.gstmeat.com` (SiteGround),
mirrored from the same CI build as GitHub Pages.

1. **DNS** — point `board.gstmeat.com` at SiteGround (already on gstmeat.com).
2. **FTP account** — Site Tools → FTP Accounts: create a user whose home is
   *only* the `board.gstmeat.com` document root (not the whole site).
3. **GitHub repo secrets** (Settings → Secrets and variables → Actions):
   - `SG_FTP_HOST` — SiteGround FTP hostname
   - `SG_FTP_USER` / `SG_FTP_PASS` — that scoped account
   Until these exist, the FTPS step skips (Pages still publishes).
4. **Password** — Site Tools → Security → Protected URLs: protect
   `board.gstmeat.com` (or `/`) with a username/password for staff. HTTP
   basic auth is SiteGround-side; GitHub cannot set it for you.
5. Re-run **Update forward-buy dashboard** (or wait for the weekday cron).

GitHub Pages custom domain (optional alternative): **Settings → Pages →
Custom domain**, then add the CNAME GitHub shows at your DNS registrar.

`PUBLIC_BUILD=1` refuses DEMO mode so a bad USDA fetch fails the job instead
of publishing sample LOCK/HOLD signals.

---

## Privacy design

The public repo and page must never expose GST's dollars; pounds are OK.

- Sales/margin/cost live only in `gst_private.py` (gitignored).
- `PUBLIC_BUILD=1` (set in CI) strips dollar fields at the data level.
- Built artifacts (`index.html`, `data.json`, `backtest_results.json`) are
  gitignored so a local full-detail build can't be committed by accident.
- Demo-mode base prices are generic public USDA ballparks, not GST figures.

---

*Decision-support tool for GST Meat Co. Not financial advice.
Data © USDA Agricultural Marketing Service, Livestock Market News.*
