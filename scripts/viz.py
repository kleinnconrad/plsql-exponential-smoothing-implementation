#!/usr/bin/env python3
"""Draw docs/exponential_smoothing_light.png and docs/exponential_smoothing_dark.png.

The figure shows what the procedure computes, one row per pass of FOR i IN 0..10:
the window of dates that is summed, the weight of each date, the anchor date
and the value prog_1 that is inserted into analytics.var_x.

No database is needed. The script reads the rows that sql/V*.sql insert into
analytics.source_table and replays analytics.exponential_smoothing()
(src/exponential_smoothing_pg.pls, the port of src/EXPONENTIAL_SMOOTHING.pls)
step by step in exact arithmetic, so every number in the figure is computed.

Usage (from any directory):
    pip install -r scripts/requirements.txt
    python3 scripts/viz.py                  # writes the two PNGs to docs/
    python3 scripts/viz.py --out /tmp/viz   # somewhere else
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

# Inter is used when installed; otherwise matplotlib falls back to DejaVu Sans.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
FONT = ["Inter", "DejaVu Sans"]
MONO = "DejaVu Sans Mono"  # ships with matplotlib

# --------------------------------------------------------------------------
# 1. Data: the ('YYYY-MM-DD', n) tuples inserted by the Flyway migrations
# --------------------------------------------------------------------------
ROW_RE = re.compile(r"\(\s*'(\d{4}-\d{2}-\d{2})'\s*,\s*(-?\d+|NULL)\s*\)", re.I)


def read_source_rows(sql_dir: Path):
    files = sorted(sql_dir.glob("V*__*.sql"),
                   key=lambda p: int(re.match(r"V(\d+)", p.name).group(1)))
    rows = []
    for path in files:
        for datum, val in ROW_RE.findall(path.read_text(encoding="utf-8")):
            rows.append((date.fromisoformat(datum), None if val.upper() == "NULL" else int(val)))
    if not rows:
        raise SystemExit(f"no ('YYYY-MM-DD', n) rows found in {sql_dir}/V*__*.sql")
    return rows, [re.match(r"V\d+", p.name).group(0) for p in files]


# --------------------------------------------------------------------------
# 2. Replay the procedure as written (Fraction = exact NUMERIC arithmetic)
# --------------------------------------------------------------------------
ALPHA = Fraction("0.2")  # alpha NUMERIC := 0.2 in src/exponential_smoothing_pg.pls


def replay(rows):
    # SELECT DATUM, SUM(KUENDIGER) ... WHERE KUENDIGER IS NOT NULL GROUP BY datum
    sums: dict[date, int] = {}
    for datum, val in rows:
        if val is not None:
            sums[datum] = sums.get(datum, 0) + val
    asc = sorted(sums)                 # ROW_NUMBER() OVER(PARTITION BY 1 ORDER BY DATUM ASC) AS K
    desc = sorted(sums, reverse=True)  # ROW_NUMBER() OVER(PARTITION BY 1 ORDER BY DATUM DESC) AS rn
    series = [{"K": k, "datum": d, "Y": sums[d]} for k, d in enumerate(asc, start=1)]
    k_of = {d: k for k, d in enumerate(asc, start=1)}
    max_k = len(series)                # SELECT MAX(K) INTO max_k
    if not series:
        raise SystemExit("every KUENDIGER in sql/V*.sql is NULL; MAX(K) is NULL, nothing to draw")

    passes = []
    for i in range(0, 11):             # FOR i IN 0..10 LOOP
        lo, hi = 1 + i, max_k - (10 - i)   # WHERE K BETWEEN 1 + i AND max_k - (10-i)
        window = [s for s in series if lo <= s["K"] <= hi]
        anz = len(window)                  # COUNT(*) AS ANZ
        weights = {s["K"]: ALPHA * (1 - ALPHA) ** s["K"] for s in window}
        terms = {s["K"]: weights[s["K"]] * s["Y"] for s in window}  # alpha * POWER((1-alpha), k) * KUENDIGER
        x = sum(terms.values(), Fraction(0)) if window else None    # SUM(X); NULL over no rows
        rn = max_k - (10 - i)              # WHERE rn = max_k - (10-i)
        a_datum = desc[rn - 1] if 1 <= rn <= max_k else None       # no row -> NULL
        a_y = None if a_datum is None else sums[a_datum]
        a_w = (1 - ALPHA) ** (anz + 1)     # POWER((1-alpha), ANZ + 1)
        a_term = None if a_y is None else a_w * a_y
        prog = None if x is None or a_term is None else x + a_term
        passes.append({
            "i": i, "id": i + 1, "window": (lo, hi), "ANZ": anz,
            "weights": weights, "terms": terms, "X": x,
            "anchor_rn": rn, "anchor_K": None if a_datum is None else k_of[a_datum],
            "anchor_value": a_y, "anchor_weight": a_w, "anchor_term": a_term,
            "prog_1": prog,                # INSERT INTO analytics.var_x (prog_1)
        })
    return series, max_k, passes


# The 20 rows of sql/V1 + V2 (2026-04-01 .. 2026-04-20). On exactly this data the
# replay must give prog_1 = 31.9004172288 for i = 0.
BUNDLED_START = date(2026, 4, 1)
BUNDLED_Y = (30, 34, 45, 38, 41, 42, 39, 47, 50, 48, 53, 45, 49, 55, 52, 58, 60, 54, 51, 57)


def sanity_check(series, passes):
    bundled = (len(series) == len(BUNDLED_Y)
               and series[0]["datum"] == BUNDLED_START
               and all((s["datum"] - BUNDLED_START).days == s["K"] - 1 for s in series)
               and tuple(s["Y"] for s in series) == BUNDLED_Y)
    if not bundled:
        print("note: sql/V*.sql holds other data than the bundled dummy rows; "
              "built-in check skipped", file=sys.stderr)
        return
    first = passes[0]["prog_1"]
    assert first == Fraction("31.9004172288"), f"prog_1 for i = 0 is {first}, expected 31.9004172288"


def check_against(truth_path: Path, series, max_k, passes):
    """Optional: compare every number with a ground-truth JSON file."""
    t = json.loads(truth_path.read_text())
    assert t["max_k"] == max_k, (t["max_k"], max_k)
    assert [s["kuendiger"] for s in t["series"]] == [s["Y"] for s in series]
    for a, b in zip(t["iterations"], passes, strict=True):
        assert tuple(a["window"]) == b["window"] and a["ANZ"] == b["ANZ"], a["i"]
        assert a["anchor_K"] == b["anchor_K"] and a["anchor_value"] == b["anchor_value"], a["i"]
        assert a["anchor_rn_desc"] == b["anchor_rn"], a["i"]
        for key in ("X", "anchor_weight", "anchor_term", "prog_1"):
            assert abs(a[key] - float(b[key])) < 1e-9, (a["i"], key, a[key], float(b[key]))
        for k, w in a["weights"].items():
            assert abs(w - float(b["weights"][int(k)])) < 1e-12, (a["i"], k)
        for k, c in a["contributions"].items():
            assert abs(c - float(b["terms"][int(k)])) < 1e-9, (a["i"], k)
    print(f"ground truth OK: {truth_path}")


# --------------------------------------------------------------------------
# 3. Number formatting
# --------------------------------------------------------------------------
def fixed(v: Fraction, places: int) -> str:
    """Round half away from zero, as NUMERIC does."""
    with localcontext() as ctx:
        ctx.prec = 80
        d = Decimal(v.numerator) / Decimal(v.denominator)
        return str(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def sig3(v: float) -> str:
    """Three significant digits in plain decimal notation, trailing zeros kept."""
    exp = int(f"{v:e}".split("e")[1])
    return f"{v:.{max(2 - exp, 0)}f}"


def fmt_prog(v):
    return "NULL" if v is None else fixed(v, 3)


# --------------------------------------------------------------------------
# 4. Palette (dataviz reference palette); light and dark are selected, not inverted
# --------------------------------------------------------------------------
BLUE = {100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef", 300: "#6da7ec",
        350: "#5598e7", 400: "#3987e5", 450: "#2a78d6", 500: "#256abf", 550: "#1c5cab",
        600: "#184f95", 650: "#104281", 700: "#0d366b"}

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "input": "#898781",
        "x": "#2a78d6",        # categorical slot 1: X, the window sum
        "anchor": "#eb6834",   # categorical slot 2: the anchor row and its term
        # weight ramp, near zero -> step 150 so the smallest cells keep visible edges
        "ramp": [BLUE[s] for s in range(150, 701, 50)],
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7",
        "grid": "#2c2c2a", "axis": "#383835", "input": "#898781",
        "x": "#3987e5",
        "anchor": "#d95926",
        # flipped: near zero -> step 700 blended 40 % toward the surface, so the
        # low-weight tail recedes into the dark surface as the pale end does in light
        "ramp": ["#122b4a"] + [BLUE[s] for s in range(700, 99, -50)],
    },
}


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[j:j + 2], 16) / 255 for j in (0, 2, 4))


def ramp_color(ramp, t):
    """Linear interpolation along the ramp steps, t in [0, 1]."""
    t = min(max(t, 0.0), 1.0) * (len(ramp) - 1)
    j = min(int(t), len(ramp) - 2)
    f = t - j
    a, b = hex2rgb(ramp[j]), hex2rgb(ramp[j + 1])
    return tuple(a[n] + (b[n] - a[n]) * f for n in range(3))


# --------------------------------------------------------------------------
# 5. Canvas: one axes in inches, y = 0 at the top edge
# --------------------------------------------------------------------------
PX = 1 / 90          # one CSS pixel when the 10 in wide PNG is shown 900 px wide
GAP = 2 * PX         # surface gap between touching marks
ALL = frozenset({"tl", "tr", "bl", "br"})
RIGHT = frozenset({"tr", "br"})
TOP = frozenset({"tl", "tr"})
K4 = 0.5523          # cubic Bezier constant for a quarter circle


def rounded_rect(x0, y0, x1, y1, r, corners):
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    q = {c: (r if c in corners else 0.0) for c in ("tl", "tr", "br", "bl")}
    verts, codes = [(x0 + q["bl"], y0)], [MPath.MOVETO]

    def line(x, y):
        verts.append((x, y))
        codes.append(MPath.LINETO)

    def arc(*pts):
        verts.extend(pts)
        codes.extend([MPath.CURVE4] * 3)

    line(x1 - q["br"], y0)
    if q["br"]:
        arc((x1 - q["br"] * (1 - K4), y0), (x1, y0 + q["br"] * (1 - K4)), (x1, y0 + q["br"]))
    line(x1, y1 - q["tr"])
    if q["tr"]:
        arc((x1, y1 - q["tr"] * (1 - K4)), (x1 - q["tr"] * (1 - K4), y1), (x1 - q["tr"], y1))
    line(x0 + q["tl"], y1)
    if q["tl"]:
        arc((x0 + q["tl"] * (1 - K4), y1), (x0, y1 - q["tl"] * (1 - K4)), (x0, y1 - q["tl"]))
    line(x0, y0 + q["bl"])
    if q["bl"]:
        arc((x0, y0 + q["bl"] * (1 - K4)), (x0 + q["bl"] * (1 - K4), y0), (x0 + q["bl"], y0))
    verts.append((x0 + q["bl"], y0))
    codes.append(MPath.CLOSEPOLY)
    return MPath(verts, codes)


class Canvas:
    def __init__(self, T, width):
        self.T, self.W = T, width
        self.fig = plt.figure(figsize=(width, 12), facecolor=T["surface"])
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.axis("off")
        self.ax.set_xlim(0, width)
        self.renderer = self.fig.canvas.get_renderer()
        self.marks = []      # (label, x0, top, x1, bottom) in inches, for the layout check

    def text(self, x, y, s, size=9.5, color=None, mask=False, **kw):
        kw.setdefault("va", "center")
        if mask:   # surface patch behind the text, so hairlines pass behind it
            kw["bbox"] = dict(fc=self.T["surface"], ec="none", pad=1.2)
        return self.ax.text(x, -y, s, fontsize=size, color=color or self.T["ink"], **kw)

    def mono(self, x, y, s, size=9.0, color=None, **kw):
        return self.text(x, y, s, size, color or self.T["ink"], family=MONO, **kw)

    def run(self, x, y, parts, **kw):
        """Several text pieces on one line, each with its own colour/size/weight."""
        for s, opts in parts:
            t = self.text(x, y, s, ha="left", **{**kw, **opts})
            x += self.width_of(t)
        return x

    def width_of(self, t):
        return t.get_window_extent(self.renderer).width / self.fig.dpi

    def measure(self, s, size=9.5, **kw):
        t = self.ax.text(0, 0, s, fontsize=size, **kw)
        w = self.width_of(t)
        t.remove()
        return w

    def run_width(self, parts, **kw):
        return sum(self.measure(s, **{"size": 9.5, **kw, **o}) for s, o in parts)

    def wrap(self, s, width, size=9.5):
        """Split s into lines no wider than width inches."""
        lines, cur = [], ""
        for word in s.split(" "):
            cand = f"{cur} {word}".strip()
            if cur and self.measure(cand, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        return lines + [cur]

    def rect(self, x0, top, x1, bottom, color, r=0.0, corners=frozenset(), z=1, label="mark"):
        if x1 < x0:
            x0, x1 = x1, x0
        self.ax.add_patch(PathPatch(rounded_rect(x0, -bottom, x1, -top, r, corners),
                                    fc=color, ec="none", zorder=z))
        self.marks.append((label, x0, top, x1, bottom))

    def dot(self, x, y, color, r=0.05, ring=GAP, label="dot"):
        self.ax.add_patch(Circle((x, -y), r + ring, fc=self.T["surface"], ec="none", zorder=3))
        self.ax.add_patch(Circle((x, -y), r, fc=color, ec="none", zorder=4))
        self.marks.append((label, x - r, y - r, x + r, y + r))

    def hline(self, x0, x1, y, color, lw=0.75, z=0.5):
        self.ax.plot([x0, x1], [-y, -y], color=color, lw=lw, solid_capstyle="butt", zorder=z)

    def vline(self, x, y0, y1, color, lw=0.75, z=0.5):
        self.ax.plot([x, x], [-y0, -y1], color=color, lw=lw, solid_capstyle="butt", zorder=z)

    def layout_problems(self, height, margin=0.3):
        """Text boxes that overlap each other or a mark, or leave the margins."""
        self.fig.canvas.draw()
        r = self.fig.canvas.get_renderer()
        dpi = self.fig.dpi
        boxes = []
        for t in self.ax.texts:
            if t.get_text().strip():
                bb = t.get_window_extent(r)
                boxes.append((t.get_text(), bb.x0 / dpi, height - bb.y1 / dpi,
                              bb.x1 / dpi, height - bb.y0 / dpi))
        tol = 0.01

        def hit(a, b):
            return (min(a[3], b[3]) - max(a[1], b[1]) > tol
                    and min(a[4], b[4]) - max(a[2], b[2]) > tol)

        problems = []
        for j, a in enumerate(boxes):
            if a[1] < margin or a[3] > self.W - margin or a[2] < 0.2 or a[4] > height - 0.2:
                problems.append(f"outside the margin: {a[0]!r}")
            problems += [f"text overlaps text: {a[0]!r} / {b[0]!r}" for b in boxes[j + 1:] if hit(a, b)]
            problems += [f"text overlaps a {m[0]}: {a[0]!r}" for m in self.marks if hit(a, m)]
        return problems, len(boxes)

    def save(self, height, path):
        self.fig.set_size_inches(self.W, height)
        self.ax.set_xlim(0, self.W)
        self.ax.set_ylim(-height, 0)
        problems, n = self.layout_problems(height)
        for p in problems:
            print(f"layout warning ({path.name}): {p}", file=sys.stderr)
        self.fig.savefig(path, dpi=200, facecolor=self.T["surface"])
        plt.close(self.fig)
        print(f"wrote {path}  ({n} text boxes, {len(problems)} layout warnings)")
        return len(problems)


# --------------------------------------------------------------------------
# 6. The figure
# --------------------------------------------------------------------------
def draw(theme, series, max_k, passes, sources, out_path: Path):
    T = THEMES[theme]
    plt.rcParams.update({"font.family": FONT, "mathtext.fontset": "stixsans",
                         "text.color": T["ink"]})
    W, L, R = 10.0, 0.45, 0.45
    c = Canvas(T, W)
    ink, ink2 = T["ink"], T["ink2"]
    p0 = passes[0]
    anz = p0["ANZ"]
    one_minus = 1 - ALPHA

    # column system shared by the input, the grid, the weight scale and the example
    gx0 = 1.45                       # left edge of the K columns
    cw = min(0.22, 4.4 / max_k)      # column width = row height
    gx1 = gx0 + cw * max_k
    rh = cw
    lab_x = gx0 - 0.25               # right edge of the row labels
    col_i, col_id = L + 0.1, L + 0.5
    kx0 = gx1 + 0.25                 # key and right-hand columns start here
    every_k = cw >= 0.19             # room for a label on every K column
    ticks = sorted({1, max_k} | {k for k in range(5, max_k + 1, 5) if max_k - k >= 3})

    def kx(k):
        return gx0 + (k - 0.5) * cw

    def weight(k):
        return ALPHA * one_minus ** k

    wmax = float(weight(1))

    def shade(w):
        return ramp_color(T["ramp"], float(w) / wmax)

    # ---- header ------------------------------------------------------------
    c.text(L, 0.42, "EXPONENTIAL_SMOOTHING, one loop iteration per row", 15,
           weight="semibold", va="baseline")
    sub = c.wrap(f"Each pass of FOR i IN 0..10 sums a {anz}-date window of the input, adds an "
                 "anchor term and inserts the result prog_1 into analytics.var_x.", W - L - R, 10)
    for j, line in enumerate(sub):
        c.text(L, 0.72 + j * 0.2, line, 10, ink2, va="baseline")

    rows = [
        (r"$\alpha$", [("alpha  =  ", {"color": ink2}), (f"{ALPHA.numerator / ALPHA.denominator:g}", {})]),
        ("max_k", [("MAX(K)  =  ", {"color": ink2}), (f"{max_k}", {})]),
        ("ANZ", [("COUNT(*) of the window  =  ", {"color": ink2}), (f"{anz}", {})]),
        ("K", [("ROW_NUMBER() OVER (ORDER BY DATUM ASC)", {"color": ink2})]),
        ("rn", [("ROW_NUMBER() OVER (ORDER BY DATUM DESC)", {"color": ink2})]),
        (r"$Y_K$", [("SUM(KUENDIGER) of the K-th DATUM", {"color": ink2})]),
        (r"$Y_{11-i}$", [("Y at rn = max_k − (10 − i), which is K = 11 − i", {"color": ink2})]),
    ]
    row_h = 0.215
    d_top = 1.07 + (len(sub) - 1) * 0.2
    d_bot = d_top + (len(rows) - 1) * row_h
    dx = 5.95
    for j, (name, parts) in enumerate(rows):
        yy = d_top + j * row_h
        c.text(dx, yy, name, 10.5 if "$" in name else 9.5, ink, ha="right")
        c.text(dx + 0.1, yy, "=", 9.5, ink, ha="left")
        c.run(dx + 0.3, yy, [(s, {"size": 9.5, **o}) for s, o in parts])
    c.text(L, (d_top + d_bot) / 2,
           r"$\mathrm{prog\_1}(i) \;=\; \sum_{K=1+i}^{\mathrm{max\_k}-(10-i)}\,"
           r"\alpha\,(1-\alpha)^{K}\, Y_K \;+\; (1-\alpha)^{\mathrm{ANZ}+1}\, Y_{11-i}$", 13.5)

    # ---- key (right of the input), with the matching lines of the PL/pgSQL code --
    in_top = d_bot + 0.45
    tx = kx0 + 0.3
    sw = 0.2
    k1 = in_top + 0.04
    c.rect(kx0, k1 - 0.055, kx0 + sw, k1 + 0.055, T["x"], 4 * PX, RIGHT, label="key swatch")
    c.text(tx, k1, "X, the sum over the shaded cells", 9.5, ink, weight="medium", ha="left")
    c.mono(tx, k1 + 0.21, "alpha * POWER((1-alpha), k) * KUENDIGER", ha="left")
    c.mono(tx, k1 + 0.39, "WHERE K BETWEEN 1 + i AND max_k - (10-i)", ha="left")
    k2 = k1 + 0.64
    c.dot(kx0 + sw / 2, k2, T["anchor"], label="key dot")
    c.text(tx, k2, "anchor row, K = 11 − i", 9.5, ink, weight="medium", ha="left")
    xe = c.width_of(c.mono(tx, k2 + 0.21, "WHERE rn = max_k - (10-i)", ha="left"))
    c.text(tx + xe + 0.1, k2 + 0.21, "(DATUM DESC)", 9.5, ink2, ha="left")
    k3 = k2 + 0.47
    c.rect(kx0, k3 - 0.055, kx0 + sw, k3 + 0.055, T["anchor"], 4 * PX, RIGHT, label="key swatch")
    c.run(tx, k3, [("anchor term", {"weight": "medium"}),
                   (f", weight {float(one_minus):g}$^{{{anz + 1}}}$ = "
                    f"{float(p0['anchor_weight']):.4f}", {"color": ink2})])
    c.mono(tx, k3 + 0.21, "POWER((1-alpha), ANZ + 1) *", ha="left")
    key_bottom = k3 + 0.3

    # ---- input series ------------------------------------------------------
    # column headers sit at r_k; the key has to end above them
    in_h = max(0.85, key_bottom + 0.2 - in_top - 0.67)
    base = in_top + in_h
    r_y, r_d = base + 0.17, base + 0.39
    r_k = base + 0.67
    c.text(L, in_top, "Input", 10, ink, weight="semibold", va="center")
    c.text(L, in_top + 0.22, r"$Y_K$ per DATUM", 10.5, ink2, va="center")
    ymax = max(max(s["Y"] for s in series), 1)
    bw = min(0.12, cw * 0.55)
    for s in series:
        h = max(s["Y"], 0) / ymax * in_h
        c.rect(kx(s["K"]) - bw / 2, base - h, kx(s["K"]) + bw / 2, base, T["input"],
               4 * PX, TOP, label="input column")
    c.hline(gx0, gx1, base, T["axis"], z=2)

    c.text(lab_x, r_y, r"$Y_K$", 10.5, ink2, ha="right")
    c.text(lab_x, r_d, "DATUM", 9.5, ink2, ha="right")
    c.text(lab_x, r_k, "K", 9.5, ink2, ha="right")
    for s in series:
        if every_k or s["K"] in ticks:
            c.text(kx(s["K"]), r_y, str(s["Y"]), 9.5, ink2, ha="center")
            c.text(kx(s["K"]), r_k, str(s["K"]), 9.5, ink, ha="center", weight="medium")
        if s["K"] in ticks:
            c.text(kx(s["K"]), r_d, f"{s['datum']:%m-%d}", 9.5, ink2, ha="center")

    # ---- grid: one row per pass --------------------------------------------
    win_x = kx0 + 0.2           # centre of the window column
    anc_x = win_x + 0.6         # centre of the anchor column
    bx0 = anc_x + 0.4           # prog_1 baseline
    c.text(col_i, r_k, "i", 9.5, ink2, ha="center")
    c.text(col_id, r_k, "id", 9.5, ink2, ha="center")
    c.text(win_x, r_k, "window", 9.5, ink2, ha="center")
    c.text(anc_x, r_k, "anchor", 9.5, ink2, ha="center")
    c.text(bx0, r_k, "prog_1 = X + anchor term", 9.5, ink, weight="medium", ha="left")

    label_w = max(c.measure(fmt_prog(p["prog_1"])) for p in passes)
    bx1 = W - R - label_w - 0.1
    progs = [p["prog_1"] for p in passes if p["prog_1"] is not None]
    pmax = float(max(progs)) if progs and max(progs) > 0 else 1.0
    bscale = (bx1 - bx0) / pmax
    bh = rh * 0.5

    m_top = r_k + 0.17
    for row, p in enumerate(passes):
        yt = m_top + row * rh
        yc = yt + rh / 2
        c.hline(col_id + 0.2, bx0, yc, T["grid"], lw=0.6, z=0.4)   # row guide
        c.text(col_i, yc, str(p["i"]), 9.5, ink, ha="center")
        c.text(col_id, yc, str(p["id"]), 9.5, ink2, ha="center")
        for k, w in p["weights"].items():
            x0 = gx0 + (k - 1) * cw
            c.rect(x0 + GAP / 2, yt + GAP / 2, x0 + cw - GAP / 2, yt + rh - GAP / 2, shade(w),
                   2 * PX, ALL, label="cell")
        lo, hi = p["window"]
        c.text(win_x, yc, f"{lo}–{hi}" if p["ANZ"] else "none", 9.5, ink2, ha="center", mask=True)
        c.text(anc_x, yc, "none" if p["anchor_K"] is None else str(p["anchor_K"]), 9.5, ink2,
               ha="center", mask=True)
        if p["anchor_K"] is not None and p["anchor_K"] <= max_k:
            c.dot(kx(p["anchor_K"]), yc, T["anchor"], r=min(0.05, cw * 0.23), label="anchor dot")
        if p["prog_1"] is None:
            c.text(bx0 + 0.08, yc, "NULL", 9.5, ink2, ha="left")
            continue
        xe = bx0 + float(p["X"]) * bscale
        pe = bx0 + float(p["prog_1"]) * bscale
        c.rect(bx0, yc - bh / 2, xe - GAP / 2, yc + bh / 2, T["x"], label="X bar")
        c.rect(xe + GAP / 2, yc - bh / 2, pe, yc + bh / 2, T["anchor"], 4 * PX, RIGHT,
               label="anchor bar")
        c.text(max(pe, bx0) + 0.08, yc, fmt_prog(p["prog_1"]), 9.5, ink, ha="left")
    m_bot = m_top + len(passes) * rh
    c.vline(bx0, m_top + 0.03, m_bot - 0.03, T["axis"], z=2)

    # ---- weight scale, on the same columns ----------------------------------
    s_top = m_bot + 0.2
    s_bot = s_top + 0.14
    for k in range(1, max_k + 1):
        x0 = gx0 + (k - 1) * cw
        c.rect(x0 + GAP / 2, s_top, x0 + cw - GAP / 2, s_bot, shade(weight(k)), 2 * PX, ALL,
               label="scale cell")
    c.text(lab_x, (s_top + s_bot) / 2, r"$\alpha(1-\alpha)^K$", 10.5, ink2, ha="right")
    for k in ticks:
        c.text(kx(k), s_bot + 0.17, sig3(float(weight(k))), 9.5, ink2, ha="center")
    c.text(kx0, (s_top + s_bot) / 2, "weight of each K = cell shade", 9.5, ink2, ha="left")

    # ---- one row in detail: i = 0 ------------------------------------------
    y = s_bot + 0.62
    if p0["prog_1"] is not None and p0["X"] > 0:
        lo, hi = p0["window"]
        c.run(L, y, [(f"Row i = {p0['i']} in detail", {"size": 10, "weight": "semibold"}),
                     ("   each window term ", {"color": ink2}),
                     (r"$\alpha(1-\alpha)^K\, Y_K$", {"size": 10.5, "color": ink2}),
                     (" is one segment, followed by the anchor term", {"color": ink2})])
        tip = fixed(p0["prog_1"], 5)
        tip_w = c.measure(tip, 10, weight="semibold")
        ex1 = W - R - tip_w - 0.12
        escale = (ex1 - gx0) / float(p0["prog_1"])
        ey = y + 0.36
        eh = 0.16
        x = gx0
        k_row = ey + eh / 2 + 0.15
        last_label = gx0 - 1.0
        for k, term in p0["terms"].items():
            x1 = x + float(term) * escale
            c.rect(x, ey - eh / 2, x1 - GAP, ey + eh / 2, T["x"], label="example segment")
            # label a segment with its K only where the number fits
            mid, half = (x + x1 - GAP) / 2, c.measure(str(k)) / 2 + 0.03
            if mid - half > last_label and half * 2 < x1 - x:
                c.text(mid, k_row, str(k), 9.5, ink2, ha="center")
                last_label = mid + half
            x = x1
        xa = x + float(p0["anchor_term"]) * escale
        c.rect(x, ey - eh / 2, xa, ey + eh / 2, T["anchor"], 4 * PX, RIGHT,
               label="example segment")
        c.text((x + xa) / 2, k_row, str(p0["anchor_K"]), 9.5, ink2, ha="center")
        c.text(lab_x, k_row, "K", 9.5, ink2, ha="right")
        c.text(xa + 0.1, ey, tip, 10, ink, weight="semibold", ha="left")
        c.text(xa + 0.1, k_row, f"prog_1, id {p0['id']}", 9.5, ink2, ha="left")

        # brackets under the X part and under the anchor part
        br = k_row + 0.17
        tick = 0.05
        for x0b, x1b in ((gx0, x - GAP), (x, xa)):
            c.hline(x0b, x1b, br, T["axis"], lw=0.8, z=2)
            c.vline(x0b, br - tick, br, T["axis"], lw=0.8, z=2)
            c.vline(x1b, br - tick, br, T["axis"], lw=0.8, z=2)
        xs, at, ps = fixed(p0["X"], 5), fixed(p0["anchor_term"], 5), fixed(p0["prog_1"], 5)
        closes = Fraction(xs) + Fraction(at) == Fraction(ps)
        lab_y = br + 0.2
        xmid = (gx0 + x) / 2
        parts = [(f"X = {xs}", {"weight": "medium"}),
                 (f"   the sum of the {p0['ANZ']} terms for K = {lo}..{hi}", {"color": ink2})]
        c.run(xmid - c.run_width(parts) / 2, lab_y, parts)
        amid = (x + xa) / 2
        c.text(amid, lab_y,
               rf"{float(one_minus):g}$^{{{p0['ANZ'] + 1}}}$ × {p0['anchor_value']} = {at}",
               9.5, ink, ha="center")
        c.text(amid, lab_y + 0.21,
               f"K = {p0['anchor_K']}  (rn = {p0['anchor_rn']}, DATUM DESC)", 9.5, ink2,
               ha="center")
        y = lab_y + 0.21
        if not closes:
            c.text(L, y + 0.25, "Values rounded to 5 decimals on their own.", 9.5, ink2,
                   ha="left")
            y += 0.25

    # ---- provenance and notes ----------------------------------------------
    y += 0.45
    src = " + ".join(sources)
    d0, d1 = series[0]["datum"], series[-1]["datum"]
    notes = [
        "Code: src/exponential_smoothing_pg.pls (port of src/EXPONENTIAL_SMOOTHING.pls), "
        "alpha NUMERIC := 0.2.",
        f"Data: the {max_k} dates that sql/{src} insert into analytics.source_table, "
        f"{d0:%Y-%m-%d} to {d1:%Y-%m-%d}.",
        "Every number in the figure is recomputed from these files by scripts/viz.py.",
        "id = identity value of analytics.var_x on the first run after the table is created; "
        f"TRUNCATE keeps the counter, so a second run writes {len(passes) + 1} to "
        f"{2 * len(passes)}.",
    ]
    lines = [line for s in notes for line in c.wrap(s, W - L - R)]
    for j, s in enumerate(lines):
        c.text(L, y + j * 0.21, s, 9.5, ink2, ha="left")
    return c.save(y + (len(lines) - 1) * 0.21 + 0.36, out_path)


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Draw the exponential smoothing figure.")
    ap.add_argument("--repo", type=Path, default=here.parent, help="repository root")
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: docs/)")
    ap.add_argument("--truth", type=Path, default=None,
                    help="optional ground-truth JSON to compare every number against")
    ap.add_argument("--strict", action="store_true", help="exit 1 on layout warnings")
    a = ap.parse_args()

    rows, sources = read_source_rows(a.repo / "sql")
    series, max_k, passes = replay(rows)
    sanity_check(series, passes)
    if a.truth:
        check_against(a.truth, series, max_k, passes)

    out = a.out or a.repo / "docs"
    out.mkdir(parents=True, exist_ok=True)
    warnings = 0
    for theme in ("light", "dark"):
        warnings += draw(theme, series, max_k, passes, sources,
                         out / f"exponential_smoothing_{theme}.png")
    if warnings and a.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
