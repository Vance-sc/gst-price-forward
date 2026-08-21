#!/usr/bin/env python3
"""GST Forward-Buy Daily Calls board — one-page PDF in the style of the
original board, but leading with THE CALL per cut: the momentum lock rule
(1-week move > +4% -> LOCK ~4 weeks) overriding the v2 30-day signal.
Usage: python3 call_board.py board_data.json
CALLBOARD_VERSION = 4
"""

import sys
import json
import os
import datetime as dt

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.use14corefonts"] = True
matplotlib.rcParams["font.family"] = "Helvetica"
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

GREEN, RED, AMBER = "#117b53", "#a6152e", "#d68a12"
INK, MUTED, BG, LINE = "#1b1b1b", "#666666", "#f6f5f2", "#e4e4e4"
SIG = {"LOCK": GREEN, "SPLIT": AMBER, "HOLD": RED}
TRIG = 0.04          # 1-week momentum lock trigger
LOCK_DAYS = 28       # lock tenor, calendar days

# 60-day lock vs float on days with trailing-month rally > +8%, per cut:
# (win_pct, n_days). Computed on 2016-2026 USDA history, daily samples with
# overlapping forward windows (so effective n is smaller than shown).
# Regenerate with study7.py (Temp\gst-bt) after model changes or ~quarterly.
RALLY_STATS = {
    "chuck_roll": (37, 650),
    "flap": (52, 785),
    "shoulder_clod": (38, 391),
    "short_rib": (56, 427),
    "round": (46, 508),
}

HERE = os.path.dirname(__file__) or "."
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "board_data.json")
D = json.load(open(DATA))
META, PRODUCTS = D["meta"], D["products"]

ASCII = {"—": "-", "·": "|", "†": "+", "⚠": "!",
         "→": "->", "’": "'", "‘": "'"}

PAGE_W, PAGE_H = 8.5, 11.0
fig = plt.figure(figsize=(PAGE_W, PAGE_H))
fig.patch.set_facecolor(BG)


def text(x, y, s, size=8, color=INK, weight="normal", ha="left", va="top"):
    for k, v in ASCII.items():
        s = s.replace(k, v)
    fig.text(x, y, s, fontsize=size, color=color, fontweight=weight,
             ha=ha, va=va)


def mom(series, i_end, look=5):
    """simple return over `look` obs ending at index i_end (negative ok)."""
    if len(series) + min(i_end, 0) < look + 1:
        return None
    a = series[i_end][1]
    b = series[i_end - look][1]
    return a / b - 1


def sma(vals, n):
    out = []
    for i in range(len(vals)):
        w = vals[max(0, i - n + 1):i + 1]
        out.append(sum(w) / len(w) if i >= n - 1 else None)
    return out


def call_for(p):
    """-> dict(call, is_new, streak_start, through, r1wk)"""
    s = p["series"]
    r = mom(s, -1)
    ry = mom(s, -2)
    if r is not None and r > TRIG:
        # walk back to the first day of the consecutive streak above TRIG
        i = len(s) - 1
        start = i
        while True:
            j = start - 1
            rj = mom(s, j - len(s))
            if rj is None or rj <= TRIG or start - 1 < 5:
                break
            start = j
        start_date = dt.date.fromisoformat(s[start][0])
        through = start_date + dt.timedelta(days=LOCK_DAYS)
        return {"call": "LOCK", "is_new": (ry is not None and ry <= TRIG),
                "streak_start": start_date, "through": through, "r1wk": r}
    v2 = p["horizons"]["30"]["signal"]
    return {"call": v2, "is_new": False, "streak_start": None,
            "through": None, "r1wk": r}


# ---------------- header ----------------
fig.patches.append(Rectangle((0, 0.962), 1, 0.038, transform=fig.transFigure,
                             facecolor="white", edgecolor="none"))
fig.patches.append(Rectangle((0, 0.958), 1, 0.006, transform=fig.transFigure,
                             facecolor=GREEN, edgecolor="none"))
for i, c in enumerate([GREEN, "white", RED]):
    fig.patches.append(Rectangle((0.035, 0.968 + i * 0.008), 0.018, 0.008,
                                 transform=fig.transFigure, facecolor=c,
                                 edgecolor="#222222", linewidth=0.4))
text(0.065, 0.992, "Forward-Buy Daily Calls", size=15, weight="bold")
text(0.065, 0.972, "GST's top 5 beef products · momentum lock rule + v2 "
     "value model → one call per cut", size=7.5, color=MUTED)
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
COLS = 2
M, GX, GY = 0.035, 0.02, 0.012
CW = (1 - 2 * M - GX) / 2
CH = 0.285
TOP = 0.945


def card(ix, key):
    p = PRODUCTS[key]
    c = call_for(p)
    col, row = ix % COLS, ix // COLS
    x0 = M + col * (CW + GX)
    y1 = TOP - row * (CH + GY)
    y0 = y1 - CH
    fig.patches.append(FancyBboxPatch(
        (x0, y0), CW, CH, transform=fig.transFigure,
        boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor="white", edgecolor=SIG[c["call"]], linewidth=1.4))
    # header: name left, price right
    text(x0 + 0.012, y1 - 0.012, p["name"], size=9.5, weight="bold")
    text(x0 + 0.012, y1 - 0.028, f"{p['unit']} · {p['spec']}", size=6.5,
         color=MUTED)
    chg = p.get("change_30d_pct", 0)
    text(x0 + CW - 0.012, y1 - 0.012, f"{p['current']:.2f}", size=12,
         weight="bold", ha="right")
    r1 = c["r1wk"]
    r1s = f"{r1 * 100:+.1f}%" if r1 is not None else "—"
    text(x0 + CW - 0.012, y1 - 0.030,
         f"1wk {r1s} · 30d {'+' if chg >= 0 else ''}{chg}%",
         size=6.5, color=(RED if chg >= 0 else GREEN), ha="right")
    # ---- THE CALL: big pill ----
    py = y1 - 0.052
    fig.patches.append(FancyBboxPatch(
        (x0 + 0.012, py - 0.024), 0.085, 0.024, transform=fig.transFigure,
        boxstyle="round,pad=0.003,rounding_size=0.008",
        facecolor=SIG[c["call"]], edgecolor="none"))
    text(x0 + 0.0545, py - 0.0065, c["call"], size=11, color="white",
         weight="bold", ha="center")
    if c["call"] == "LOCK":
        tag = "NEW today" if c["is_new"] else f"streak since {c['streak_start']}"
        text(x0 + 0.105, py - 0.001,
             f"{tag} — lock ~4 wks of volume", size=7, weight="bold")
        text(x0 + 0.105, py - 0.013,
             f"at/near {p['current']:.0f}, through ~{c['through'].strftime('%b %d')}",
             size=7, color=MUTED)
    elif c["call"] == "SPLIT":
        text(x0 + 0.105, py - 0.001, "lock ~half of volume (v2 value model)",
             size=7, weight="bold")
        text(x0 + 0.105, py - 0.013,
             f"v2 30d conf: {p['horizons']['30']['confidence']}", size=7, color=MUTED)
    else:
        text(x0 + 0.105, py - 0.001, "stay floating (v2 value model)",
             size=7, weight="bold")
        text(x0 + 0.105, py - 0.013,
             f"v2 30d conf: {p['horizons']['30']['confidence']}", size=7, color=MUTED)
    # ---- secondary detail line ----
    dy = py - 0.036
    h30, h60 = p["horizons"]["30"], p["horizons"]["60"]
    rv = p.get("rv_pct")
    rvtag = " (rich)" if rv is not None and rv > 1.5 else \
            " (cheap)" if rv is not None and rv < -1.5 else ""
    text(x0 + 0.012, dy,
         f"v2: 30d {h30['signal']} {h30['score']} ({h30['confidence']}) · "
         f"60d {h60['signal']} {h60['score']} ({h60['confidence']})",
         size=6.3, color=MUTED)
    text(x0 + 0.012, dy - 0.011,
         f"vs cutout {'+' if (rv or 0) >= 0 else ''}{rv}%{rvtag} · "
         f"1d {'+' if p.get('change_1d', 0) >= 0 else ''}{p.get('change_1d', 0)}",
         size=6.3, color=MUTED)
    if chg > 8:
        win, nn = RALLY_STATS.get(key, (None, 0))
        if win is None:
            wtxt, wcol, wwt = (f"⚠ up {chg}% in 30d — cap locks at ~4 wks",
                               AMBER, "bold")
        elif win < 45:
            wtxt, wcol, wwt = (f"⚠ up {chg}% in 30d — cap locks at ~4 wks; "
                               f"60-day locks here won only {win}% ('16-'26, n={nn})",
                               AMBER, "bold")
        elif win <= 55:
            wtxt, wcol, wwt = (f"up {chg}% in 30d — 60-day locks here are a "
                               f"coin flip ({win}%): no edge extending past 4 wks",
                               MUTED, "normal")
        else:
            wtxt, wcol, wwt = (f"up {chg}% in 30d — this cut's rallies tend to "
                               f"persist ({win}% 60-day lock win): extension defensible",
                               MUTED, "normal")
        text(x0 + 0.012, dy - 0.023, wtxt, size=6.3, color=wcol, weight=wwt)
    # ---- chart ----
    s = p["series"][-180:]
    prices = [pt[1] for pt in s]
    ax = fig.add_axes([x0 + 0.015, y0 + 0.012, CW - 0.03, 0.115])
    ax.set_zorder(5)
    ax.plot(range(len(prices)), prices, color=INK, linewidth=0.8)
    ax.plot(range(len(prices)), sma(prices, 10), color=RED, linewidth=0.6,
            linestyle="--")
    ax.plot(range(len(prices)), sma(prices, 40), color=GREEN, linewidth=0.6)
    # shade the current lock window origin if in a streak
    if c["call"] == "LOCK" and c["streak_start"] is not None:
        dates = [pt[0] for pt in s]
        ss = str(c["streak_start"])
        if ss in dates:
            k = dates.index(ss)
            ax.axvspan(k, len(prices) - 1, color=GREEN, alpha=0.12)
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

# ---------------- legend panel in the 6th slot ----------------
lx = M + (len(ORDER) % COLS) * (CW + GX)
ly1 = TOP - (len(ORDER) // COLS) * (CH + GY)
text(lx + 0.012, ly1 - 0.015, "How to read it", size=9, weight="bold")
for i, (sig, desc) in enumerate([
        ("LOCK", "fix ~4 weeks of volume at/near today's price"),
        ("SPLIT", "fix about half of the volume"),
        ("HOLD", "stay floating — no lock")]):
    yy = ly1 - 0.035 - i * 0.017
    fig.patches.append(FancyBboxPatch(
        (lx + 0.012, yy - 0.004), 0.042, 0.012, transform=fig.transFigure,
        boxstyle="round,pad=0.002,rounding_size=0.006",
        facecolor=SIG[sig], edgecolor="none"))
    text(lx + 0.033, yy + 0.006, sig, size=6.5, color="white", weight="bold",
         ha="center")
    text(lx + 0.062, yy + 0.006, desc, size=7)
text(lx + 0.012, ly1 - 0.095,
     "THE CALL combines two engines:\n"
     "1) Momentum lock rule — if the cut is up more than +4%\n"
     "over the trailing week, LOCK ~4 weeks (walk-forward\n"
     "2016-2026: saved ~1.9% of chuck spend, in every era).\n"
     "This overrides v2 in rallies, where v2's 30d HOLD is\n"
     "a coin flip. Green shading on charts = current streak.\n"
     "2) Otherwise the v2 30-day value signal is the call.",
     size=6.5, color=MUTED)
text(lx + 0.012, ly1 - 0.215,
     "Chart lines: daily close (black), 10-day avg (red\n"
     "dashed), 40-day avg (green).\n"
     "Tenor notes on cards show each cut's OWN 60-day-lock\n"
     "win rate during >8%/30d rallies ('16-'26): chuck 37%\n"
     "and clod 38% mean-revert (cap at 4 wks); knuckle 46%\n"
     "and flap 52% are coin flips; short rib 56% persists.\n"
     "USDA is packer→wholesale; vendor cost lags ~3-9 days.\n"
     "Decision support, not a forecast.",
     size=6.5, color=MUTED)
text(0.5, 0.012, "Data: USDA AMS LM_XB403 · momentum rule validated "
     "walk-forward 2016-2026 · Built for GST Meat Co.",
     size=6, color=MUTED, ha="center", va="bottom")

out_pdf = os.path.join(HERE, "call_board.pdf")
fig.savefig(out_pdf, format="pdf", facecolor=BG)
plt.close(fig)
print(f"call_board.pdf: {os.path.getsize(out_pdf)} bytes")
