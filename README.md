# GST Forward-Buy Signal Board

A daily dashboard that reads USDA boxed-beef price trends and gives a
**30- and 60-day "lock" signal** to help decide when to price-fix product with
a vendor (Cargill, Zant, etc.).

- **Red / LOCK** — market under upward pressure; locking looks favorable
- **Amber / SPLIT** — mixed; consider locking part of the volume
- **Green / HOLD** — soft or softening; little urgency to lock now

Tracks GST's top 5 beef products by sales — Diesmillo (Chuck Roll), Fajita de Res (Flap/Bavette), Espaldia (Shoulder Clod), Costilla (Short Rib), and Milanesa (Round) — each mapped to its USDA boxed-beef item. Free to run and host.

---

## What it is (and isn't)

It measures three things and blends them into a 0–100 **Lock Score**:

1. **Momentum (40%)** — short-term vs longer-term price average + recent trend.
2. **Seasonality (30%)** — how the calendar has historically moved into the next
   30/60 days at this time of year.
3. **Range position (30%)** — how low/high today's price sits in its recent
   range (low = more room to rise = better time to lock).

**It is not a forecast.** Cattle supply, packer margins, and demand shocks can
override any signal. Use it as one honest input to a lock conversation, not a
crystal ball. The dashboard shows a **Confidence** flag (Low when the three
signals disagree or volatility is high) so you know when to trust it less.

**Basis note:** the USDA cutout is the *packer→wholesale* price. Your vendor
cost tracks it with a lag and a spread — so read the **direction** as your cost
signal, not the exact dollar figure.

---

## Files

| File | Purpose |
|------|---------|
| `generate.py` | Pulls USDA data, computes signals, writes `index.html` + `data.json` |
| `dashboard_template.py` | The dashboard HTML/CSS/JS template |
| `.github/workflows/update.yml` | Auto-runs each weekday and publishes to GitHub Pages |
| `index.html` / `data.json` | Built output (regenerated each run) |

No third-party Python packages — it runs on the standard library alone.
Charts use Chart.js from a CDN.

---

## Run it locally (see the sample now)

```bash
python generate.py        # no key = DEMO mode with clearly-labeled sample data
# open index.html in a browser
```

With a key, it pulls the real USDA data:

```bash
# macOS/Linux
export USDA_API_KEY="your-key-here"
python generate.py
# Windows PowerShell
$env:USDA_API_KEY="your-key-here"; python generate.py
```

---

## Step 1 — Get a free USDA API key (2 minutes)

1. Go to **https://mymarketnews.ams.usda.gov/mars-api/authentication** (the
   MyMarketNews / MARS API page) and request a key. It's free; USDA emails you a
   key string.
2. Keep that key handy for Step 3. The tool sends it as the HTTP Basic
   username (blank password) — the standard MARS pattern.

---

## Step 2 — Put it on GitHub (free hosting, auto-updating)

1. Create a free account at **github.com** if you don't have one.
2. Create a new repository, e.g. `gst-price-forward` (Private is fine — Pages
   still works on free accounts for public sites; if you want the page itself
   private, see "Keeping it private" below).
3. Upload every file in this folder, preserving the `.github/workflows/`
   subfolder. Easiest path: on the repo page use **Add file → Upload files**,
   drag the contents in, and commit. (The `.github` folder must keep its name
   and structure or the automation won't run.)

## Step 3 — Add your API key as a secret

1. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**.
2. Name it exactly `USDA_API_KEY`, paste your key as the value, save.

## Step 4 — Turn on GitHub Pages

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**

## Step 5 — Run it

1. Go to the **Actions** tab → "Update forward-buy dashboard" → **Run
   workflow**. First run builds and publishes the page.
2. After it finishes, your dashboard is live at
   `https://<your-username>.github.io/gst-price-forward/`.
3. From then on it refreshes itself every weekday evening after USDA posts the
   afternoon report. No further action needed.

### Putting it on your own subdomain (optional)

If you want `pricing.gstmeat.com` instead of the github.io URL: in
**Settings → Pages → Custom domain**, enter your subdomain, then add the CNAME
record GitHub shows you at your DNS/registrar. Free.

---

## Confirming field names on the first live run

The USDA report's exact column names occasionally differ from what the parser
expects. `generate.py` is written to tolerate that, and if a live pull returns
nothing usable it automatically falls back to DEMO and prints a warning in the
Actions log. If that happens:

1. Open the Actions run log and look for the WARNING line.
2. In `generate.py`, the `PRODUCTS` list has a `match` field (lowercase
   substrings) for each product, and `parse_live_records` lists the candidate
   date/price column names. Adjust those to match the live report.
3. Easiest: pull the report once manually and look at the JSON keys —
   `https://marsapi.ams.usda.gov/services/v1.2/reports/2453` with your key —
   then tweak the two lists. (Or send me a saved copy of that JSON and I'll
   finalize the mapping.)

---

## Adding pork or chicken later

Beef comes from report `2453` (LM_XB403). Pork and poultry live in separate
USDA reports. To add them, duplicate the `PRODUCTS`/report pattern in
`generate.py` for the relevant report ID. Ask and I'll wire it in.

---

*Decision-support tool for GST Meat Co. Not financial advice.
Data © USDA Agricultural Marketing Service, Livestock Market News.*
