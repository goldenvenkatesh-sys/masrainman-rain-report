"""
MasRainman · "Past 24 Hours Observed Rainfall": an animated, hour-by-hour video (1080x1350, 36.5 s).

The rain-gauge readings of one rain-day (08:30 to 08:30 IST) are added up hour by hour on a
hillshaded terrain map. The camera follows the gauge with the heaviest rain in each hour, then
pulls back to the whole state. Then the day's five wettest stations, a 24-hour "rain clock" of when the
rain fell, and "Did the models get it right?": the heavy-rain districts each model's forecast caught (models.py).

  python rain_report.py live24                      # Tamil Nadu, latest complete rain-day
  python rain_report.py live24 --date 2026-10-26

Adding a state: give it an entry in REGIONS24 below with
  native   state name in its own script, and native_font, a font in fonts/ that has that script
  hourly   function(rain-day) -> (stations, hours): stations as in tnsmart_fetch.station_totals()
           (district, station, lat, lon, rain), hours a 24 x stations list of mm per hour, 08:30-09:30 first
  peaks    hills to label on the zoomed map: (name, lat, lon)
and the region's boundary/IDW settings come from rain_report.REGIONS as for the other reports.
"""
import math
import os
from datetime import datetime, timedelta

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter1d, binary_dilation
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely.ops import unary_union

import models
import rain_report as rr
import terrain
import tnsmart_fetch as tf

REGIONS24 = {
    "tn": {"native": "தமிழ்நாடு", "native_font": "MuktaMalar-ExtraBold.ttf", "hourly": tf.hourly_stations,
           "source": "TN-SMART (TNSDMA)", "network": "Hourly gauge", "dem_zoom": 9,
           "peaks": [("Doddabetta", 11.40, 76.74), ("Anamalai", 10.35, 77.05), ("Kodaikanal", 10.24, 77.49),
                     ("Shevaroy", 11.80, 78.22), ("Kolli Hills", 11.27, 78.36), ("Javadhu", 12.57, 78.83),
                     ("Megamalai", 9.66, 77.39), ("Pothigai", 8.60, 77.25)]},
}

# ----------------------------------------------------------------- look
W, H = 1080, 1350
FPS = 30
BG = (11, 14, 20)
CARD = (19, 23, 31)
CARD_EDGE = (37, 43, 55)
TEXT = (236, 239, 244)
SOFT = (198, 204, 214)
MUTED = (138, 146, 160)
GOLD = (232, 163, 61)
DIM_BAR = (48, 53, 62)
MAP = (340, 216, 1059, 1160)              # map window
VW, VH = MAP[2] - MAP[0], MAP[3] - MAP[1]
ZW = 3.0                                   # world raster = 3x the full-state view
ZF = 2.6                                   # zoom while following the heaviest hourly rain
FDS = 2                                    # rain field is computed at 1/2 the world raster
SMOOTH_KM = 4                              # blur of the rain field

# colour ramp for cumulative mm (log-ish), and the colour bar's tick positions
STOPS = [(0.1, "#24406a"), (1, "#2c4a72"), (2.5, "#2f6f96"), (5, "#2a8f9c"), (10, "#2fa68f"),
         (15.6, "#69bb72"), (25, "#b5cc55"), (40, "#e3c94a"), (64.5, "#f0a23c"), (100, "#e0503e"),
         (150, "#c73a7c"), (204.5, "#b45cb8"), (300, "#efd9f5")]
TICKS = [(1, 125), (2.5, 210), (5, 300), (10, 410), (25, 577), (50, 712), (100, 851), (200, 992)]
CATS = [(0.1, "LIGHT", "0.1 – 15.5 mm"), (15.6, "MODERATE", "15.6 – 64.4 mm"), (64.5, "HEAVY", "64.5 – 115.5 mm"),
        (115.6, "VERY HEAVY", "115.6 – 204.4 mm"), (204.5, "EXTREMELY HEAVY", "≥ 204.5 mm")]
LUT_N = 1024
_L0, _L1 = math.log10(0.1), math.log10(300)


def _ramp():
    xs = np.array([math.log10(v) for v, _ in STOPS])
    cs = np.array([rr.hex2rgb(c) for _, c in STOPS], float)
    t = np.linspace(_L0, _L1, LUT_N)
    return np.stack([np.interp(t, xs, cs[:, i]) for i in range(3)], axis=1).astype(np.float32)


LUT = _ramp()


def colour(mm):
    i = int((math.log10(min(max(mm, 0.1), 300)) - _L0) / (_L1 - _L0) * (LUT_N - 1))
    return tuple(int(c) for c in LUT[i])


def category(mm):
    c = None
    for lo, name, _ in CATS:
        if mm >= lo:
            c = name
    return c or "NO RAIN"


# ----------------------------------------------------------------- fonts / text
_fonts = {}


def font(size, weight="Regular", file=None):
    key = (size, weight, file)
    if key not in _fonts:
        path = os.path.join(rr.FONT_DIR, file or f"MuktaMalar-{weight}.ttf")
        try:
            _fonts[key] = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


def spaced(s):
    """Small capitals labels read better with a little letter spacing."""
    return " ".join(s)


def features_ok():
    from PIL import features
    return features.check("raqm")


FEAT = ["lnum"] if features_ok() else None      # lining figures: Mukta's default digits are old-style


def text(d, xy, s, f, fill, anchor="ls"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, features=FEAT)


def fit(d, s, f, width):
    return rr.ellipsize(d, s, f, width)


def clock(t):
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def mm1(v):
    return f"{v:,.1f}"


# ----------------------------------------------------------------- data
class Day:
    """One rain-day of hourly gauge readings."""

    def __init__(self, day, stations, hours, geo):
        self.day = day
        self.t0 = datetime(day.year, day.month, day.day, 8, 30, tzinfo=tf.IST) - timedelta(days=1)
        self.H = np.asarray(hours, float).reshape(24, len(stations))          # mm in each hour
        self.cum = np.vstack([np.zeros(len(stations)), np.cumsum(self.H, axis=0)])   # 25 x N, at hour 0..24
        self.names = [s["station"] for s in stations]
        self.lat = np.array([s["lat"] for s in stations], float)
        self.lon = np.array([s["lon"] for s in stations], float)
        self.dist = [geo.canon(s["district"]) for s in stations]
        self.total = self.cum[-1]
        self.n = len(stations)
        self.state_hourly = self.H.mean(axis=1) if self.n else np.zeros(24)
        self.peak_hourly = self.H.max(axis=1) if self.n else np.zeros(24)

    def at(self, tau):
        """Station totals at tau hours after 08:30 (rain in the running hour spread evenly)."""
        h0 = min(int(tau), 23)
        return self.cum[h0] + (tau - h0) * self.H[h0]

    def district_max(self):
        out = {}
        for d, v in zip(self.dist, self.total):
            out[d] = max(out.get(d, 0.0), float(v))
        return out

    def peak_window(self, share=0.5):
        """Shortest run of hours holding `share` of the state's rain: (first hour, last hour) or None."""
        m = self.state_hourly
        tot = m.sum()
        if tot <= 0:
            return None
        best = None
        for i in range(24):
            s = 0.0
            for j in range(i, 24):
                s += m[j]
                if s >= share * tot:
                    if best is None or (j - i, -s) < (best[1] - best[0], -best[2]):
                        best = (i, j, s)
                    break
        return best[:2]


# ----------------------------------------------------------------- the video
class Live24:
    def __init__(self, region_key, D, geo, forecasts=None):
        self.R = REGIONS24[region_key]
        self.region = rr.REGIONS[region_key]
        self.D, self.geo = D, geo
        self.fc = forecasts or {}
        self.window = D.peak_window()
        # the state without slivers between districts and without holes
        st = geo.state.buffer(0.003).buffer(-0.003)
        self.state = unary_union([Polygon(p.exterior) for p in rr.polys(st)])
        WW, WH = int(VW * ZW), int(VH * ZW)
        self.WW, self.WH = WW, WH
        self.proj = rr.Projector((geo.lon0, geo.lat0, geo.lon1, geo.lat1), geo.kx, (0, 0, WW, WH), pad=int(14 * ZW))
        print("  terrain …")
        self._world()
        print("  rain field …")
        self._field()
        self._camera()
        self.base = self._static()
        self.sx, self.sy = self.proj(D.lon, D.lat)
        self._card_side = 0

    # ---- rasters --------------------------------------------------------
    def _world(self):
        WW, WH, P = self.WW, self.WH, self.proj
        m = Image.new("L", (WW, WH), 0)
        rr.draw_geom(ImageDraw.Draw(m), self.state, P, fill=255)
        self.mask = cv2.GaussianBlur(np.asarray(m), (3, 3), 0)
        lon, lat = P.inverse_grid(0, 0, WW, WH)
        dem = terrain.elevation(lon, lat, z=self.R.get("dem_zoom", 9))
        cell = 111320 / P.s
        self.shade = terrain.hillshade(dem, (cell, cell))
        # outline rings in world pixels, simplified a little
        self.rings = []
        for p in rr.polys(self.state.simplify(0.003)):
            xy = np.array([P(a, b) for a, b in p.exterior.coords])
            if len(xy) > 3:
                self.rings.append(xy)

    def _field(self):
        D, P, geo = self.D, self.proj, self.geo
        fw, fh = self.WW // FDS, self.WH // FDS
        inside = cv2.resize(self.mask, (fw, fh), interpolation=cv2.INTER_AREA) > 0
        inside = binary_dilation(inside, iterations=4)
        xs, ys = np.nonzero(inside.T)
        lon = P.lon0 + (xs * FDS + FDS / 2 - P.ox) / (P.s * P.kx)
        lat = P.lat1 - (ys * FDS + FDS / 2 - P.oy) / P.s
        tree = cKDTree(geo.km(D.lon, D.lat))
        k = min(rr.IDW_K, D.n)
        d, ix = tree.query(geo.km(lon, lat), k=k)
        if k == 1:
            d, ix = d[:, None], ix[:, None]
        d = np.maximum(d, 0.5)
        w = 1.0 / d ** rr.IDW_POWER
        far = d > self.region["radius"]
        far[:, 0] = False
        w[far] = 0
        w /= w.sum(axis=1, keepdims=True)
        self.F = np.zeros((25, fh, fw), np.float32)
        # IDW peaks and dips sharply at each gauge; a light blur (normalised over the land) smooths that
        sig = SMOOTH_KM * 1000 / (111320 / P.s * FDS)
        m = inside.astype(np.float32)
        den = cv2.GaussianBlur(m, (0, 0), sig) + 1e-6
        for h in range(25):
            self.F[h][ys, xs] = (w * D.cum[h][ix]).sum(axis=1)
            self.F[h] = cv2.GaussianBlur(self.F[h] * m, (0, 0), sig) / den * m

    # ---- timeline and camera -----------------------------------------------
    N = 1095
    F_START, F_END = 30, 555             # the 24 hours play between these frames
    ZOOM_IN, ZOOM_OUT = (30, 62), (440, 482)
    F_TOP = 585                          # toppers card from here
    F_CLOCK = 675                        # rain clock from here
    F_SCORE = 885                        # model scorecard from here

    def tau(self, f):
        return float(np.clip((f - self.F_START) / (self.F_END - self.F_START) * 24, 0, 24))

    def hour(self, tau):
        return min(24, math.ceil(tau - 1e-9))

    def zoom(self, f):
        a, b = self.ZOOM_IN
        c, e = self.ZOOM_OUT
        if f < a or f >= e:
            return 1.0
        if f < b:
            return 1 + (ZF - 1) * rr.ease((f - a) / (b - a))
        if f < c:
            return ZF
        return ZF - (ZF - 1) * rr.ease((f - c) / (e - c))

    def _camera(self):
        D = self.D
        sx, sy = self.proj(D.lon, D.lat)
        tgt = [None] * 25
        for k in range(1, 25):
            if D.n and D.H[k - 1].max() >= 1:
                i = int(np.argmax(D.H[k - 1]))
                tgt[k] = (sx[i], sy[i])
        first = next((t for t in tgt if t), (self.WW / 2, self.WH / 2))
        prev = first
        for k in range(25):
            prev = tgt[k] = tgt[k] or prev
        path = np.array([tgt[max(1, self.hour(self.tau(f)))] for f in range(self.N)], float)
        self.path = gaussian_filter1d(path, 12, axis=0, mode="nearest")

    def view(self, f):
        z = self.zoom(f)
        t = (z - 1) / (ZF - 1)
        c = np.array([self.WW / 2, self.WH / 2]) * (1 - t) + self.path[f] * t
        sc = ZW / z
        hx, hy = VW / 2 * sc, VH / 2 * sc
        cx = float(np.clip(c[0], hx, self.WW - hx))
        cy = float(np.clip(c[1], hy, self.WH - hy))
        return cx, cy, z

    # ---- static layer ----------------------------------------------------------
    def _static(self):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im, "RGBA")
        D = self.D
        text(d, (43, 48), spaced("BY  MASRAINMAN"), font(15, "Bold"), GOLD)
        text(d, (43, 104), self.R["native"], font(60, file=self.R["native_font"]), TEXT)
        f25 = font(26)
        s0 = self.region["name"].upper()
        text(d, (43, 140), s0, f25, SOFT)
        text(d, (43 + d.textlength(s0, font=f25), 140), "  ·  Past 24 Hours Observed Rainfall", f25, SOFT)
        text(d, (43, 172), f"Ending {D.day.day} {D.day:%b %Y}, 8:30 AM IST  ·  Hourly accumulation of "
                           f"rain-gauge observations", font(16), MUTED)
        d.rectangle((43, 195, 107, 197), fill=GOLD)
        d.line((107, 196, 1037, 196), fill=(30, 35, 45), width=1)
        for box in [(38, 216, 323, 415), (38, 440, 323, 590), (38, 614, 323, 742), (38, 766, 323, 1063),
                    (38, 1088, 323, 1150)]:
            rr.rrect(d, box, 10, fill=CARD, outline=CARD_EDGE)
        f12 = font(12, "Bold")
        for y, s in [(247, "TIME  ·  IST"), (469, "PEAK THIS HOUR"), (643, "HIGHEST HOURLY SO FAR"),
                     (797, "LEADERS SO FAR  ·  mm"), (1111, "STATIONS WITH RAIN")]:
            text(d, (180, y), spaced(s), f12, MUTED, "ms")
        text(d, (180, 362), spaced("HOURLY RAIN INTENSITY"), font(10, "Bold"), MUTED, "ms")
        # colour bar and categories
        text(d, (43, 1202), spaced("CUMULATIVE RAINFALL  ·  mm"), f12, MUTED)
        mx = np.array([p for _, p in TICKS], float)
        lv = np.log10([v for v, _ in TICKS])
        for x in range(43, 1037):
            j = min(max(np.searchsorted(mx, x) - 1, 0), len(mx) - 2)       # extend the end segments
            v = 10 ** (lv[j] + (x - mx[j]) / (mx[j + 1] - mx[j]) * (lv[j + 1] - lv[j]))
            d.line((x, 1218, x, 1230), fill=colour(v))
        for v, x in TICKS:
            text(d, (x, 1253), "200+" if v == 200 else f"{v:g}", font(13), SOFT, "ms")
        cw = (1037 - 43) / 5
        for i, (lo, name, rng) in enumerate(CATS):
            x0 = 43 + i * cw
            mid = [8, 35, 85, 160, 260][i]
            d.rectangle((x0, 1262, x0 + cw - 12, 1264), fill=colour(mid))
            text(d, (x0, 1285), spaced(name), font(12, "Bold"), TEXT)
            text(d, (x0, 1303), rng, font(11), MUTED)
        text(d, (540, 1334), f"Data: {self.R['source']}  ·  Terrain: SRTM via AWS Terrain Tiles  ·  "
                             f"{rr.BRAND} · {rr.SITE}", font(12), MUTED, "ms")
        return im

    # ---- map --------------------------------------------------------------------
    def to_view(self, wx, wy, cx, cy, z):
        sc = ZW / z
        return (np.asarray(wx) - cx) / sc + VW / 2, (np.asarray(wy) - cy) / sc + VH / 2

    def map_image(self, f, tau):
        cx, cy, z = self.view(f)
        sc = ZW / z
        M = np.float32([[sc, 0, cx - VW / 2 * sc], [0, sc, cy - VH / 2 * sc]])
        flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
        shade = cv2.warpAffine(self.shade, M, (VW, VH), flags=flags, borderValue=0)
        mask = cv2.warpAffine(self.mask, M, (VW, VH), flags=flags, borderValue=0).astype(np.float32) / 255
        h0 = min(int(tau), 23)
        Ft = self.F[h0] + (tau - h0) * (self.F[h0 + 1] - self.F[h0])
        field = cv2.warpAffine(Ft, M / FDS, (VW, VH), flags=flags, borderValue=0)
        idx = ((np.log10(np.clip(field, 0.1, 300)) - _L0) / (_L1 - _L0) * (LUT_N - 1)).astype(np.int32)
        rain = LUT[idx] * (0.45 + 0.8 * shade)[..., None]
        a = np.clip((field - 0.1) / 0.9, 0, 1)
        a = (a * a * (3 - 2 * a))[..., None]
        land = (14 + 130 * shade ** 1.3)[..., None] * np.array([0.92, 0.97, 1.06], np.float32)
        out = land * (1 - a) + rain * a
        m = mask[..., None]
        out = np.array(BG, np.float32) * (1 - m) + out * m
        im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
        d = ImageDraw.Draw(im, "RGBA")
        for ring in self.rings:
            x, y = self.to_view(ring[:, 0], ring[:, 1], cx, cy, z)
            d.line(list(zip(x, y)), fill=(222, 230, 242, 170), width=1)
        return im, (cx, cy, z)

    def peaks(self, d, cam, alpha):
        if alpha <= 0:
            return
        cx, cy, z = cam
        f = font(14, "Bold")
        for name, lat, lon in self.R.get("peaks", []):
            wx, wy = self.proj(lon, lat)
            x, y = self.to_view(wx, wy, cx, cy, z)
            if not (20 < x < VW - 120 and 40 < y < VH - 20):
                continue
            x, y = x + MAP[0], y + MAP[1]
            a = int(220 * alpha)
            d.polygon([(x - 6, y + 4), (x + 6, y + 4), (x, y - 7)], fill=(205, 210, 220, a),
                      outline=(20, 24, 30, a))
            d.text((x + 11, y + 6), name, font=f, fill=(10, 12, 16, int(160 * alpha)), anchor="ls")
            d.text((x + 10, y + 5), name, font=f, fill=(215, 220, 230, a), anchor="ls")

    def inset(self, im, cam, alpha):
        if alpha <= 0:
            return
        cx, cy, z = cam
        iw = 108
        k = iw / self.WW
        ih = int(self.WH * k)
        x0, y0 = MAP[0] + 12, MAP[3] - ih - 12
        lay = Image.new("RGBA", (iw, ih), (24, 34, 40, int(200 * alpha)))
        d = ImageDraw.Draw(lay)
        for ring in self.rings:
            d.line([(x * k, y * k) for x, y in ring], fill=(225, 232, 242, int(230 * alpha)), width=1)
        sc = ZW / z
        r = ((cx - VW / 2 * sc) * k, (cy - VH / 2 * sc) * k, (cx + VW / 2 * sc) * k, (cy + VH / 2 * sc) * k)
        d.rectangle(r, outline=GOLD + (int(255 * alpha),), width=2)
        im.paste(lay, (x0, y0), lay)

    def popup(self, im, cam, k, alpha):
        """Card for the gauge with the most rain in hour k, pinned to it on the map."""
        D = self.D
        if alpha <= 0 or k < 1 or not D.n:
            return
        hr = D.H[k - 1]
        order = np.argsort(-hr)
        i = int(order[0])
        if hr[i] < 0.5:
            return
        cx, cy, z = cam
        x, y = self.to_view(self.sx[i], self.sy[i], cx, cy, z)
        x, y = float(x), float(y)
        cw, ch = 236, 212
        col = colour(max(hr[i], 8))
        opts = [(x - 46 - cw, y - 70 - ch), (x + 46, y - 70 - ch), (x - 46 - cw, y + 70), (x + 46, y + 70)]
        ok = [10 <= ox and ox + cw <= VW - 10 and 50 <= oy and oy + ch <= VH - 10 for ox, oy in opts]
        if not ok[self._card_side]:
            self._card_side = ok.index(True) if any(ok) else 0
        ox, oy = opts[self._card_side]
        ox = min(max(ox, 10), VW - 10 - cw)
        oy = min(max(oy, 50), VH - 10 - ch)
        lay = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        # leader line and ring at the gauge
        ax = ox + cw if ox + cw < x else ox if ox > x else x
        ay = oy + ch if oy + ch < y else oy if oy > y else y
        d.line((ax, ay, x, y), fill=col + (200,), width=2)
        d.ellipse((x - 15, y - 15, x + 15, y + 15), outline=col + (230,), width=2)
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=col + (255,))
        rr.rrect(d, (ox, oy, ox + cw, oy + ch), 8, fill=(13, 17, 24, 238), outline=col + (255,), width=2)
        lx = ox + 10
        text(d, (lx, oy + 17), spaced("PEAK RAINFALL THIS HOUR"), font(10, "Bold"), col)
        text(d, (lx, oy + 43), fit(d, D.names[i], font(22, "Bold"), cw - 20), font(22, "Bold"), TEXT)
        text(d, (lx, oy + 61), fit(d, D.dist[i].upper(), font(12, "Bold"), cw - 20), font(12, "Bold"), SOFT)
        big = f"{hr[i]:.0f}" if hr[i] >= 10 else f"{hr[i]:.1f}"
        fb = font(40, "Bold")
        text(d, (lx, oy + 101), big, fb, col)
        text(d, (lx + d.textlength(big, font=fb) + 10, oy + 99), "mm / hr", font(15, "SemiBold"), SOFT)
        text(d, (lx, oy + 120), spaced("THIS GAUGE  ·  HOUR BY HOUR"), font(9, "Bold"), MUTED)
        bx0, bx1, by = lx, ox + cw - 10, oy + 160
        bw = (bx1 - bx0) / 24
        mxv = max(D.H[:, i].max(), 1)
        for h in range(24):
            v = D.H[h, i]
            if h + 1 > k:
                d.rectangle((bx0 + h * bw + 1, by - 2, bx0 + (h + 1) * bw - 2, by), fill=(52, 57, 66, 255))
                continue
            hh = max(2, 34 * v / mxv)
            fill = col + (255,) if h + 1 == k else (110, 117, 130, 255)
            d.rectangle((bx0 + h * bw + 1, by - hh, bx0 + (h + 1) * bw - 2, by), fill=fill)
        text(d, (lx, oy + 179), f"{D.cum[k][i]:.0f} mm so far", font(13, "Bold"), TEXT)
        f11 = font(12)
        for r, j in enumerate(order[1:3], 2):
            if hr[j] <= 0:
                break
            text(d, (lx, oy + 179 + 15 * (r - 1)), f"{r}   {fit(d, D.names[j], f11, 140)}   {hr[j]:.0f} mm",
                 f11, MUTED)
        if alpha < 1:
            lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha)))
        im.paste(lay, (MAP[0], MAP[1]), lay)

    # ---- one frame -------------------------------------------------------------
    def frame(self, f):
        D = self.D
        tau = self.tau(f)
        k = self.hour(tau)
        im = self.base.copy()
        mim, cam = self.map_image(f, tau)
        im.paste(mim, (MAP[0], MAP[1]))
        d = ImageDraw.Draw(im, "RGBA")
        z = cam[2]
        zt = (z - 1) / (ZF - 1)
        self.peaks(d, cam, max(0.0, (zt - 0.6) / 0.4))
        if zt > 0.05:
            pk = D.H[k - 1].max() if k >= 1 and D.n else 0
            col = colour(max(pk, 8))
            a = int(255 * min(1, zt * 1.5))
            s = spaced("FOCUS  ·  HEAVIEST HOURLY RAIN")
            fb = font(14, "Bold")
            bw = d.textlength(s, font=fb) + 18
            rr.rrect(d, (MAP[0] + 15, MAP[1] + 17, MAP[0] + 15 + bw, MAP[1] + 45), 5, fill=(13, 17, 24, int(a * 0.85)),
                     outline=col + (a,), width=2)
            text(d, (MAP[0] + 24, MAP[1] + 37), s, fb, col + (a,))
        self.inset(im, cam, min(1, zt * 1.5))
        if zt > 0.85 and k >= 1:
            into = (tau - (k - 1)) * (self.F_END - self.F_START) / 24       # frames into this hour
            self.popup(im, cam, k, min(1.0, into / 5) * min(1.0, (zt - 0.85) / 0.15))
        d = ImageDraw.Draw(im, "RGBA")
        self.panels(d, tau, k)
        return im

    def panels(self, d, tau, k):
        D = self.D
        t = D.t0 + timedelta(minutes=15 * math.floor(tau * 4 + 1e-6))
        text(d, (180, 305), clock(t), font(50, "Bold"), TEXT, "ms")
        text(d, (180, 340), f"{t:%a %d %b}".upper() + f"  ·  HOUR {k:02d} / 24", font(16, "Bold"), SOFT, "ms")
        mx = max(D.state_hourly.max(), 1e-6)
        bw = (305 - 56) / 24
        for h in range(24):
            hh = max(2, 34 * D.state_hourly[h] / mx)
            d.rectangle((56 + h * bw + 1, 404 - hh, 56 + (h + 1) * bw - 1, 404), fill=GOLD if h < k else DIM_BAR)
        # peak this hour
        if k >= 1 and D.n and D.H[k - 1].max() > 0:
            i = int(np.argmax(D.H[k - 1]))
            text(d, (180, 523), f"{mm1(D.H[k - 1][i])} mm", font(40, "Bold"), GOLD, "ms")
            text(d, (180, 555), fit(d, D.names[i], font(18, "Bold"), 265), font(18, "Bold"), TEXT, "ms")
            text(d, (180, 576), D.dist[i], font(14), MUTED, "ms")
        else:
            text(d, (180, 523), "–", font(40, "Bold"), GOLD, "ms")
            text(d, (180, 560), "waiting for data" if k < 1 else "no rain this hour", font(16, "Bold"), SOFT, "ms")
        # highest hourly so far
        if k >= 1 and D.n and D.H[:k].max() > 0:
            h, i = np.unravel_index(np.argmax(D.H[:k]), D.H[:k].shape)
            text(d, (180, 690), f"{mm1(D.H[h, i])} mm", font(34, "Bold"), TEXT, "ms")
            text(d, (180, 715), fit(d, D.names[i], font(17, "Bold"), 265), font(17, "Bold"), TEXT, "ms")
            text(d, (180, 734), f"{D.dist[i]}  ·  {clock(D.t0 + timedelta(hours=int(h) + 1))}", font(14), MUTED, "ms")
        else:
            text(d, (180, 690), "–", font(34, "Bold"), TEXT, "ms")
        # leaders so far
        now = D.at(tau)
        order = [i for i in np.argsort(-now)[:5] if now[i] > 0]
        top = now[order[0]] if order else 1
        f17 = font(17, "Bold")
        for r, i in enumerate(order):
            y = 838 + 52 * r
            vs = mm1(now[i])
            vw = d.textlength(vs, font=f17)
            text(d, (56, y), f"{r + 1}   " + fit(d, D.names[i], f17, 236 - vw), f17, TEXT)
            text(d, (305, y), vs, f17, TEXT, "rs")
            d.rectangle((56, y + 9, 56 + max(4, 249 * now[i] / top), y + 13), fill=colour(now[i]))
        n_rain = int(np.sum(now > 0))
        text(d, (180, 1140), f"{n_rain} / {D.n}", font(25, "Bold"), TEXT, "ms")

    # ---- toppers card ---------------------------------------------------------
    def toppers(self, last, t):
        """The day's five wettest stations, fading in over the last map frame. t = frames since start."""
        D = self.D
        im = last.copy()
        a0 = min(1.0, t / 12)
        cover = Image.new("RGBA", (W, H - 205), BG + (int(255 * a0),))
        im.paste(cover, (0, 205), cover)
        d = ImageDraw.Draw(im, "RGBA")
        if a0 < 1:
            return im
        order = [int(i) for i in np.argsort(-D.total)[:5] if D.total[i] > 0]
        text(d, (540, 262), spaced("RAINFALL TOPPERS"), font(40, "Bold"), TEXT, "ms")
        text(d, (540, 291), f"Top 5 of {D.n:,} stations  ·  past 24 hours  ·  {self.R['source']} "
                            f"{self.R['network'].lower()}s", font(15), MUTED, "ms")
        text(d, (540, 314), self.highlight(order), font(15, "Bold"), GOLD, "ms")
        top = D.total[order[0]] if order else 1
        for r, i in enumerate(order):
            ra = min(1.0, max(0.0, (t - 12 - 7 * r) / 10))
            if ra <= 0:
                continue
            al = int(255 * ra)
            y0 = 330 + 158 * r
            v = D.total[i]
            col = colour(v)
            if r == 0:
                d.rectangle((43, y0, 1037, y0 + 150), fill=(30, 27, 19, al))
                d.rectangle((43, y0, 46, y0 + 150), fill=GOLD + (al,))
            elif r:
                d.line((43, y0 - 4, 1037, y0 - 4), fill=(32, 37, 46, al))
            text(d, (65, y0 + 58), f"{r + 1:02d}", font(22, "Bold"), (GOLD if r == 0 else MUTED) + (al,))
            text(d, (125, y0 + 48), fit(d, D.names[i], font(26, "Bold"), 600), font(26, "Bold"), TEXT + (al,))
            text(d, (125, y0 + 76), f"{D.dist[i]}  ·  {self.R['network']}", font(15), MUTED + (al,))
            text(d, (1030, y0 + 62), mm1(v), font(40, "Bold"), TEXT + (al,), "rs")
            text(d, (1030, y0 + 84), "mm", font(13), MUTED + (al,), "rs")
            text(d, (1030, y0 + 104), spaced(category(v)), font(12, "Bold"), col + (al,), "rs")
            d.rectangle((125, y0 + 106, 910, y0 + 112), fill=(38, 43, 52, al))
            d.rectangle((125, y0 + 106, 125 + 785 * (v / top) * rr.ease_out(ra), y0 + 112), fill=col + (al,))
        sa = min(1.0, max(0.0, (t - 50) / 10))
        if sa > 0:
            al = int(255 * sa)
            stats = [(f"{D.total.mean():.1f} mm" if D.n else "–", "STATION AVERAGE"),
                     (f"{int(np.sum(D.total >= 64.5))}", "STATIONS WITH HEAVY RAIN OR MORE"),
                     (f"{int(np.sum(D.total > 0))}", "STATIONS WITH RAIN")]
            for j, (val, lab) in enumerate(stats):
                x = 210 + 330 * j
                text(d, (x, 1165), val, font(34, "Bold"), TEXT + (al,), "ms")
                text(d, (x, 1190), spaced(lab), font(11, "Bold"), MUTED + (al,), "ms")
        return im

    def highlight(self, order):
        D = self.D
        if not order:
            return "NO RAIN RECORDED"
        ds = [D.dist[i] for i in order]
        best = max(ds, key=lambda x: (ds.count(x), -ds.index(x)))
        n = ds.count(best)
        if n >= 2:
            return spaced(f"{n} OF THE TOP {len(order)} STATIONS ARE IN {best.upper()}")
        i = order[0]
        return spaced(f"WETTEST: {D.names[i].upper()}, {D.dist[i].upper()}")

    def caption(self):
        D = self.D
        d0, d1 = D.t0.date(), D.day
        order = [int(i) for i in np.argsort(-D.total)[:5] if D.total[i] > 0]
        lines = [f"🌧 {self.region['name']} · Past 24 hours observed rainfall",
                 f"8:30 am {d0.day} {d0:%b} to 8:30 am {d1.day} {d1:%b %Y} · hour by hour", "", "Rainfall toppers:"]
        for r, i in enumerate(order, 1):
            lines.append(f"{r}. {D.names[i]} ({D.dist[i]}) – {mm1(D.total[i])} mm")
        if D.n and D.H.max() > 0:
            h, i = np.unravel_index(np.argmax(D.H), D.H.shape)
            lines += ["", f"Heaviest in one hour: {mm1(D.H[h, i])} mm at {D.names[i]} ({D.dist[i]}), "
                          f"hour ending {clock(D.t0 + timedelta(hours=int(h) + 1))}"]
        lines += [f"Rain at {int(np.sum(D.total > 0))} of {D.n} stations · "
                  f"{int(np.sum(D.total >= 64.5))} with heavy rain or more", self.window_text()]
        S = self.scores()
        if S["obs"]:
            lines += ["", f"Did the models get it right? ({S['label']}) at a gauge in {len(S['obs'])} districts)"]
            for k, name, sc in S["rows"]:
                lines.append(f"  {name}: " + ("no forecast" if sc is None else
                                              f"{len(sc['caught'])} caught, {len(sc['false'])} false alarms"))
        lines += ["", f"Source: {self.R['source']} · forecasts: ECMWF open data (CC BY 4.0)",
                  f"#{rr.BRAND} · {rr.SITE}"]
        return "\n".join(lines)

    # ---- rain clock -------------------------------------------------------------
    def _cover(self, prev, t):
        """Fade the area under the header to the background; returns (picture, done)."""
        im = prev.copy()
        a0 = min(1.0, t / 12)
        cover = Image.new("RGBA", (W, H - 205), BG + (int(255 * a0),))
        im.paste(cover, (0, 205), cover)
        return im, a0 >= 1

    def window_text(self):
        if not self.window:
            return "No rain fell"
        i, j = self.window
        if j - i + 1 > 12:
            return "Rain was spread across the day"
        t = self.D.t0
        return f"Most of it fell {clock(t + timedelta(hours=i))} – {clock(t + timedelta(hours=j + 1))}"

    def rain_clock(self, prev, t):
        """24-hour dial, 08:30 at the top, filling hour by hour. Slice length = state-average rain in
        that hour, colour = the heaviest gauge in it. Ends with the hours that held half the rain."""
        D = self.D
        im, done = self._cover(prev, t)
        if not done:
            return im
        d = ImageDraw.Draw(im, "RGBA")
        text(d, (540, 262), spaced("RAIN CLOCK"), font(40, "Bold"), TEXT, "ms")
        text(d, (540, 291), "When the rain fell  ·  8:30 AM to 8:30 AM IST, hour by hour", font(15), MUTED, "ms")
        tau = 24 * rr.ease(max(0.0, t - 14) / 130)
        cx0, cy0, r_in, r_base, r_max, r_lab = 540, 735, 112, 172, 330, 368
        ss = 2
        size = 2 * (r_lab + 30)
        lay = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        c = size * ss / 2

        def box(r):
            return (c - r * ss, c - r * ss, c + r * ss, c + r * ss)

        def ang(hours):
            return -90 + hours * 15

        for k in range(24):                     # day / night track; night = 18:30 to 06:30
            dl.pieslice(box(r_base), ang(k), ang(k + 1), fill=(17, 22, 32) if 10 <= k < 22 else (34, 44, 64))
        mx = max(D.state_hourly.max(), 1e-6)
        for k in range(24):
            if tau <= k:
                break
            v = D.state_hourly[k]
            if v <= 0:
                continue
            r = r_base * 0.92 + (r_max - r_base * 0.92) * math.sqrt(v / mx)
            dl.pieslice(box(r), ang(k), ang(k + min(1.0, tau - k)), fill=colour(max(D.peak_hourly[k], 0.1)) + (255,))
        for k in range(24):                     # slice gaps
            a = math.radians(ang(k))
            dl.line((c + r_in * ss * math.cos(a), c + r_in * ss * math.sin(a), c + (r_max + 2) * ss * math.cos(a),
                     c + (r_max + 2) * ss * math.sin(a)), fill=BG + (255,), width=2 * ss)
        dl.ellipse(box(r_in), fill=CARD + (255,), outline=CARD_EDGE + (255,), width=2 * ss)
        if self.window and t >= 150:            # the peak window
            i, j = self.window
            e = rr.ease((t - 150) / 15)
            dl.arc(box(r_max + 14), ang(i), ang(i + (j + 1 - i) * e), fill=GOLD + (255,), width=6 * ss)
        a = math.radians(ang(tau))
        dl.line((c + (r_in + 4) * ss * math.cos(a), c + (r_in + 4) * ss * math.sin(a),
                 c + (r_max + 12) * ss * math.cos(a), c + (r_max + 12) * ss * math.sin(a)), fill=GOLD + (255,),
                width=4 * ss)
        ex, ey = c + (r_max + 12) * ss * math.cos(a), c + (r_max + 12) * ss * math.sin(a)
        dl.ellipse((ex - 7 * ss, ey - 7 * ss, ex + 7 * ss, ey + 7 * ss), fill=GOLD + (255,))
        for hrs, sun in ((4, True), (16, False)):   # sun at 12:30 PM, moon at 12:30 AM
            b = math.radians(ang(hrs))
            sx, sy = c + r_lab * ss * math.cos(b), c + r_lab * ss * math.sin(b)
            r = 11 * ss
            if sun:
                dl.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(250, 204, 92, 255))
                for q in range(8):
                    g = q * math.pi / 4
                    dl.line((sx + 1.4 * r * math.cos(g), sy + 1.4 * r * math.sin(g), sx + 1.9 * r * math.cos(g),
                             sy + 1.9 * r * math.sin(g)), fill=(250, 204, 92, 255), width=ss)
            else:
                dl.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(214, 222, 236, 255))
                dl.ellipse((sx - r + 7 * ss, sy - r - 3 * ss, sx + r + 7 * ss, sy + r - 3 * ss), fill=BG + (255,))
        lay = lay.resize((size, size), Image.LANCZOS)
        im.paste(lay, (int(cx0 - size / 2), int(cy0 - size / 2)), lay)
        d = ImageDraw.Draw(im, "RGBA")
        for hrs, lab in ((0, "8:30 AM"), (6, "2:30 PM"), (12, "8:30 PM"), (18, "2:30 AM")):
            b = math.radians(ang(hrs))
            text(d, (cx0 + r_lab * math.cos(b), cy0 + r_lab * math.sin(b) + 6), lab, font(17, "Bold"), SOFT, "ms")
        now = D.t0 + timedelta(minutes=15 * math.floor(tau * 4 + 1e-6))
        text(d, (cx0, cy0 - 34), clock(now), font(22, "Bold"), GOLD, "ms")
        text(d, (cx0, cy0 + 22), f"{int(np.sum(D.at(tau) >= 0.1))}", font(54, "Bold"), TEXT, "ms")
        text(d, (cx0, cy0 + 46), f"of {D.n} stations with rain", font(14, "Bold"), MUTED, "ms")
        text(d, (540, 1132), "Slice length: state-average rain in the hour  ·  colour: the heaviest gauge in it",
             font(15), MUTED, "ms")
        if t >= 150:
            al = int(255 * rr.ease((t - 150) / 12))
            text(d, (540, 1196), self.window_text(), font(34, "Bold"), GOLD + (al,), "ms")
            if self.window:
                i, j = self.window
                text(d, (540, 1226), f"Half of the day's rain fell in these {j - i + 1} hours", font(17),
                     SOFT + (al,), "ms")
        return im

    # ---- model scorecard -----------------------------------------------------------
    def scores(self):
        """Observed heavy-rain districts and each model's score. Falls back to moderate rain when no
        district had heavy rain."""
        if hasattr(self, "_scores"):
            return self._scores
        dm = self.D.district_max()
        thr, label = 64.5, "heavy rain (≥ 64.5 mm"
        obs = sorted((d for d, v in dm.items() if v >= thr), key=lambda d: -dm[d])
        if not obs:
            thr, label = 15.6, "moderate rain (≥ 15.6 mm"
            obs = sorted((d for d, v in dm.items() if v >= thr), key=lambda d: -dm[d])
        rows = [(k, models.NAMES[k], models.score(self.fc.get(k), obs, thr)) for k in ("wn3", "ifs", "aifs")]
        have = [(k, s) for k, _, s in rows if s is not None]
        win = None
        if have and obs:
            key = lambda s: (len(s["caught"]), -len(s["false"]))
            best = max(key(s) for _, s in have)
            ties = [k for k, s in have if key(s) == best]
            win = ties[0] if len(ties) == 1 else "tie"
        self._scores = {"thr": thr, "label": label, "obs": obs, "rows": rows, "win": win}
        return self._scores

    def scorecard(self, prev, t):
        """Heavy-rain districts each model's forecast caught; bars fill one by one, then the winner."""
        S = self.scores()
        im, done = self._cover(prev, t)
        if not done:
            return im
        d = ImageDraw.Draw(im, "RGBA")
        run = models.run_for(self.D.day)
        obs, n = S["obs"], len(S["obs"])
        text(d, (540, 262), spaced("DID THE MODELS GET IT RIGHT?"), font(40, "Bold"), TEXT, "ms")
        text(d, (540, 291), f"24-hour rain forecast from the {run:%H} UTC run of {run.day} {run:%b} "
                            f"({clock(run.astimezone(tf.IST))} IST) vs the rain gauges", font(15), MUTED, "ms")
        head = (f"{S['label'].upper()}) AT A GAUGE IN {n} DISTRICT{'S' if n != 1 else ''}" if n else
                "NO DISTRICT HAD MODERATE OR HEAVY RAIN: NOTHING TO SCORE")
        text(d, (540, 314), spaced(head), font(15, "Bold"), GOLD, "ms")
        for r, (key, name, sc) in enumerate(S["rows"]):
            ra = rr.ease((t - 12 - 8 * r) / 10)
            if ra <= 0:
                continue
            al = int(255 * ra)
            y0 = 340 + 232 * r
            win = S["win"] == key and t >= 120
            rr.rrect(d, (43, y0, 1037, y0 + 216), 12, fill=CARD + (al,), outline=(GOLD if win else CARD_EDGE) + (al,),
                     width=3 if win else 1)
            text(d, (70, y0 + 48), name, font(30, "Bold"), TEXT + (al,))
            if sc is None:
                text(d, (70, y0 + 76), "WeatherNext 3 data was not available to this report" if key == "wn3"
                     else "ECMWF open data for this run was not available", font(15), MUTED + (al,))
                text(d, (540, y0 + 150), "no forecast", font(30, "Bold"), (98, 106, 120, al), "ms")
                continue
            text(d, (70, y0 + 76), "highest forecast inside each district vs the highest gauge there", font(15),
                 MUTED + (al,))
            c = len(sc["caught"])
            bt = max(0.0, rr.ease_out((t - 24 - 30 * r) / 24))
            tail = f" / {n}"
            ft = font(28, "Bold")
            text(d, (1010, y0 + 66), tail, ft, SOFT + (al,), "rs")
            text(d, (1010 - d.textlength(tail, font=ft), y0 + 66), f"{round(c * bt)}", font(56, "Bold"), GOLD + (al,),
                 "rs")
            text(d, (1010, y0 + 88), "districts caught", font(13), MUTED + (al,), "rs")
            d.rectangle((70, y0 + 104, 1010, y0 + 118), fill=DIM_BAR + (al,))
            if n:
                d.rectangle((70, y0 + 104, 70 + 940 * c / n * bt, y0 + 118), fill=GOLD + (al,))
            ca = int(al * min(1.0, bt * 1.5))
            x, y = 70, y0 + 134
            fc = font(15, "Bold")
            for dn in obs:                       # district chips: filled = caught, outlined = missed
                wdt = d.textlength(dn, font=fc) + 24
                if x + wdt > 1010:
                    x, y = 70, y + 36
                    if y > y0 + 170:
                        break
                hit = dn in sc["caught"]
                rr.rrect(d, (x, y, x + wdt, y + 28), 14, fill=(GOLD + (ca,)) if hit else None,
                         outline=(GOLD if hit else (80, 90, 108)) + (ca,), width=2)
                text(d, (x + 12, y + 20), dn, fc, ((20, 22, 28) if hit else SOFT) + (ca,))
                x += wdt + 8
            fa = len(sc["false"])
            text(d, (70, y0 + 204), f"hit rate {100 * c / n:.0f}%  ·  " if n else "", font(14), MUTED + (ca,))
            text(d, (70 + (d.textlength(f"hit rate {100 * c / n:.0f}%  ·  ", font=font(14)) if n else 0), y0 + 204),
                 f"{fa} false alarm{'s' if fa != 1 else ''} (forecast heavy, none observed)", font(14), MUTED + (ca,))
        if t >= 120:
            s = rr.ease_out((t - 120) / 10)
            al = int(255 * min(1.0, (t - 120) / 6))
            y = 1050
            if S["win"] is None:
                text(d, (540, y + 70), "No model forecasts to score today" if obs else "", font(28, "Bold"),
                     SOFT + (al,), "ms")
            else:
                text(d, (540, y + 10), spaced("TODAY'S WINNER"), font(16, "Bold"), MUTED + (al,), "ms")
                name = "It's a tie" if S["win"] == "tie" else models.NAMES[S["win"]]
                fw = font(int(46 * (0.7 + 0.3 * s)), "ExtraBold")
                pw = d.textlength(name, font=fw) + 64
                rr.rrect(d, (540 - pw / 2, y + 24, 540 + pw / 2, y + 100), 38, fill=GOLD + (al,))
                text(d, (540, y + 79), name, fw, BG + (al,), "ms")
                if S["win"] != "tie":
                    sc = {k: s_ for k, _, s_ in S["rows"]}[S["win"]]
                    fa = len(sc["false"])
                    text(d, (540, y + 136), f"caught {len(sc['caught'])} of {n} districts, {fa} false "
                                            f"alarm{'s' if fa != 1 else ''}", font(19), SOFT + (al,), "ms")
            text(d, (540, 1222), "Not an official verification. Forecasts: ECMWF open data (CC BY 4.0).", font(13),
                 MUTED + (al,), "ms")
        return im

    # ---- render -----------------------------------------------------------------
    def render(self, base):
        wr = rr.Writer(base + ".mp4", size=(W, H))
        last = None
        for f in range(self.F_TOP):
            if f < self.F_END + 1:
                last = self.frame(f)
            wr.add(last)
            if f == self.F_END:
                last.save(base + ".png", optimize=True)
        im = last
        for t in range(self.F_CLOCK - self.F_TOP):
            im = self.toppers(last, t)
            wr.add(im)
        last = im
        for t in range(self.F_SCORE - self.F_CLOCK):
            im = self.rain_clock(last, t)
            wr.add(im)
        last = im
        for t in range(self.N - self.F_SCORE):
            wr.add(self.scorecard(last, t))
        return wr


def run(region_key, day, out_dir):
    if region_key not in REGIONS24:
        raise SystemExit(f"live24 is not set up for {rr.REGIONS[region_key]['name']} yet (see live24.py)")
    R = REGIONS24[region_key]
    geo = rr.Geo(rr.REGIONS[region_key])
    stations, hours = R["hourly"](day)
    if not stations:
        raise SystemExit(f"No hourly data for {day}")
    D = Day(day, stations, hours, geo)
    print(f"{rr.REGIONS[region_key]['name']}: past 24 hours to 08:30 {day}, {D.n} stations, "
          f"{int(np.sum(D.total > 0))} with rain")
    tag = "" if region_key == "tn" else f"{region_key}_"
    base = os.path.join(out_dir, f"rain_{tag}live24_{day.isoformat()}")
    print("  model forecasts …")
    fc = models.forecasts(day, geo)
    V = Live24(region_key, D, geo, fc)
    print("Rendering …")
    wr = V.render(base)
    wr.close()
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(V.caption())
    print(f"  video : {base}.mp4  ({wr.n / FPS:.1f} s)")
    return base
