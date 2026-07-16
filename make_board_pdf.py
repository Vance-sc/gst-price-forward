#!/usr/bin/env python3
"""
Render the Forward-Buy Signal Board as a one-page PDF (board.pdf) that
mirrors the live dashboard: five product cards with signal pills, component
bars, validation lines, and 180-day price charts. Runs in CI right after
generate.py using the data.json it just wrote — no network needed here.

Also writes board_data.json — a slim (<80 KB) copy of the board's inputs —
so downstream automations can fetch it as plain JSON and re-render this
exact PDF locally:  python make_board_pdf.py board_data.json

Requires matplotlib (CI installs it). Public data only — same rules as the
dashboard: pounds OK, no dollars.
"""

import sys
import json
import os

import matplotlib
matplotlib.use("Agg")
# Core-14 fonts keep the PDF small (~15 KB vs ~65 KB with embedded Type3
# glyphs) so the weekly automation can move it as one base64 chunk.
matplotlib.rcParams["pdf.use14corefonts"] = True
matplotlib.rcParams["font.family"] = "Helvetica"
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

GREEN, RED, AMBER = "#117b53", "#a6152e", "#d68a12"
INK, MUTED, BG, LINE = "#1b1b1b", "#666666", "#f6f5f2", "#e4e4e4"
SIG = {"LOCK": RED, "SPLIT": AMBER, "HOLD": GREEN}

HERE = os.path.dirname(__file__) or "."
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data.json")
D = json.load(open(DATA))
META, PRODUCTS = D["meta"], D["products"]

PAGE_W, PAGE_H = 8.5, 11.0
fig = plt.figure(figsize=(PAGE_W, PAGE_H))
fig.patch.set_facecolor(BG)


def sma(vals, n):
    out = []
    for i in range(len(vals)):
        out.append(sum(vals[max(0, i - n + 1):i + 1])
                   / len(vals[max(0, i - n + 1):i + 1]) if i >= n - 1 else None)
    return out


ASCII = {"\u2014": "-", "\u00b7": "|", "\u2020": "+", "\u26a0": "!",
         "\u2192": "->", "\u2019": "'", "\u2018": "'"}


def text(x, y, s, size=8, color=INK, weight="normal", ha="left", va="top"):
    for k, v in ASCII.items():
        s = s.replace(k, v)
    fig.text(x, y, s, fontsize=size, color=color, fontweight=weight,
             ha=ha, va=va)


# ---------------- header ----------------
fig.patches.append(Rectangle((0, 0.962), 1, 0.038, transform=fig.transFigure,
                             facecolor="white", edgecolor="none"))
fig.patches.append(Rectangle((0, 0.958), 1, 0.006, transform=fig.transFigure,
                             facecolor=GREEN, edgecolor="none"))
# small shield-color chip (green/white/red) — brand cue, not the Shield
for i, c in enumerate([GREEN, "white", RED]):
    fig.patches.append(Rectangle((0.035, 0.968 + i * 0.008), 0.018, 0.008,
                                 transform=fig.transFigure, facecolor=c,
                                 edgecolor="#222222", linewidth=0.4))
text(0.065, 0.992, "Forward-Buy Signal Board", size=15, weight="bold")
text(0.065, 0.972, "GST's top 5 beef products · USDA boxed-beef quotes "
     "→ 30 / 60-day lock guidance", size=7.5, color=MUTED)
demo = "  ·  SAMPLE DATA (DEMO)" if META.get("is_demo") else ""
text(0.97, 0.992, f"Built {META.get('generated_utc','')}{demo}", size=7,
     color=MUTED, ha="right")
text(0.97, 0.981, f"Market date: {META.get('last_market_date','—')}", size=7,
     color=MUTED, ha="right")
cut = META.get("cutout") or {}
if cut:
    chg = cut.get("choice_chg_1d")
    text(0.97, 0.970,
         f"Choice Cutout {cut.get('choice','—')} "
         f"({'+' if (chg or 0) >= 0 else ''}{chg})  ·  Select {cut.get('select','—')}",
         size=7.5, color=INK, ha="right", weight="bold")
for w in META.get("warnings") or []:
    text(0.5, 0.955, "⚠ " + w, size=7.5, color=AMBER, ha="center")

# ---------------- cards ----------------
ORDER = list(PRODUCTS.keys())
COLS, ROWS = 2, 3
M, GX, GY = 0.035, 0.02, 0.012
CW = (1 - 2 * M - GX) / 2
CH = 0.285
TOP = 0.945


def bar(x, y, w, val, color):
    fig.patches.append(Rectangle((x, y), w, 0.004, transform=fig.transFigure,
                                 facecolor="#eeeeee", edgecolor="none"))
    fig.patches.append(Rectangle((x, y), w * max(0.01, val / 100), 0.004,
                                 transform=fig.transFigure, facecolor=color,
                                 edgecolor="none"))


def card(ix, key):
    p = PRODUCTS[key]
    col, row = ix % COLS, ix // COLS
    x0 = M + col * (CW + GX)
    y1 = TOP - row * (CH + GY)
    y0 = y1 - CH
    fig.patches.append(FancyBboxPatch(
        (x0, y0), CW, CH, transform=fig.transFigure,
        boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor="white", edgecolor=LINE, linewidth=0.8))
    # header
    text(x0 + 0.012, y1 - 0.012, p["name"], size=9.5, weight="bold")
    text(x0 + 0.012, y1 - 0.028, f"{p['unit']} · {p['spec']}", size=6.5,
         color=MUTED)
    chg = p.get("change_30d_pct", 0)
    text(x0 + CW - 0.012, y1 - 0.012, f"{p['current']:.2f}", size=12,
         weight="bold", ha="right")
    text(x0 + CW - 0.012, y1 - 0.030,
         f"{'+' if chg >= 0 else ''}{chg}% / 30 cal days · "
         f"volatility {p.get('volatility_ann_pct','—')}%",
         size=6.5, color=(RED if chg >= 0 else GREEN), ha="right")
    lbs = (p.get("gst") or {}).get("lbs")
    if lbs:
        text(x0 + 0.012, y1 - 0.043, f"{lbs:,} lbs/yr", size=6.5,
             color=GREEN, weight="bold")
    rv = p.get("rv_pct")
    if rv is not None:
        tag = " (rich)" if rv > 1.5 else " (cheap)" if rv < -1.5 else ""
        text(x0 + CW - 0.012, y1 - 0.043,
             f"vs cutout: {'+' if rv >= 0 else ''}{rv}% of 250d norm{tag}",
             size=6.5, color=MUTED, ha="right")
    # two horizon boxes
    for hx, h in enumerate(("30", "60")):
        hz = p["horizons"][h]
        bx = x0 + 0.012 + hx * (CW / 2 - 0.008)
        by = y1 - 0.055
        text(bx, by, f"{h}-DAY", size=6, color=MUTED, weight="bold")
        text(bx, by - 0.013, f"{hz['score']}", size=13, weight="bold",
             color=SIG[hz["signal"]])
        # pill
        px = bx + 0.055
        fig.patches.append(FancyBboxPatch(
            (px, by - 0.0255), 0.042, 0.012, transform=fig.transFigure,
            boxstyle="round,pad=0.002,rounding_size=0.006",
            facecolor=SIG[hz["signal"]], edgecolor="none"))
        text(px + 0.021, by - 0.0155, hz["signal"], size=6.5, color="white",
             weight="bold", ha="center")
        text(bx, by - 0.032, f"Confidence: {hz['confidence']}", size=6,
             color=MUTED)
        v = hz.get("validation")
        if v:
            text(bx, by - 0.042,
                 f"'18-'26: {'+' if v['mean'] >= 0 else ''}{v['mean']}% fwd, "
                 f"hit {round(v['hit'] * 100)}% (n={v['n']})",
                 size=6, color=MUTED)
        comp = hz["components"]
        for ci, (lbl, ck) in enumerate([("RelVal", "rel_value"),
                                        ("Mom†", "momentum"),
                                        ("Vol", "volume"),
                                        ("C/S", "cs_spread")]):
            cy = by - 0.055 - ci * 0.0085
            text(bx, cy + 0.004, lbl, size=5.5, color=MUTED)
            val = comp[ck]
            bcol = RED if val >= 62 else AMBER if val >= 45 else GREEN
            bar(bx + 0.030, cy, CW / 2 - 0.075, val, bcol)
            text(bx + CW / 2 - 0.038, cy + 0.004, f"{val:.0f}", size=5.5,
                 color=MUTED)
    # chart (bottom of the card) — decimate to <=60 points so the PDF
    # stays ~12-15 KB; larger files corrupt when moved as inline base64
    s = p["series"][-180:]
    step = max(1, len(s) // 60)
    s = s[::step] if step > 1 else s
    prices = [pt[1] for pt in s]
    ax = fig.add_axes([x0 + 0.015, y0 + 0.012, CW - 0.03, 0.085])
    ax.set_zorder(5)   # figure patches default to zorder 1; axes default 0
    ax.plot(range(len(prices)), prices, color=INK, linewidth=0.8)
    s10 = sma(prices, 10)
    s40 = sma(prices, 40)
    ax.plot(range(len(prices)), s10, color=RED, linewidth=0.6,
            linestyle="--")
    ax.plot(range(len(prices)), s40, color=GREEN, linewidth=0.6)
    ax.set_facecolor("white")
    ax.tick_params(labelsize=4.5, colors=MUTED, length=1.5)
    for sp in ax.spines.values():
        sp.set_color(LINE)
    ax.margins(x=0)
    n = len(s)
    ticks = [0, n // 2, n - 1]
    ax.set_xticks(ticks)
    ax.set_xticklabels([s[t][0] for t in ticks])


for i, k in enumerate(ORDER):
    card(i, k)

# ---------------- legend / footer panel in the 6th slot ----------------
lx = M + (len(ORDER) % COLS) * (CW + GX)
ly1 = TOP - (len(ORDER) // COLS) * (CH + GY)
text(lx + 0.012, ly1 - 0.015, "How to read it", size=9, weight="bold")
for i, (sig, desc) in enumerate([
        ("LOCK", "cheap vs cutout / post-dip — locking looks favorable"),
        ("SPLIT", "mixed — consider locking part of the volume"),
        ("HOLD", "rich vs cutout / post-rally — wait")]):
    yy = ly1 - 0.035 - i * 0.017
    fig.patches.append(FancyBboxPatch(
        (lx + 0.012, yy - 0.004), 0.042, 0.012, transform=fig.transFigure,
        boxstyle="round,pad=0.002,rounding_size=0.006",
        facecolor=SIG[sig], edgecolor="none"))
    text(lx + 0.033, yy + 0.006, sig, size=6.5, color="white", weight="bold",
         ha="center")
    text(lx + 0.062, yy + 0.006, desc, size=7)
text(lx + 0.012, ly1 - 0.095,
     "Score = 40% relative value + 25% contrarian momentum†\n"
     "+ 20% volume + 15% Choice/Select spread, each vs its own\n"
     "trailing 250 days. Thresholds self-calibrate each build.\n"
     "Validated walk-forward 2018-2026 (no lookahead); the\n"
     "hit rate shown per bucket IS the confidence figure.\n"
     "† these cuts mean-revert: dips raise the score.",
     size=6.5, color=MUTED)
text(lx + 0.012, ly1 - 0.20,
     "Decision support, not a forecast. USDA quotes are\n"
     "packer→wholesale; GST vendor cost follows with a lag\n"
     "(measured Q2-26: flap ~3d, chuck ~9d, clod ~12d).\n"
     "Live board: vance-sc.github.io/gst-price-forward",
     size=6.5, color=MUTED)
text(0.5, 0.012, "Data: USDA AMS Market News LM_XB403 (LMR DataMart) · "
     "Built for GST Meat Co.", size=6, color=MUTED, ha="center", va="bottom")

out_pdf = os.path.join(HERE, "board.pdf")
fig.savefig(out_pdf, format="pdf", facecolor=BG)
plt.close(fig)

# Slim data file (<80 KB) so downstream automations can fetch the board's
# inputs as plain JSON and re-render this exact PDF locally.
slim = {"meta": META, "products": {}}
for k, p in PRODUCTS.items():
    q = dict(p)
    q["series"] = p["series"][-180:]
    slim["products"][k] = q
with open(os.path.join(HERE, "board_data.json"), "w") as f:
    json.dump(slim, f, separators=(",", ":"))
print(f"board.pdf: {os.path.getsize(out_pdf)} bytes; board_data.json: "
      f"{os.path.getsize(os.path.join(HERE, 'board_data.json'))} bytes")
