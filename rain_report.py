"""
MasRainman · Tamil Nadu rainfall report (animated)

Two modes
  daily : one rain-day (24 h ending 08:30 IST). Map colours build up from zero.
  accum : total from a start date to the run day. Map fills day by day.

Examples
  python rain_report.py daily                         # latest complete day
  python rain_report.py daily --date 2026-10-18
  python rain_report.py accum --start 2026-10-01      # Oct 1 to today
  python rain_report.py accum --start 2026-10-01 --end 2026-10-18
  python rain_report.py accum --start 2026-10-01 --live   # also add today's running (partial) day

Outputs go to ./out : MP4 video, GIF, and a PNG of the final frame.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.spatial import cKDTree
from shapely.geometry import shape, Point
from shapely.ops import unary_union

import tnsmart_fetch as tf
from tn_names import MONTHS_TA, canon, ta, date_ta, time_ta

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")
OUT_DIR = os.path.join(HERE, "out")

# ----------------------------------------------------------------- settings
BRAND = "MasRainman"
BRAND_LINE = "தயாரிப்பு: MasRainman"
SITE = "masrainman.blogspot.com"
FPS = 30
W, H = 1080, 1620                     # 2:3 portrait, good for Facebook / WhatsApp
IDW_POWER = 2
IDW_RADIUS_KM = 44
IDW_K = 12

# colour classes ---------------------------------------------------------
# (lower bound inclusive, label, colour)
SCALE_ACCUM = [
    (0.0, "0 மி.மீ.", "#e4e7ee"),
    (0.1, "0.1 – 25", "#86efac"),
    (25.1, "25.1 – 50", "#22c55e"),
    (50.1, "50.1 – 100", "#facc15"),
    (100.1, "100.1 – 150", "#f59e0b"),
    (150.1, "150.1 – 200", "#ef4444"),
    (200.1, "200.1 – 300", "#c084fc"),
    (300.1, "300.1 – 400", "#9333ea"),
    (400.1, "> 400", "#581c87"),
]
SCALE_DAILY = [  # IMD 24-hour categories
    (0.0, "0 மி.மீ.", "#e4e7ee"),
    (0.1, "0.1 – 2.4", "#bbf7d0"),
    (2.5, "2.5 – 15.5", "#4ade80"),
    (15.6, "15.6 – 64.4", "#facc15"),
    (64.5, "64.5 – 115.5", "#f59e0b"),
    (115.6, "115.6 – 204.4", "#ef4444"),
    (204.5, "≥ 204.5", "#9333ea"),
]

# theme
BG = (14, 21, 36)
PANEL = (22, 31, 50)
PANEL_EDGE = (36, 49, 74)
TEXT = (232, 237, 245)
MUTED = (138, 151, 173)
ACCENT = (91, 155, 213)
ACCENT_SOFT = (120, 175, 230)
TRACK = (40, 52, 76)


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ----------------------------------------------------------------- fonts
_font_cache = {}


def font(size, weight="Regular"):
    key = (size, weight)
    if key not in _font_cache:
        path = os.path.join(FONT_DIR, f"MuktaMalar-{weight}.ttf")
        try:
            _font_cache[key] = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
        except Exception:  # Pillow without libraqm: Tamil vowel signs may misplace
            _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def tw(draw, text, f):
    return draw.textlength(text, font=f)


def text_right(draw, xy, text, f, fill):
    x, y = xy
    draw.text((x - tw(draw, text, f), y), text, font=f, fill=fill)


def wrap(draw, text, f, width):
    words, lines, cur = text.split(" "), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if tw(draw, t, f) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def ellipsize(draw, text, f, width):
    if tw(draw, text, f) <= width:
        return text
    while text and tw(draw, text + "…", f) > width:
        text = text[:-1]
    return text + "…"


def rrect(draw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def fmt(v):
    return f"{v:,.1f}"


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


# ----------------------------------------------------------------- geography
class Geo:
    def __init__(self):
        with open(os.path.join(HERE, "data", "tn_districts.geojson"), encoding="utf-8") as f:
            gj = json.load(f)
        self.districts = {}
        for ft in gj["features"]:
            name = canon(ft["properties"]["district"])
            g = shape(ft["geometry"])
            self.districts[name] = self.districts[name].union(g) if name in self.districts else g
        self.state = unary_union(list(self.districts.values()))
        self.lon0, self.lat0, self.lon1, self.lat1 = self.state.bounds
        self.kx = math.cos(math.radians((self.lat0 + self.lat1) / 2))

    def km(self, lon, lat):
        return np.column_stack([np.asarray(lon) * 111.32 * self.kx, np.asarray(lat) * 110.57])


def polys(geom):
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


class Projector:
    """lon/lat -> pixel inside a box, keeping true shape."""

    def __init__(self, bounds, kx, box, pad=10):
        lon0, lat0, lon1, lat1 = bounds
        x0, y0, x1, y1 = box
        bw, bh = (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad
        gw, gh = (lon1 - lon0) * kx, (lat1 - lat0)
        self.s = min(bw / gw, bh / gh)
        self.kx = kx
        self.ox = x0 + pad + (bw - gw * self.s) / 2
        self.oy = y0 + pad + (bh - gh * self.s) / 2
        self.lon0, self.lat1 = lon0, lat1

    def __call__(self, lon, lat):
        return (self.ox + (lon - self.lon0) * self.kx * self.s, self.oy + (self.lat1 - lat) * self.s)

    def inverse_grid(self, x0, y0, w, h):
        xs = np.arange(w) + x0 + 0.5
        ys = np.arange(h) + y0 + 0.5
        lon = self.lon0 + (xs - self.ox) / (self.s * self.kx)
        lat = self.lat1 - (ys - self.oy) / self.s
        return np.meshgrid(lon, lat)


def draw_geom(draw, geom, proj, fill=None, outline=None, width=1, scale=1.0, off=(0, 0)):
    for p in polys(geom):
        for ring_i, ring in enumerate([p.exterior] + list(p.interiors)):
            pts = [((x - off[0]) * scale, (y - off[1]) * scale) for x, y in (proj(a, b) for a, b in ring.coords)]
            if len(pts) < 3:
                continue
            if fill is not None:
                draw.polygon(pts, fill=fill if ring_i == 0 else 0)
            if outline is not None:
                draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")


# ----------------------------------------------------------------- data
class Period:
    """Station values for the report period, as a (days x stations) matrix."""

    def __init__(self, days_dict, geo):
        dates = sorted(days_dict)
        idx, meta = {}, []
        for d in dates:
            for s in days_dict[d]:
                key = (canon(s["district"]), s["station"], round(s["lat"], 3), round(s["lon"], 3))
                if key not in idx:
                    idx[key] = len(meta)
                    meta.append({"district": key[0], "station": s["station"], "lat": s["lat"], "lon": s["lon"]})
        V = np.full((len(dates), len(meta)), np.nan)
        for i, d in enumerate(dates):
            for s in days_dict[d]:
                key = (canon(s["district"]), s["station"], round(s["lat"], 3), round(s["lon"], 3))
                if s["rain"] is not None:
                    V[i, idx[key]] = s["rain"]
        keep = ~np.all(np.isnan(V), axis=0)          # stations that reported at least once
        self.dates = dates
        self.meta = [m for m, k in zip(meta, keep) if k]
        self.V = V[:, keep]
        self.lat = np.array([m["lat"] for m in self.meta])
        self.lon = np.array([m["lon"] for m in self.meta])
        self.dist = np.array([m["district"] for m in self.meta])
        self.names = [m["station"] for m in self.meta]
        # fix district names that are not in the boundary file by point-in-polygon
        for i, dn in enumerate(self.dist):
            if dn not in geo.districts:
                p = Point(self.lon[i], self.lat[i])
                for k, g in geo.districts.items():
                    if g.contains(p):
                        self.dist[i] = k
                        break
        self.cum = np.nancumsum(self.V, axis=0)
        self.rainy_days = np.cumsum(np.nan_to_num(self.V) > 0, axis=0)


# ----------------------------------------------------------------- IDW field
class Field:
    def __init__(self, geo, proj, box, lon, lat):
        x0, y0, x1, y1 = box
        self.box = box
        self.w, self.h = x1 - x0, y1 - y0
        glon, glat = proj.inverse_grid(x0, y0, self.w, self.h)
        # state mask, anti-aliased (3x supersample)
        ss = 3
        m = Image.new("L", (self.w * ss, self.h * ss), 0)
        dm = ImageDraw.Draw(m)
        draw_geom(dm, geo.state, proj, fill=255, scale=ss, off=(x0, y0))
        self.mask = m.resize((self.w, self.h), Image.LANCZOS)
        inside = np.asarray(self.mask) > 0
        self.inside = inside
        pts = geo.km(glon[inside], glat[inside])
        tree = cKDTree(geo.km(lon, lat))
        k = min(IDW_K, len(lon))
        d, ix = tree.query(pts, k=k)
        if k == 1:
            d, ix = d[:, None], ix[:, None]
        d = np.maximum(d, 0.5)
        wgt = 1.0 / d ** IDW_POWER
        far = d > IDW_RADIUS_KM
        far[:, 0] = False                              # always keep the nearest station
        wgt[far] = 0
        self.ix = ix
        self.wgt = wgt / wgt.sum(axis=1, keepdims=True)

    def values(self, v):
        return (self.wgt * v[self.ix]).sum(axis=1)

    def image(self, v, scale):
        bounds = np.array([b for b, _, _ in scale])
        cols = np.array([hex2rgb(c) for _, _, c in scale], dtype=np.uint8)
        f = self.values(v)
        cls = np.searchsorted(bounds, f + 1e-9, side="right") - 1
        cls[f < 0.05] = 0
        rgb = np.zeros((self.h, self.w, 3), np.uint8)
        rgb[self.inside] = cols[np.clip(cls, 0, len(cols) - 1)]
        im = Image.fromarray(rgb, "RGB").convert("RGBA")
        im.putalpha(self.mask)
        return im


def classify(v, scale):
    bounds = [b for b, _, _ in scale]
    if v < 0.05:
        return 0
    c = 0
    for i, b in enumerate(bounds):
        if v >= b - 1e-9:
            c = i
    return c


# ----------------------------------------------------------------- renderer
class Report:
    MAP_BOX = (48, 392, 590, 1066)
    INSET_X = (606, 1032)

    def __init__(self, mode, period, geo, title_dates, sub, scale, card3, headline_thresholds):
        self.mode, self.P, self.geo = mode, period, geo
        self.scale = scale
        self.sub = sub
        self.title_dates = title_dates
        self.card3 = card3                      # (threshold, label, sublabel)
        self.thr_list = headline_thresholds
        mx0, my0, mx1, my1 = self.MAP_BOX
        self.map_inner = (mx0 + 14, my0 + 14, mx1 - 14, my1 - 40)
        self.proj = Projector((geo.lon0, geo.lat0, geo.lon1, geo.lat1), geo.kx, self.map_inner, pad=6)
        self.field = Field(geo, self.proj, self.map_inner, period.lon, period.lat)
        self.created = datetime.now(tf.IST)
        self.base = self._static_layer()
        self.lines = self._boundary_layer()
        self._inset_cache = {}

    # ---- static parts -------------------------------------------------
    def _static_layer(self):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((0, 0, W, 7), fill=ACCENT)
        # faint diagonal watermark
        wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dw = ImageDraw.Draw(wm)
        f = font(30, "SemiBold")
        for y in range(-200, H + 200, 230):
            for x in range(-300, W + 300, 420):
                dw.text((x + (y // 230) % 2 * 210, y), BRAND, font=f, fill=(255, 255, 255, 9))
        wm = wm.rotate(28, resample=Image.BICUBIC, center=(W / 2, H / 2))
        im.paste(wm, (0, 0), wm)
        d = ImageDraw.Draw(im, "RGBA")

        d.text((48, 26), "மழைப்பொழிவு அறிக்கை", font=font(56, "ExtraBold"), fill=TEXT)
        pill = "முழு தமிழ்நாடு"
        pf = font(22, "SemiBold")
        pw = tw(d, pill, pf) + 40
        rrect(d, (W - 48 - pw, 44, W - 48, 90), 23, fill=(25, 38, 62), outline=(45, 70, 110))
        d.text((W - 48 - pw + 20, 50), pill, font=pf, fill=ACCENT_SOFT)

        # panels
        mx0, my0, mx1, my1 = self.MAP_BOX
        rrect(d, self.MAP_BOX, 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((mx0 + 18, my1 - 36), "IDW மேற்பரப்பு (இடைக்கணிப்பு)", font=font(17), fill=MUTED)
        ix0, ix1 = self.INSET_X
        for i in range(3):
            y0 = my0 + i * 230
            rrect(d, (ix0, y0, ix1, y0 + 214), 16, fill=PANEL, outline=PANEL_EDGE)
        rrect(d, (48, 1084, 528, 1404), 16, fill=PANEL, outline=PANEL_EDGE)
        rrect(d, (544, 1084, 1032, 1404), 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((68, 1098), "முதல் 5 நிலையங்கள் (மி.மீ.)", font=font(20, "SemiBold"), fill=MUTED)
        d.text((564, 1098), "முதல் 5 மாவட்டங்கள் · சராசரி மழை (மி.மீ.)", font=font(20, "SemiBold"), fill=MUTED)
        rrect(d, (48, 1420, 1032, 1532), 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((68, 1430), "மழை அளவு வகைகள் · ஒவ்வொரு வகையிலும் நிலையங்கள்", font=font(19, "SemiBold"), fill=MUTED)

        # footer
        d.line((48, 1548, 1032, 1548), fill=PANEL_EDGE, width=1)
        f17 = font(17)
        d.text((48, 1556), f"ஆதாரம்: TN-SMART (TNSDMA) · IDW p={IDW_POWER}, ஆரம் ≈ {IDW_RADIUS_KM} கி.மீ.",
               font=f17, fill=MUTED)
        c = self.created
        made = f"உருவாக்கம்: {c.day} {MONTHS_TA[c.month - 1]} {c.year}, {time_ta(c)}"
        text_right(d, (1032, 1556), made, f17, MUTED)
        d.text((48, 1580), BRAND_LINE, font=font(22, "Bold"), fill=ACCENT_SOFT)
        text_right(d, (1032, 1583), SITE, font(18), MUTED)
        return im

    def _boundary_layer(self):
        x0, y0, x1, y1 = self.map_inner
        ss = 3
        lay = Image.new("RGBA", ((x1 - x0) * ss, (y1 - y0) * ss), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        for g in self.geo.districts.values():
            draw_geom(dl, g, self.proj, outline=(20, 28, 44, 150), width=2 * ss // 2 + 1, scale=ss, off=(x0, y0))
        draw_geom(dl, self.geo.state, self.proj, outline=(10, 15, 26, 230), width=2 * ss, scale=ss, off=(x0, y0))
        return lay.resize((x1 - x0, y1 - y0), Image.LANCZOS)

    # ---- per-frame stats -------------------------------------------------
    def stats(self, v, reported):
        P = self.P
        vv = np.where(reported, v, np.nan)
        dist_avg = {}
        for dn in np.unique(P.dist[reported]):
            m = (P.dist == dn) & reported
            dist_avg[dn] = float(np.nanmean(vv[m]))
        top_d = sorted(dist_avg.items(), key=lambda kv: -kv[1])
        order = np.argsort(-np.nan_to_num(vv, nan=-1))
        top_s = [i for i in order[:5] if reported[i]]
        counts = [0] * len(self.scale)
        for x in vv[reported]:
            counts[classify(x, self.scale)] += 1
        return {
            "n_rep": int(reported.sum()),
            "n_dist": len(dist_avg),
            "n_rain": int(np.sum(np.nan_to_num(vv) > 0)),
            "n_thr": int(np.sum(np.nan_to_num(vv) >= self.card3[0])),
            "state_avg": float(np.nanmean(vv)) if reported.any() else 0.0,
            "top_d": top_d,
            "top_s": top_s,
            "counts": counts,
            "vv": vv,
        }

    def headline(self, st):
        vv = np.nan_to_num(st["vv"])
        if st["top_d"] and st["top_d"][0][1] >= 0.5:
            dn, avg = st["top_d"][0]
            for thr in self.thr_list:
                n = int(np.sum(vv > thr))
                if n:
                    return (f"{n} நிலையங்களில் {thr:g} மி.மீ.க்கு மேல் மழை பதிவு; மாவட்டங்களில் "
                            f"{ta(dn)} {fmt(avg)} மி.மீ. நிலையச் சராசரியுடன் முதலிடம்.")
            return f"மாவட்டங்களில் {ta(dn)} {fmt(avg)} மி.மீ. நிலையச் சராசரியுடன் முதலிடம்."
        return "மாநிலம் முழுவதும் குறிப்பிடத்தக்க மழை பதிவாகவில்லை."

    # ---- inset ---------------------------------------------------------
    def inset(self, dn, box):
        if dn not in self._inset_cache:
            g = self.geo.districts.get(dn)
            x0, y0, x1, y1 = box
            pr = Projector(g.bounds, self.geo.kx, (0, 0, x1 - x0, y1 - y0), pad=8)
            self._inset_cache[dn] = (g, pr)
        return self._inset_cache[dn]

    def draw_inset(self, im, d, rank, dn, st, box_card, alpha=1.0):
        P = self.P
        cx0, cy0, cx1, cy1 = box_card
        vbox = (cx0 + 12, cy0 + 52, cx0 + 226, cy1 - 8)
        g, pr = self.inset(dn, vbox)
        vx0, vy0 = vbox[0], vbox[1]
        vw, vh = vbox[2] - vbox[0], vbox[3] - vbox[1]
        avg = dict(st["top_d"]).get(dn, 0.0)
        col = hex2rgb(self.scale[classify(avg, self.scale)][2])
        tint = tuple(int(c * 0.45 + b * 0.55) for c, b in zip(col, PANEL))
        ss = 3
        lay = Image.new("RGBA", (vw * ss, vh * ss), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        draw_geom(dl, g, pr, fill=tint + (255,), outline=tuple(min(255, c + 60) for c in tint) + (255,),
                  width=2 * ss, scale=ss)
        m = (P.dist == dn) & ~np.isnan(st["vv"])
        idx = np.where(m)[0]
        vv = st["vv"]
        best = idx[np.argmax(vv[idx])] if len(idx) else None
        for i in sorted(idx, key=lambda i: vv[i]):
            x, y = pr(P.lon[i], P.lat[i])
            r = 7 * ss
            c = hex2rgb(self.scale[classify(vv[i], self.scale)][2])
            dl.ellipse((x * ss - r, y * ss - r, x * ss + r, y * ss + r), fill=c + (255,), outline=(15, 20, 32, 255),
                       width=ss)
        lay = lay.resize((vw, vh), Image.LANCZOS)
        im.paste(lay, (vx0, vy0), lay)
        if best is not None and vv[best] > 0:
            x, y = pr(P.lon[best], P.lat[best])
            x, y = x + vx0, y + vy0
            d.ellipse((x - 13, y - 13, x + 13, y + 13), outline=(255, 255, 255), width=3)
            lab = fmt(vv[best])
            f = font(16, "Bold")
            lw = tw(d, lab, f)
            lx = min(max(x - lw / 2, vx0), vx0 + vw - lw)
            ly = y - 36 if y - 36 > vy0 else y + 14
            d.text((lx + 1, ly + 1), lab, font=f, fill=(0, 0, 0, 200))
            d.text((lx, ly), lab, font=f, fill=TEXT)
        # text block
        rx = cx0 + 16
        d.ellipse((rx, cy0 + 16, rx + 28, cy0 + 44), fill=(150, 90, 220) if rank == 1 else (220, 70, 70))
        d.text((rx + 9, cy0 + 16), str(rank), font=font(18, "Bold"), fill=(255, 255, 255))
        d.text((rx + 40, cy0 + 9), ellipsize(d, ta(dn), font(26, "Bold"), cx1 - rx - 56), font=font(26, "Bold"),
               fill=TEXT)
        tx = cx0 + 240
        d.text((tx, cy0 + 56), "சராசரி மழை", font=font(16), fill=MUTED)
        d.text((tx, cy0 + 70), f"{fmt(avg)}", font=font(38, "ExtraBold"), fill=TEXT)
        d.text((tx + tw(d, fmt(avg), font(38, "ExtraBold")) + 6, cy0 + 90), "மி.மீ.", font=font(16), fill=MUTED)
        d.text((tx, cy0 + 122), "அதிகபட்ச நிலையம்", font=font(16), fill=MUTED)
        if best is not None:
            d.text((tx, cy0 + 136), f"{fmt(vv[best])} மி.மீ.", font=font(24, "Bold"), fill=TEXT)
            d.text((tx, cy0 + 168), ellipsize(d, P.names[best], font(15), cx1 - tx - 14), font=font(15), fill=MUTED)

    # ---- full frame ------------------------------------------------------
    def frame(self, v, reported, sub_text, badge=None, progress=None, reveal_markers=0.0, final_stats=None):
        im = self.base.copy()
        d = ImageDraw.Draw(im, "RGBA")
        st = self.stats(v, reported)
        fs = final_stats or st

        d.text((48, 110), sub_text, font=font(21), fill=MUTED)
        hl = wrap(d, self.headline(st), font(25, "SemiBold"), W - 96)
        for i, line in enumerate(hl[:2]):
            d.text((48, 148 + i * 36), line, font=font(25, "SemiBold"), fill=TEXT)

        if progress is not None:
            rrect(d, (48, 230, W - 48, 235), 3, fill=TRACK)
            rrect(d, (48, 230, 48 + (W - 96) * progress, 235), 3, fill=ACCENT)

        # stat cards
        cw = (W - 96 - 3 * 14) / 4
        best_i = st["top_s"][0] if st["top_s"] else None
        cards = [
            ("தரவு அளித்த நிலையங்கள்", f"{st['n_rep']}", f"{st['n_dist']} மாவட்டங்கள்"),
            ("மழை பெய்த நிலையங்கள்", f"{st['n_rain']}", self.card2_sub(st, v)),
            (self.card3[1], f"{st['n_thr']}", self.card3[2]),
            ("அதிகபட்ச நிலையம்",
             f"{fmt(st['vv'][best_i])} மி.மீ." if best_i is not None else "—",
             self.P.names[best_i] if best_i is not None else ""),
        ]
        for i, (lab, val, sub) in enumerate(cards):
            x0 = 48 + i * (cw + 14)
            box = (x0, 248, x0 + cw, 372)
            rrect(d, box, 14, fill=PANEL, outline=PANEL_EDGE)
            d.rectangle((x0 + 1, 264, x0 + 5, 356), fill=ACCENT)
            d.text((x0 + 20, 258), ellipsize(d, lab, font(15), cw - 28), font=font(15), fill=MUTED)
            vf = font(44 if i < 3 else 34, "Bold")
            d.text((x0 + 20, 278 if i < 3 else 286), val, font=vf, fill=TEXT)
            d.text((x0 + 20, 338), ellipsize(d, sub, font(16), cw - 30), font=font(16), fill=MUTED)

        # map
        x0, y0, x1, y1 = self.map_inner
        fim = self.field.image(np.where(reported, v, 0.0), self.scale)
        glow = fim.filter(ImageFilter.GaussianBlur(8))
        glow.putalpha(glow.getchannel("A").point(lambda a: a * 0.35))
        im.paste(glow, (x0, y0), glow)
        im.paste(fim, (x0, y0), fim)
        im.paste(self.lines, (x0, y0), self.lines)
        d = ImageDraw.Draw(im, "RGBA")
        if badge:
            bf = font(30, "ExtraBold")
            bw_ = tw(d, badge, bf) + 36
            rrect(d, (x0 + 6, y0 + 6, x0 + 6 + bw_, y0 + 58), 14, fill=(10, 16, 28, 215), outline=(60, 90, 140))
            d.text((x0 + 24, y0 + 10), badge, font=bf, fill=TEXT)

        # numbered markers on the top 3 districts' max stations and top 5 stations
        if reveal_markers > 0:
            a = int(255 * ease(reveal_markers))
            for rank, i in enumerate(fs["top_s"][:5], 1):
                x, y = self.proj(self.P.lon[i], self.P.lat[i])
                r = 12 * (0.6 + 0.4 * ease_out(reveal_markers))
                d.ellipse((x - r, y - r, x + r, y + r), fill=(110, 60, 180, a), outline=(255, 255, 255, a), width=2)
                f = font(17, "Bold")
                d.text((x - tw(d, str(rank), f) / 2, y - 12), str(rank), font=f, fill=(255, 255, 255, a))

        # insets: top 3 districts
        for k in range(3):
            if k < len(st["top_d"]):
                y0c = self.MAP_BOX[1] + k * 230
                self.draw_inset(im, d, k + 1, st["top_d"][k][0], st, (self.INSET_X[0], y0c, self.INSET_X[1], y0c + 214))

        # top 5 stations
        for r, i in enumerate(st["top_s"]):
            y = 1140 + r * 52
            d.ellipse((68, y + 2, 100, y + 34), fill=(110, 60, 180) if r < 2 else (150, 80, 210))
            d.text((79 if r < 9 else 74, y + 3), str(r + 1), font=font(19, "Bold"), fill=(255, 255, 255))
            d.text((114, y - 4), ellipsize(d, self.P.names[i], font(23, "Bold"), 290), font=font(23, "Bold"), fill=TEXT)
            d.text((114, y + 24), ta(self.P.dist[i]), font=font(15), fill=MUTED)
            text_right(d, (508, y - 2), fmt(st["vv"][i]), font(30, "Bold"), TEXT)

        # top 5 districts bars
        top = st["top_d"][:5]
        mx = max([a for _, a in top] + [1e-6])
        for r, (dn, avg) in enumerate(top):
            y = 1140 + r * 52
            d.text((564, y - 6), ta(dn), font=font(23, "Bold"), fill=TEXT)
            text_right(d, (1012, y - 4), fmt(avg), font(24, "Bold"), TEXT)
            rrect(d, (564, y + 30, 1012, 37 + y), 4, fill=TRACK)
            col = hex2rgb(self.scale[classify(avg, self.scale)][2])
            rrect(d, (564, y + 30, 564 + max(8, 448 * avg / mx), 37 + y), 4, fill=col)

        # legend with counts
        n = len(self.scale)
        per_row = 5 if n > 7 else 4
        cwid = (1032 - 68 - 20) / per_row
        for i, (_, lab, c) in enumerate(self.scale):
            row, col_ = divmod(i, per_row)
            lx = 68 + col_ * cwid
            ly = 1464 + row * 32
            rrect(d, (lx, ly + 4, lx + 22, ly + 26), 4, fill=hex2rgb(c))
            d.text((lx + 30, ly), f"{lab} ({st['counts'][i]})", font=font(18), fill=TEXT)
        if n % per_row:
            row, col_ = divmod(n, per_row)
            d.text((68 + col_ * cwid, 1464 + row * 32), "அனைத்தும் மி.மீ.", font=font(17), fill=MUTED)
        return im, st

    def caption(self, st, sub):
        lines = ["🌧 மழைப்பொழிவு அறிக்கை", sub, "", self.headline(st), "", "முதல் 5 நிலையங்கள்:"]
        for r, i in enumerate(st["top_s"][:5], 1):
            lines.append(f"{r}. {self.P.names[i]} ({ta(self.P.dist[i])}) – {fmt(st['vv'][i])} மி.மீ.")
        lines += ["", "ஆதாரம்: TN-SMART", f"#{BRAND} · {SITE}"]
        return "\n".join(lines)

    def card2_sub(self, st, v):
        if self.mode == "daily":
            return f"மாநில சராசரி {fmt(st['state_avg'])} மி.மீ."
        return getattr(self, "_rainy_sub", "")


# ----------------------------------------------------------------- video
class Writer:
    def __init__(self, path):
        import imageio_ffmpeg
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.path = path
        self.p = subprocess.Popen(
            [self.ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", path],
            stdin=subprocess.PIPE)
        self.n = 0

    def add(self, im, repeat=1):
        b = im.convert("RGB").tobytes()
        for _ in range(repeat):
            self.p.stdin.write(b)
            self.n += 1

    def close(self):
        self.p.stdin.close()
        self.p.wait()

    def gif(self, gif_path, width=720, fps=12):
        vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
              f"[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
        subprocess.run([self.ff, "-y", "-loglevel", "error", "-i", self.path, "-vf", vf, "-loop", "0", gif_path],
                       check=True)


# ----------------------------------------------------------------- modes
def run_daily(day, geo, out_base):
    print(f"Downloading rain-day {day} …")
    days = tf.load_days(day, day)
    if day not in days:
        raise SystemExit(f"No TN-SMART data for {day}")
    P = Period(days, geo)
    v_final = np.nan_to_num(P.V[0])
    reported = ~np.isnan(P.V[0])
    prev = day - timedelta(days=1)
    sub = f"24 மணி நேர மழை · {date_ta(prev)} காலை 8:30 முதல் {date_ta(day)} காலை 8:30 வரை"
    R = Report("daily", P, geo, None, sub, SCALE_DAILY,
               (64.5, "≥ 64.5 மி.மீ. நிலையங்கள்", "கனமழை அளவு"),
               [200, 100, 64.5, 50, 20, 10])
    fin_st = R.stats(v_final, reported)
    wr = Writer(out_base + ".mp4")
    intro, grow, markers, hold = int(0.5 * FPS), int(4.5 * FPS), int(0.8 * FPS), int(3.0 * FPS)
    print("Rendering frames …")
    for i in range(intro + grow):
        t = ease(max(0, i - intro) / grow)
        im, _ = R.frame(v_final * t, reported, sub, final_stats=fin_st)
        wr.add(im)
    for i in range(markers):
        im, _ = R.frame(v_final, reported, sub, reveal_markers=(i + 1) / markers, final_stats=fin_st)
        wr.add(im)
    wr.add(im, hold)
    return wr, im, R.caption(fin_st, sub)


def run_accum(start, end, geo, out_base, live=False):
    last = end + timedelta(days=1) if live else end
    print(f"Downloading rain-days {start} … {last}")
    days = tf.load_days(start, last)
    if not days:
        raise SystemExit("No TN-SMART data in that range")
    P = Period(days, geo)
    nd = len(P.dates)
    reported = np.ones(len(P.meta), bool)
    span = f"{date_ta(P.dates[0])} முதல் {date_ta(P.dates[-1])} வரை"
    live_note = " (இன்றைய மழை இதுவரை உட்பட)" if live and P.dates[-1] > end else ""

    def sub_for(k):
        return f"பல நாள் மொத்த மழை · {date_ta(P.dates[0])} முதல் {date_ta(P.dates[k])} வரை ({k + 1} நாட்கள்){live_note}"

    R = Report("accum", P, geo, None, sub_for(nd - 1), SCALE_ACCUM,
               (100, "≥ 100 மி.மீ. பெற்ற நிலையங்கள்", "மொத்த மழை அளவு"),
               [500, 400, 300, 200, 150, 100, 50])
    fin_v = P.cum[-1]
    fin_st = R.stats(fin_v, reported)
    wr = Writer(out_base + ".mp4")
    per_day = int(max(4, min(15, round(12 * FPS / nd))))
    print(f"Rendering {nd} days × {per_day} frames …")
    prev = np.zeros(len(P.meta))
    im = None
    for k in range(nd):
        cur = P.cum[k]
        R._rainy_sub = f"சராசரி {np.mean(P.rainy_days[k]):.1f} மழை நாட்கள்"
        for j in range(per_day):
            t = ease((j + 1) / per_day)
            v = prev + (cur - prev) * t
            im, _ = R.frame(v, reported, sub_for(k), badge=date_ta(P.dates[k]),
                            progress=(k + t) / nd, final_stats=fin_st)
            wr.add(im)
        prev = cur
    markers = int(0.8 * FPS)
    for i in range(markers):
        im, _ = R.frame(fin_v, reported, sub_for(nd - 1), badge=date_ta(P.dates[-1]), progress=1.0,
                        reveal_markers=(i + 1) / markers, final_stats=fin_st)
        wr.add(im)
    wr.add(im, int(3.0 * FPS))
    return wr, im, R.caption(fin_st, sub_for(nd - 1))


def main():
    ap = argparse.ArgumentParser(description="MasRainman animated Tamil Nadu rainfall report")
    ap.add_argument("mode", choices=["daily", "accum"])
    ap.add_argument("--date", help="daily: rain-day (24 h ending 08:30 IST on this date). Default: latest complete")
    ap.add_argument("--start", help="accum: first rain-day, e.g. 2026-10-01 (default Oct 1 of this year)")
    ap.add_argument("--end", help="accum: last rain-day (default: latest complete day)")
    ap.add_argument("--live", action="store_true", help="accum: also add today's running (partial) day")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--gif-width", type=int, default=720)
    a = ap.parse_args()

    from PIL import features
    if not features.check("raqm"):
        print("WARNING: Pillow has no libraqm here, so Tamil letters may render wrongly. "
              "Run on GitHub Actions/Linux, or see README.")
    today = tf.today_ist()
    latest = today if tf.rain_day_is_complete(today) else today - timedelta(days=1)
    geo = Geo()
    os.makedirs(OUT_DIR, exist_ok=True)

    if a.mode == "daily":
        day = date.fromisoformat(a.date) if a.date else latest
        base = os.path.join(OUT_DIR, f"rain_daily_{day.isoformat()}")
        wr, last, cap = run_daily(day, geo, base)
    else:
        start = date.fromisoformat(a.start) if a.start else date(today.year, 10, 1)
        end = date.fromisoformat(a.end) if a.end else latest
        base = os.path.join(OUT_DIR, f"rain_accum_{start.isoformat()}_to_{end.isoformat()}")
        wr, last, cap = run_accum(start, end, geo, base, live=a.live)

    wr.close()
    last.save(base + ".png", optimize=True)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(cap)

    print(f"  video : {base}.mp4  ({wr.n / FPS:.1f} s)")
    print(f"  image : {base}.png")
    if not a.no_gif:
        wr.gif(base + ".gif", width=a.gif_width)
        print(f"  gif   : {base}.gif  ({os.path.getsize(base + '.gif') / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
