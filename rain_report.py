"""
MasRainman · animated rainfall report for Tamil Nadu (TN-SMART) and Kerala (IMD Thiruvananthapuram)

Two modes
  daily : one rain-day (24 h ending 08:30 IST). Map colours build up from zero.
  accum : total from a start date to the run day. Map fills day by day.
  live  : Tamil Nadu and Karnataka. Rain since 08:30 IST today, so far. Run every 2 hours: each run takes
          the latest TN-SMART file / KSNDMC 15-minute table and adds any new rain to the totals saved by
          earlier runs.

Regions / languages
  --region tn  (default)  Tamil Nadu, Tamil
  --region kl             Kerala, English and Malayalam (one video each); --lang en or --lang ml for one
  --region ka             Karnataka (KSNDMC), English; live mode only

Examples
  python rain_report.py daily --region kl
  python rain_report.py daily                         # latest complete day
  python rain_report.py daily --date 2026-10-18
  python rain_report.py accum --start 2026-10-01      # Oct 1 to today
  python rain_report.py accum --start 2026-10-01 --end 2026-10-18
  python rain_report.py accum --start 2026-10-01 --live   # also add today's running (partial) day
  python rain_report.py live                          # rain since 08:30 today, so far
  python rain_report.py accum --month 2026-10         # whole month (rain-days Oct 1 to Oct 31)

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
import tn_names
import kerala_data
import ksndmc_fetch
from lang import LANGS, date_str

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")
OUT_DIR = os.path.join(HERE, "out")

# ----------------------------------------------------------------- settings
BRAND = "MasRainman"
SITE = "masrainman.blogspot.com"
FPS = 30
W, H = 1080, 1620                     # 2:3 portrait, good for Facebook / WhatsApp
IDW_POWER = 2
IDW_RADIUS_KM = 44
IDW_K = 12

REGIONS = {
    "tn": {"name": "Tamil Nadu", "geo": "tn_districts.geojson", "canon": tn_names.canon, "langs": ["ta"],
           "radius": 44, "source": "TN-SMART (TNSDMA)", "pill": {"ta": "முழு தமிழ்நாடு"},
           "load": lambda a, b: tf.load_days(a, b)},
    "kl": {"name": "Kerala", "geo": "kl_districts.geojson", "canon": lambda d: kerala_data.district(d) or d,
           "langs": ["en", "ml"], "radius": 50, "source": "IMD Thiruvananthapuram, Kerala WRD, KSEB",
           "pill": {"en": "All Kerala", "ml": "കേരളം മുഴുവൻ"},
           "load": lambda a, b: kerala_data.load_days(a, b)},
    # KSNDMC has ~6,000 telemetric gauges, so a short IDW radius keeps the map local
    "ka": {"name": "Karnataka", "geo": "ka_districts.geojson", "canon": lambda d: d.strip().title(),
           "langs": ["en"], "radius": 22, "fill_km": 10, "area_avg": True,
           "source": "KSNDMC telemetric rain gauges", "pill": {"en": "All Karnataka"},
           "load": None, "live": ksndmc_fetch.day_stations},
}
REGION = REGIONS["tn"]
L = LANGS["ta"]


def set_lang(code):
    global L
    L = LANGS[code]
    _font_cache.clear()


def dname(d):
    return L["district"](d)


def dstr(d):
    return date_str(L, d)

# colour classes ---------------------------------------------------------
# (lower bound inclusive, label, colour)
SCALE_ACCUM = [
    (0.0, "ZERO", "#e4e7ee"),
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
    (0.0, "ZERO", "#e4e7ee"),
    (0.1, "0.1 – 2.4", "#bbf7d0"),
    (2.5, "2.5 – 15.5", "#4ade80"),
    (15.6, "15.6 – 64.4", "#facc15"),
    (64.5, "64.5 – 115.5", "#f59e0b"),
    (115.6, "115.6 – 204.4", "#ef4444"),
    (204.5, "≥ 204.5", "#9333ea"),
]

# themes: dark for the daily and multi-day reports, white for the 2-hourly "so far" report
THEMES = {
    "dark": dict(BG=(14, 21, 36), PANEL=(22, 31, 50), PANEL_EDGE=(36, 49, 74), TEXT=(232, 237, 245),
                 MUTED=(138, 151, 173), ACCENT=(91, 155, 213), ACCENT_SOFT=(120, 175, 230), TRACK=(40, 52, 76),
                 PILL=((25, 38, 62), (45, 70, 110)), BADGE=((10, 16, 28, 215), (60, 90, 140)),
                 WATERMARK=(255, 255, 255, 9), ZERO="#e4e7ee", INSET_MIX=0.45),
    "white": dict(BG=(241, 244, 249), PANEL=(255, 255, 255), PANEL_EDGE=(214, 221, 232), TEXT=(17, 24, 39),
                  MUTED=(90, 102, 122), ACCENT=(29, 99, 186), ACCENT_SOFT=(29, 99, 186), TRACK=(222, 228, 238),
                  PILL=((228, 238, 252), (176, 200, 235)), BADGE=((255, 255, 255, 235), (176, 200, 235)),
                  WATERMARK=(0, 0, 0, 7), ZERO="#e9ecf1", INSET_MIX=0.8),
}
LIVE_RED = (220, 38, 38)


def set_theme(name):
    globals().update(THEMES[name])


set_theme("dark")


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ----------------------------------------------------------------- fonts
_font_cache = {}


_WGHT = {"Regular": 400, "SemiBold": 600, "Bold": 700, "ExtraBold": 800}


def font(size, weight="Regular"):
    key = (size, weight)
    if key not in _font_cache:
        if L["font"] == "mukta":
            path = os.path.join(FONT_DIR, f"MuktaMalar-{weight}.ttf")
        else:
            path = os.path.join(FONT_DIR, "BalooChettan2.ttf")
        try:
            f = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
        except Exception:  # Pillow without libraqm: Indic vowel signs may misplace
            f = ImageFont.truetype(path, size)
        if L["font"] != "mukta":
            try:
                f.set_variation_by_axes([_WGHT.get(weight, 400)])
            except Exception:
                pass
        _font_cache[key] = f
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
    def __init__(self, region):
        self.canon = region["canon"]
        with open(os.path.join(HERE, "data", region["geo"]), encoding="utf-8") as f:
            gj = json.load(f)
        self.districts = {}
        for ft in gj["features"]:
            name = self.canon(ft["properties"]["district"])
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
        canon = geo.canon
        dates = sorted(days_dict)
        idx, meta = {}, []

        def kf(s):
            r3 = lambda x: None if x is None else round(x, 3)
            return (canon(s["district"]), s["station"], r3(s["lat"]), r3(s["lon"]), s.get("type"))
        for d in dates:
            for s in days_dict[d]:
                key = kf(s)
                if key not in idx:
                    idx[key] = len(meta)
                    meta.append({"district": key[0], "station": s["station"], "lat": s["lat"], "lon": s["lon"]})
        V = np.full((len(dates), len(meta)), np.nan)
        for i, d in enumerate(dates):
            for s in days_dict[d]:
                key = kf(s)
                if s["rain"] is not None:
                    V[i, idx[key]] = s["rain"]
        keep = ~np.all(np.isnan(V), axis=0)          # stations that reported at least once
        self.dates = dates
        self.meta = [m for m, k in zip(meta, keep) if k]
        self.V = V[:, keep]
        self.lat = np.array([np.nan if m["lat"] is None else m["lat"] for m in self.meta], float)
        self.lon = np.array([np.nan if m["lon"] is None else m["lon"] for m in self.meta], float)
        self.has_xy = ~np.isnan(self.lat)
        self.dist = np.array([m["district"] for m in self.meta])
        self.names = [m["station"] for m in self.meta]
        # fix district names that are not in the boundary file by point-in-polygon
        for i, dn in enumerate(self.dist):
            if dn not in geo.districts and self.has_xy[i]:
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
        self.sel = np.where(~np.isnan(lat))[0]           # stations that have coordinates
        lon, lat = lon[self.sel], lat[self.sel]
        self.n_fill = 0
        if REGION.get("fill_km"):
            # the feed lists only gauges with rain: put 0 mm points wherever no gauge is near, so a
            # lone wet gauge does not colour a whole dry area
            fk = REGION["fill_km"]
            g0 = geo.km(*[np.asarray(a) for a in (geo.lon0, geo.lat0)]).ravel()
            g1 = geo.km(*[np.asarray(a) for a in (geo.lon1, geo.lat1)]).ravel()
            gx, gy = np.meshgrid(np.arange(g0[0], g1[0], fk), np.arange(g0[1], g1[1], fk))
            cand = np.column_stack([gx.ravel(), gy.ravel()])
            dd, _ = cKDTree(geo.km(lon, lat)).query(cand, k=1)
            fill = cand[dd > fk]
            self.n_fill = len(fill)
            flon = fill[:, 0] / (111.32 * geo.kx)
            flat = fill[:, 1] / 110.57
            lon, lat = np.concatenate([lon, flon]), np.concatenate([lat, flat])
        tree = cKDTree(geo.km(lon, lat))
        k = min(IDW_K, len(lon))
        d, ix = tree.query(pts, k=k)
        if k == 1:
            d, ix = d[:, None], ix[:, None]
        d = np.maximum(d, 0.5)
        wgt = 1.0 / d ** IDW_POWER
        far = d > REGION["radius"]
        far[:, 0] = False                              # always keep the nearest station
        wgt[far] = 0
        self.ix = ix
        self.wgt = wgt / wgt.sum(axis=1, keepdims=True)

    def values(self, v):
        vs = v[self.sel]
        if self.n_fill:
            vs = np.concatenate([vs, np.zeros(self.n_fill)])
        return (self.wgt * vs[self.ix]).sum(axis=1)

    def district_pixels(self, geo, proj):
        """For each district, the positions of its pixels in values() order."""
        x0, y0, x1, y1 = self.box
        flat = np.flatnonzero(self.inside.ravel())
        pos = np.full(self.inside.size, -1)
        pos[flat] = np.arange(len(flat))
        out = {}
        for dn, g in geo.districts.items():
            m = Image.new("L", (self.w, self.h), 0)
            draw_geom(ImageDraw.Draw(m), g, proj, fill=255, off=(x0, y0))
            p = pos[np.asarray(m).ravel() > 0]
            out[dn] = p[p >= 0]
        return out

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

    def __init__(self, mode, period, geo, title_dates, sub, scale, card3, headline_thresholds, live_tag=None):
        self.mode, self.P, self.geo = mode, period, geo
        self.live_tag = live_tag
        self.lang_code = [k for k, v in LANGS.items() if v is L][0]
        self.scale = [(b, lab, ZERO if lab == "ZERO" else c) for b, lab, c in scale]
        self.sub = sub
        self.title_dates = title_dates
        self.card3 = card3                      # (threshold, label, sublabel)
        self.thr_list = headline_thresholds
        mx0, my0, mx1, my1 = self.MAP_BOX
        self.map_inner = (mx0 + 14, my0 + 14, mx1 - 14, my1 - 40)
        self.proj = Projector((geo.lon0, geo.lat0, geo.lon1, geo.lat1), geo.kx, self.map_inner, pad=6)
        self.field = Field(geo, self.proj, self.map_inner, period.lon, period.lat)
        self.dpix = self.field.district_pixels(geo, self.proj) if REGION.get("area_avg") else None
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
                dw.text((x + (y // 230) % 2 * 210, y), BRAND, font=f, fill=WATERMARK)
        wm = wm.rotate(28, resample=Image.BICUBIC, center=(W / 2, H / 2))
        im.paste(wm, (0, 0), wm)
        d = ImageDraw.Draw(im, "RGBA")

        d.text((48, 26), L["title"], font=font(56, "ExtraBold"), fill=TEXT)
        pill = REGION["pill"].get(self.lang_code, REGION["name"])
        pf = font(22, "SemiBold")
        pw = tw(d, pill, pf) + 40
        rrect(d, (W - 48 - pw, 44, W - 48, 90), 23, fill=PILL[0], outline=PILL[1])
        d.text((W - 48 - pw + 20, 50), pill, font=pf, fill=ACCENT_SOFT)
        if self.live_tag:                      # red "live" pill under the region pill
            lf = font(22, "Bold")
            lw = tw(d, self.live_tag, lf) + 62
            rrect(d, (W - 48 - lw, 100, W - 48, 140), 20, fill=LIVE_RED)
            d.ellipse((W - 48 - lw + 18, 112, W - 48 - lw + 34, 128), fill=(255, 255, 255))
            d.text((W - 48 - lw + 44, 104), self.live_tag, font=lf, fill=(255, 255, 255))

        # panels
        mx0, my0, mx1, my1 = self.MAP_BOX
        rrect(d, self.MAP_BOX, 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((mx0 + 18, my1 - 36), L["map_note"], font=font(17), fill=MUTED)
        ix0, ix1 = self.INSET_X
        for i in range(3):
            y0 = my0 + i * 230
            rrect(d, (ix0, y0, ix1, y0 + 214), 16, fill=PANEL, outline=PANEL_EDGE)
        rrect(d, (48, 1084, 528, 1404), 16, fill=PANEL, outline=PANEL_EDGE)
        rrect(d, (544, 1084, 1032, 1404), 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((68, 1098), L["top_st"], font=font(20, "SemiBold"), fill=MUTED)
        d.text((564, 1098), L["top_di"], font=font(20, "SemiBold"), fill=MUTED)
        rrect(d, (48, 1420, 1032, 1532), 16, fill=PANEL, outline=PANEL_EDGE)
        d.text((68, 1430), L["legend"], font=font(19, "SemiBold"), fill=MUTED)

        # footer
        d.line((48, 1548, 1032, 1548), fill=PANEL_EDGE, width=1)
        f17 = font(17)
        d.text((48, 1556), L["source"].format(src=REGION["source"], p=IDW_POWER, r=REGION["radius"]),
               font=f17, fill=MUTED)
        c = self.created
        made = L["made"].format(d=c.day, m=L["months"][c.month - 1], y=c.year, t=L["time"](c))
        text_right(d, (1032, 1556), made, f17, MUTED)
        d.text((48, 1580), L["brand"], font=font(22, "Bold"), fill=ACCENT_SOFT)
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
        extra = {}
        if self.dpix:
            # few gauges list a dry reading, so rank districts by the mean of the map over their area
            f = self.field.values(np.nan_to_num(vv))
            dist_avg = {dn: float(f[p].mean()) for dn, p in self.dpix.items() if len(p)}
            extra = {"area_wet": 100.0 * float(np.mean(f >= 0.5)),
                     "n_dist_wet": len(set(P.dist[np.nan_to_num(vv) > 0]))}
        else:
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
            "state_avg": (float(self.field.values(np.nan_to_num(vv)).mean()) if self.dpix else
                          float(np.nanmean(vv)) if reported.any() else 0.0),
            "top_d": top_d,
            "top_s": top_s,
            "counts": counts,
            "vv": vv,
            **extra,
        }

    def headline(self, st):
        vv = np.nan_to_num(st["vv"])
        if st["top_d"] and st["top_d"][0][1] >= 0.5:
            dn, avg = st["top_d"][0]
            for thr in self.thr_list:
                n = int(np.sum(vv > thr))
                if n:
                    key = "head_one" if n == 1 else "head_full"
                    key = key + "_area" if self.dpix and key + "_area" in L else key
                    return L[key].format(n=n, thr=f"{thr:g}", dist=dname(dn), avg=fmt(avg))
            key = "head_dist_area" if self.dpix and "head_dist_area" in L else "head_dist"
            return L[key].format(dist=dname(dn), avg=fmt(avg))
        return L["head_none"]

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
        tint = tuple(int(c * INSET_MIX + b * (1 - INSET_MIX)) for c, b in zip(col, PANEL))
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
            if not P.has_xy[i]:
                continue
            x, y = pr(P.lon[i], P.lat[i])
            r = (7 if len(idx) <= 80 else 4) * ss        # smaller dots for dense networks
            c = hex2rgb(self.scale[classify(vv[i], self.scale)][2])
            dl.ellipse((x * ss - r, y * ss - r, x * ss + r, y * ss + r), fill=c + (255,), outline=(15, 20, 32, 255),
                       width=ss)
        lay = lay.resize((vw, vh), Image.LANCZOS)
        im.paste(lay, (vx0, vy0), lay)
        if best is not None and vv[best] > 0 and P.has_xy[best]:
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
        d.text((rx + 40, cy0 + 9), ellipsize(d, dname(dn), font(26, "Bold"), cx1 - rx - 56), font=font(26, "Bold"),
               fill=TEXT)
        tx = cx0 + 240
        d.text((tx, cy0 + 56), L["ins_avg"], font=font(16), fill=MUTED)
        d.text((tx, cy0 + 70), f"{fmt(avg)}", font=font(38, "ExtraBold"), fill=TEXT)
        d.text((tx + tw(d, fmt(avg), font(38, "ExtraBold")) + 6, cy0 + 90), L["mm"], font=font(16), fill=MUTED)
        d.text((tx, cy0 + 122), L["ins_max"], font=font(16), fill=MUTED)
        if best is not None:
            d.text((tx, cy0 + 136), f"{fmt(vv[best])} {L['mm']}", font=font(24, "Bold"), fill=TEXT)
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
        if self.dpix:   # only wet gauges are listed, so show wet gauges and the wet share of the area
            first = [(L["c_gauge_rain"], f"{st['n_rain']}", L["c_rain_dists"].format(n=st["n_dist_wet"])),
                     (L["c_area"], f"{st['area_wet']:.0f}%", L["c_rain_daily"].format(x=fmt(st['state_avg'])))]
        else:
            first = [(L["c_rep"], f"{st['n_rep']}", L["c_rep_sub"].format(n=st['n_dist'])),
                     (L["c_rain"], f"{st['n_rain']}", self.card2_sub(st, v))]
        cards = first + [
            (L[self.card3[1]], f"{st['n_thr']}", L[self.card3[2]]),
            (L["c_max"],
             f"{fmt(st['vv'][best_i])} {L['mm']}" if best_i is not None else "—",
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
            rrect(d, (x0 + 6, y0 + 6, x0 + 6 + bw_, y0 + 58), 14, fill=BADGE[0], outline=BADGE[1])
            d.text((x0 + 24, y0 + 10), badge, font=bf, fill=TEXT)

        # numbered markers on the top 3 districts' max stations and top 5 stations
        if reveal_markers > 0:
            a = int(255 * ease(reveal_markers))
            for rank, i in enumerate(fs["top_s"][:5], 1):
                if not self.P.has_xy[i]:
                    continue
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
            d.text((114, y + 24), dname(self.P.dist[i]), font=font(15), fill=MUTED)
            text_right(d, (508, y - 2), fmt(st["vv"][i]), font(30, "Bold"), TEXT)

        # top 5 districts bars
        top = st["top_d"][:5]
        mx = max([a for _, a in top] + [1e-6])
        for r, (dn, avg) in enumerate(top):
            y = 1140 + r * 52
            d.text((564, y - 6), dname(dn), font=font(23, "Bold"), fill=TEXT)
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
            lab = L["zero"] if lab == "ZERO" else lab
            d.text((lx + 30, ly), f"{lab} ({st['counts'][i]})", font=font(18), fill=TEXT)
        if n % per_row:
            row, col_ = divmod(n, per_row)
            d.text((68 + col_ * cwid, 1464 + row * 32), L["legend_mm"], font=font(17), fill=MUTED)
        return im, st

    def caption(self, st, sub):
        lines = [L["cap_title"], sub, "", self.headline(st), "", L["cap_top"]]
        for r, i in enumerate(st["top_s"][:5], 1):
            lines.append(f"{r}. {self.P.names[i]} ({dname(self.P.dist[i])}) – {fmt(st['vv'][i])} {L['mm']}")
        lines += ["", L["cap_src"].format(src=REGION["source"]), f"#{BRAND} · {SITE}"]
        return "\n".join(lines)

    def card2_sub(self, st, v):
        if self.mode == "daily":
            return L["c_rain_daily"].format(x=fmt(st['state_avg']))
        return L["c_rain_accum"].format(x=f"{getattr(self, '_rainy_days', 0):.1f}")


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
def load(start, end):
    if REGION["load"] is None:
        raise SystemExit(f"{REGION['name']}: only live mode is available")
    days = REGION["load"](start, end)
    return {d: v for d, v in days.items() if any(s["rain"] is not None for s in v)}


def render_daily(day, P, geo, out_base):
    v_final = np.nan_to_num(P.V[0])
    reported = ~np.isnan(P.V[0])
    sub = L["daily_sub"].format(d0=dstr(day - timedelta(days=1)), d1=dstr(day))
    R = Report("daily", P, geo, None, sub, SCALE_DAILY, (64.5, "c_thr_daily", "c_thr_daily_sub"),
               [200, 100, 64.5, 50, 20, 10])
    fin_st = R.stats(v_final, reported)
    wr = Writer(out_base + ".mp4")
    intro, grow, markers, hold = int(0.5 * FPS), int(4.5 * FPS), int(0.8 * FPS), int(3.0 * FPS)
    for i in range(intro + grow):
        t = ease(max(0, i - intro) / grow)
        im, _ = R.frame(v_final * t, reported, sub, final_stats=fin_st)
        wr.add(im)
    for i in range(markers):
        im, _ = R.frame(v_final, reported, sub, reveal_markers=(i + 1) / markers, final_stats=fin_st)
        wr.add(im)
    wr.add(im, hold)
    return wr, im, R.caption(fin_st, sub)


def render_live(day, upto, P, geo, out_base):
    """Same picture as the daily report, for the running day up to `upto`."""
    sub = L["live_sub"].format(d0=dstr(day - timedelta(days=1)), d1=dstr(upto.date()), t=L["time"](upto))
    v_final = np.nan_to_num(P.V[0])
    reported = ~np.isnan(P.V[0])
    R = Report("daily", P, geo, None, sub, SCALE_DAILY, (64.5, "c_thr_daily", "c_thr_daily_sub"),
               [200, 100, 64.5, 50, 20, 10], live_tag=L["live_tag"])
    fin_st = R.stats(v_final, reported)
    badge = L["live_badge"].format(t=L["time"](upto))
    # bar under the headline: how much of the 24 hours (08:30 to 08:30) has gone by
    t0 = datetime(day.year, day.month, day.day, 8, 30, tzinfo=tf.IST) - timedelta(days=1)
    done = min(1.0, max(0.0, (upto - t0).total_seconds() / 86400))
    wr = Writer(out_base + ".mp4")
    intro, grow, markers, hold = int(0.5 * FPS), int(4.5 * FPS), int(0.8 * FPS), int(3.0 * FPS)
    for i in range(intro + grow):
        t = ease(max(0, i - intro) / grow)
        im, _ = R.frame(v_final * t, reported, sub, badge=badge, progress=done, final_stats=fin_st)
        wr.add(im)
    for i in range(markers):
        im, _ = R.frame(v_final, reported, sub, badge=badge, progress=done, reveal_markers=(i + 1) / markers,
                        final_stats=fin_st)
        wr.add(im)
    wr.add(im, hold)
    return wr, im, R.caption(fin_st, sub)


def render_accum(P, geo, out_base, live_note="", month=None):
    nd = len(P.dates)
    reported = np.ones(len(P.meta), bool)

    def sub_for(k):
        if month:
            return L["month_sub"].format(m=L["months_full"][month.month - 1], y=month.year,
                                         d0=dstr(P.dates[0]), d1=dstr(P.dates[k]))
        return L["accum_sub"].format(d0=dstr(P.dates[0]), d1=dstr(P.dates[k]), n=k + 1) + live_note

    R = Report("accum", P, geo, None, sub_for(nd - 1), SCALE_ACCUM, (100, "c_thr_accum", "c_thr_accum_sub"),
               [500, 400, 300, 200, 150, 100, 50])
    fin_v = P.cum[-1]
    fin_st = R.stats(fin_v, reported)
    wr = Writer(out_base + ".mp4")
    per_day = int(max(4, min(15, round(12 * FPS / nd))))
    prev = np.zeros(len(P.meta))
    im = None
    for k in range(nd):
        cur = P.cum[k]
        R._rainy_days = float(np.mean(P.rainy_days[k]))
        for j in range(per_day):
            t = ease((j + 1) / per_day)
            v = prev + (cur - prev) * t
            im, _ = R.frame(v, reported, sub_for(k), badge=dstr(P.dates[k]), progress=(k + t) / nd,
                            final_stats=fin_st)
            wr.add(im)
        prev = cur
    markers = int(0.8 * FPS)
    for i in range(markers):
        im, _ = R.frame(fin_v, reported, sub_for(nd - 1), badge=dstr(P.dates[-1]), progress=1.0,
                        reveal_markers=(i + 1) / markers, final_stats=fin_st)
        wr.add(im)
    wr.add(im, int(3.0 * FPS))
    return wr, im, R.caption(fin_st, sub_for(nd - 1))


def finish(wr, last, cap, base, gif=True, gif_width=720):
    wr.close()
    last.save(base + ".png", optimize=True)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(cap)
    print(f"  video : {base}.mp4  ({wr.n / FPS:.1f} s)")
    if gif:
        wr.gif(base + ".gif", width=gif_width)
        print(f"  gif   : {base}.gif  ({os.path.getsize(base + '.gif') / 1e6:.1f} MB)")


def main():
    global REGION
    ap = argparse.ArgumentParser(description="MasRainman animated rainfall report")
    ap.add_argument("mode", choices=["daily", "accum", "live"])
    ap.add_argument("--region", choices=list(REGIONS), default="tn", help="tn = Tamil Nadu, kl = Kerala, ka = Karnataka")
    ap.add_argument("--lang", help="ta, en or ml (default: the region's languages)")
    ap.add_argument("--date", help="daily: rain-day (24 h ending 08:30 IST on this date). Default: latest")
    ap.add_argument("--start", help="accum: first rain-day, e.g. 2026-10-01 (default Oct 1 of this year)")
    ap.add_argument("--end", help="accum: last rain-day (default: latest)")
    ap.add_argument("--month", help="accum: a whole month, YYYY-MM, or 'last' for last month")
    ap.add_argument("--live", action="store_true", help="accum, Tamil Nadu: also add today's running day")
    ap.add_argument("--always", action="store_true", help="live: render even if nothing changed since the last run")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--gif-width", type=int, default=720)
    a = ap.parse_args()

    from PIL import features
    if not features.check("raqm"):
        print("WARNING: Pillow has no libraqm here, so Tamil/Malayalam letters may render wrongly. "
              "Run on GitHub Actions/Linux, or see README.")
    REGION = REGIONS[a.region]
    langs = [a.lang] if a.lang else REGION["langs"]
    today = tf.today_ist()
    latest = today if tf.rain_day_is_complete(today) else today - timedelta(days=1)
    geo = Geo(REGION)
    os.makedirs(OUT_DIR, exist_ok=True)
    tag = "" if a.region == "tn" else f"{a.region}_"

    if a.mode == "live":
        if a.region == "kl":
            raise SystemExit("live mode is for Tamil Nadu (TN-SMART) and Karnataka (KSNDMC)")
        now = datetime.now(tf.IST)
        day = tf.running_day(now)
        if a.region == "ka":
            # KSNDMC's table is updated every 15 minutes
            upto = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15 + 15)
        else:
            # label with the last half past the hour, when TN-SMART's hourly values come in
            upto = now.replace(second=0, microsecond=0) - timedelta(minutes=(now.minute - 30) % 60)
        print(f"{REGION['name']}: rain-day {day} so far (to {upto:%Y-%m-%d %H:%M})")
        set_theme("white")
        stations, changed = tf.live_totals(day, fetch=REGION.get("live"), tag=tag)
        if not any(s["rain"] for s in stations):
            print("No station has rain yet; nothing to render.")
            return
        if not changed and not a.always:
            print("No new rain since the last check; nothing to render.")
            return
        P = Period({day: stations}, geo)
        for lc in langs:
            set_lang(lc)
            base = os.path.join(OUT_DIR, f"rain_{tag}live_{upto:%Y-%m-%d_%H%M}")
            print(f"Rendering ({lc}) …")
            finish(*render_live(day, upto, P, geo, base), base, not a.no_gif, a.gif_width)
    elif a.mode == "daily":
        day = date.fromisoformat(a.date) if a.date else latest
        print(f"{REGION['name']}: rain-day {day}")
        days = load(day, day)
        if not days and not a.date and a.region == "kl":       # IMD file not out yet: use previous day
            day -= timedelta(days=1)
            days = load(day, day)
        if day not in days:
            raise SystemExit(f"No {REGION['source']} data for {day}")
        P = Period(days, geo)
        for lc in langs:
            set_lang(lc)
            base = os.path.join(OUT_DIR, f"rain_{tag}daily_{day.isoformat()}" + (f"_{lc}" if len(REGION['langs']) > 1 else ""))
            print(f"Rendering ({lc}) …")
            finish(*render_daily(day, P, geo, base), base, not a.no_gif, a.gif_width)
    elif a.month:
        if a.month == "last":
            m0 = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        else:
            m0 = date.fromisoformat(a.month + "-01")
        m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if m1 > latest:
            raise SystemExit(f"{m0:%B %Y} is not over yet (last complete rain-day {latest})")
        print(f"{REGION['name']}: month {m0:%Y-%m}, rain-days {m0} … {m1}")
        days = load(m0, m1)
        need = 0.8 * ((m1 - m0).days + 1)
        if len(days) < need:
            raise SystemExit(f"Only {len(days)} days with data in {m0:%B %Y}; not making a month report")
        P = Period(days, geo)
        for lc in langs:
            set_lang(lc)
            base = os.path.join(OUT_DIR, f"rain_{tag}month_{m0:%Y-%m}" + (f"_{lc}" if len(REGION['langs']) > 1 else ""))
            print(f"Rendering month {m0:%Y-%m} ({lc}) …")
            finish(*render_accum(P, geo, base, month=m0), base, not a.no_gif, a.gif_width)
    else:
        start = date.fromisoformat(a.start) if a.start else date(today.year, 10, 1)
        end = date.fromisoformat(a.end) if a.end else latest
        last = end + timedelta(days=1) if (a.live and a.region == "tn") else end
        print(f"{REGION['name']}: rain-days {start} … {last}")
        days = load(start, last)
        if not days:
            raise SystemExit(f"No {REGION['source']} data in that range")
        P = Period(days, geo)
        for lc in langs:
            set_lang(lc)
            note = L["live_note"] if last > end and P.dates[-1] > end else ""
            base = os.path.join(OUT_DIR, f"rain_{tag}accum_{P.dates[0].isoformat()}_to_{P.dates[-1].isoformat()}"
                                + (f"_{lc}" if len(REGION['langs']) > 1 else ""))
            print(f"Rendering {len(P.dates)} days ({lc}) …")
            finish(*render_accum(P, geo, base, note), base, not a.no_gif, a.gif_width)


if __name__ == "__main__":
    main()
