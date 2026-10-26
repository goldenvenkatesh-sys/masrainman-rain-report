"""
MasRainman · "Past 24 Hours Observed Rainfall": an animated, hour-by-hour video (1080x1920, 9:16, ~29 s).

Scenes
  sting      MasRainman raindrop and wordmark
  hook       the answer first: a headline in the state's language, then the day's biggest 24-hour total
             counting up, with its station and district
  build-up   the rain-gauge readings of the rain-day (08:30 to 08:30 IST) added up hour by hour on a
             hillshaded terrain map. The camera follows the gauge with the heaviest rain in each hour,
             then pulls back to the whole state. The rain clock fills slice by slice with the hours.
  toppers    the five wettest stations
  scorecard  "Did the models get it right?": heavy-rain districts each model's forecast caught (models.py)

  python rain_report.py live24                      # Tamil Nadu, latest complete rain-day
  python rain_report.py live24 --date 2026-10-26

Adding a state: give it an entry in REGIONS24 below with
  native   state name in its own script, and native_font, a font in fonts/ that has that script
  hourly   function(rain-day) -> (stations, hours): stations as in tnsmart_fetch.station_totals()
           (district, station, lat, lon, rain), hours a 24 x stations list of mm per hour, 08:30-09:30 first
  hook     function(district, category) -> the opening headline in the state's language
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
import tn_names
import tnsmart_fetch as tf

CAT_TA = {"LIGHT": "லேசான மழை", "MODERATE": "மிதமான மழை", "HEAVY": "கனமழை", "VERY HEAVY": "மிக கனமழை",
          "EXTREMELY HEAVY": "அதி கனமழை"}

REGIONS24 = {
    "tn": {"native": "தமிழ்நாடு", "native_font": "MuktaMalar-ExtraBold.ttf", "hourly": tf.hourly_stations,
           "hook": lambda dist, cat: f"{tn_names.ta(dist)} மாவட்டத்தில் {CAT_TA.get(cat, 'மழை')}",
           "source": "TN-SMART (TNSDMA)", "network": "Hourly gauge", "dem_zoom": 9,
           "peaks": [("Doddabetta", 11.40, 76.74), ("Anamalai", 10.35, 77.05), ("Kodaikanal", 10.24, 77.49),
                     ("Shevaroy", 11.80, 78.22), ("Kolli Hills", 11.27, 78.36), ("Javadhu", 12.57, 78.83),
                     ("Megamalai", 9.66, 77.39), ("Pothigai", 8.60, 77.25)]},
}

# ----------------------------------------------------------------- look
W, H = 1080, 1920
FPS = 30
BG = (11, 31, 28)                          # deep green
CARD = (17, 45, 40)
CARD_EDGE = (32, 70, 62)
TEXT = (238, 246, 242)
SOFT = (159, 225, 203)                     # #9FE1CB
TEAL = (93, 202, 165)                      # #5DCAA5
CORAL = (240, 153, 123)                    # #F0997B
DIM_BAR = (30, 62, 56)
MAP = (40, 258, 1040, 1338)                # map window
VW, VH = MAP[2] - MAP[0], MAP[3] - MAP[1]
ZW = 3.0                                   # world raster = 3x the full-state view
ZF = 2.6                                   # zoom while following the heaviest hourly rain
FDS = 3                                    # rain field is computed at 1/3 the world raster
SMOOTH_KM = 4                              # blur of the rain field
DIAL = (40, 1352, 440, 1782)               # rain clock card
DC, R_IN, R_BASE, R_MAX, R_LAB = (240, 1584), 56, 88, 146, 166

# colour ramp for cumulative mm (log-ish), and the colour bar's tick positions
STOPS = [(0.1, "#24406a"), (1, "#2c4a72"), (2.5, "#2f6f96"), (5, "#2a8f9c"), (10, "#2fa68f"),
         (15.6, "#69bb72"), (25, "#b5cc55"), (40, "#e3c94a"), (64.5, "#f0a23c"), (100, "#e0503e"),
         (150, "#c73a7c"), (204.5, "#b45cb8"), (300, "#efd9f5")]
TICKS = [(1, 122), (2.5, 207), (5, 298), (10, 408), (25, 576), (50, 712), (100, 852), (200, 994)]
CATS = [(0.1, "LIGHT", "0.1 – 15.5"), (15.6, "MODERATE", "15.6 – 64.4"), (64.5, "HEAVY", "64.5 – 115.5"),
        (115.6, "VERY HEAVY", "115.6 – 204.4"), (204.5, "EXTREMELY HEAVY", "≥ 204.5")]
HEAVY = 64.5
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


def fade(im, a):
    """An RGBA layer with its alpha scaled by a (0..1)."""
    if a < 1:
        im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    return im


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

    def headline_district(self):
        """The district to name in the opening headline: most gauges with heavy rain, then the wettest gauge."""
        if not self.n:
            return None
        cnt, best = {}, {}
        for d, v in zip(self.dist, self.total):
            cnt[d] = cnt.get(d, 0) + (v >= HEAVY)
            best[d] = max(best.get(d, 0.0), float(v))
        return max(cnt, key=lambda d: (cnt[d], best[d]))

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
    # timeline (frames at 30 fps)
    F_HOOK = 24                          # sting before this
    F_WIPE = (96, 108)                   # hook wipes into the map
    F_MAP = 108
    F_START, F_END = 120, 630            # the 24 hours play between these frames
    ZOOM_IN, ZOOM_OUT = (120, 152), (532, 574)
    F_TOP = 666                          # toppers card from here
    F_SCORE = 756                        # model scorecard from here
    N = 876

    def __init__(self, region_key, D, geo, forecasts=None):
        self.R = REGIONS24[region_key]
        self.region = rr.REGIONS[region_key]
        self.D, self.geo = D, geo
        self.fc = forecasts or {}
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
        self.head = self._header_layer()
        self.base = self._static()
        self.sx, self.sy = self.proj(D.lon, D.lat)
        self._card_side = 0
        self.window = D.peak_window()

    # ---- rasters --------------------------------------------------------
    def _world(self):
        WW, WH, P = self.WW, self.WH, self.proj
        m = Image.new("L", (WW, WH), 0)
        rr.draw_geom(ImageDraw.Draw(m), self.state, P, fill=255)
        self.mask = cv2.GaussianBlur(np.asarray(m), (3, 3), 0)
        lon, lat = P.inverse_grid(0, 0, WW, WH)
        dem = terrain.elevation(lon, lat, z=self.R.get("dem_zoom", 9))
        del lon, lat
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

    # ---- static layers ----------------------------------------------------------
    def _footer(self, d):
        text(d, (540, 1910), f"Data: {self.R['source']}  ·  Terrain: SRTM via AWS Terrain Tiles  ·  "
                             f"{rr.BRAND} · {rr.SITE}", font(13), TEAL, "ms")

    def _header_layer(self):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im, "RGBA")
        D = self.D
        text(d, (40, 62), spaced("BY  MASRAINMAN"), font(18, "Bold"), CORAL)
        text(d, (40, 142), self.R["native"], font(76, file=self.R["native_font"]), TEXT)
        f30 = font(30)
        s0 = self.region["name"].upper()
        text(d, (40, 188), s0, f30, SOFT)
        text(d, (40 + d.textlength(s0, font=f30), 188), "  ·  Past 24 Hours Observed Rainfall", f30, SOFT)
        text(d, (40, 223), f"Ending {D.day.day} {D.day:%b %Y}, 8:30 AM IST  ·  Hourly accumulation of "
                           f"rain-gauge observations", font(19), TEAL)
        d.rectangle((40, 240, 110, 243), fill=CORAL)
        d.line((110, 241, 1040, 241), fill=CARD_EDGE, width=1)
        self._footer(d)
        return im

    def _static(self):
        im = self.head.copy()
        d = ImageDraw.Draw(im, "RGBA")
        for box in [DIAL, (460, 1352, 745, 1512), (755, 1352, 1040, 1512), (460, 1524, 1040, 1782)]:
            rr.rrect(d, box, 12, fill=CARD, outline=CARD_EDGE)
        f13 = font(13, "Bold")
        text(d, (60, 1382), spaced("RAIN CLOCK  ·  IST"), f13, TEAL)
        text(d, (602, 1380), spaced("PEAK THIS HOUR"), f13, TEAL, "ms")
        text(d, (897, 1380), spaced("HIGHEST HOURLY SO FAR"), f13, TEAL, "ms")
        text(d, (480, 1552), spaced("LEADERS SO FAR  ·  mm"), f13, TEAL)
        # colour bar and categories
        text(d, (40, 1808), spaced("CUMULATIVE RAINFALL  ·  mm"), f13, TEAL)
        mx = np.array([p for _, p in TICKS], float)
        lv = np.log10([v for v, _ in TICKS])
        for x in range(40, 1040):
            j = min(max(np.searchsorted(mx, x) - 1, 0), len(mx) - 2)       # extend the end segments
            v = 10 ** (lv[j] + (x - mx[j]) / (mx[j + 1] - mx[j]) * (lv[j + 1] - lv[j]))
            d.line((x, 1818, x, 1830), fill=colour(v))
        for v, x in TICKS:
            text(d, (x, 1851), "200+" if v == 200 else f"{v:g}", font(14), SOFT, "ms")
        cw = 1000 / 5
        for i, (lo, name, rng) in enumerate(CATS):
            x0 = 40 + i * cw
            d.rectangle((x0, 1862, x0 + cw - 12, 1864), fill=colour([8, 35, 85, 160, 260][i]))
            text(d, (x0, 1884), spaced(name), font(12, "Bold"), TEXT)
            text(d, (x0 + cw - 12, 1884), rng, font(11), TEAL, "rs")
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
        land = (14 + 130 * shade ** 1.3)[..., None] * np.array([0.9, 1.02, 0.98], np.float32)
        out = land * (1 - a) + rain * a
        m = mask[..., None]
        out = np.array(BG, np.float32) * (1 - m) + out * m
        im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
        d = ImageDraw.Draw(im, "RGBA")
        for ring in self.rings:
            x, y = self.to_view(ring[:, 0], ring[:, 1], cx, cy, z)
            d.line(list(zip(x, y)), fill=(222, 240, 232, 170), width=1)
        return im, (cx, cy, z)

    def peaks(self, d, cam, alpha):
        if alpha <= 0:
            return
        cx, cy, z = cam
        f = font(15, "Bold")
        for name, lat, lon in self.R.get("peaks", []):
            wx, wy = self.proj(lon, lat)
            x, y = self.to_view(wx, wy, cx, cy, z)
            if not (20 < x < VW - 130 and 170 < y < VH - 20):
                continue
            x, y = x + MAP[0], y + MAP[1]
            a = int(220 * alpha)
            d.polygon([(x - 6, y + 4), (x + 6, y + 4), (x, y - 7)], fill=(215, 230, 222, a),
                      outline=(10, 24, 20, a))
            d.text((x + 11, y + 6), name, font=f, fill=(6, 16, 14, int(160 * alpha)), anchor="ls")
            d.text((x + 10, y + 5), name, font=f, fill=(225, 238, 232, a), anchor="ls")

    def inset(self, im, cam, alpha):
        if alpha <= 0:
            return
        cx, cy, z = cam
        iw = 124
        k = iw / self.WW
        ih = int(self.WH * k)
        x0, y0 = MAP[0] + 14, MAP[3] - ih - 14
        lay = Image.new("RGBA", (iw, ih), (8, 26, 23, int(205 * alpha)))
        d = ImageDraw.Draw(lay)
        for ring in self.rings:
            d.line([(x * k, y * k) for x, y in ring], fill=(225, 240, 232, int(230 * alpha)), width=1)
        sc = ZW / z
        r = ((cx - VW / 2 * sc) * k, (cy - VH / 2 * sc) * k, (cx + VW / 2 * sc) * k, (cy + VH / 2 * sc) * k)
        d.rectangle(r, outline=CORAL + (int(255 * alpha),), width=2)
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
        cw, ch = 260, 228
        col = colour(max(hr[i], 8))
        top = 170                                        # keep clear of the clock and badge
        opts = [(x - 46 - cw, y - 70 - ch), (x + 46, y - 70 - ch), (x - 46 - cw, y + 70), (x + 46, y + 70)]
        ok = [10 <= ox and ox + cw <= VW - 10 and top <= oy and oy + ch <= VH - 10 for ox, oy in opts]
        if not ok[self._card_side]:
            self._card_side = ok.index(True) if any(ok) else 0
        ox, oy = opts[self._card_side]
        ox = min(max(ox, 10), VW - 10 - cw)
        oy = min(max(oy, top), VH - 10 - ch)
        lay = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        # leader line and ring at the gauge
        ax = ox + cw if ox + cw < x else ox if ox > x else x
        ay = oy + ch if oy + ch < y else oy if oy > y else y
        d.line((ax, ay, x, y), fill=col + (200,), width=2)
        d.ellipse((x - 15, y - 15, x + 15, y + 15), outline=col + (230,), width=2)
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=col + (255,))
        rr.rrect(d, (ox, oy, ox + cw, oy + ch), 8, fill=(8, 26, 23, 238), outline=col + (255,), width=2)
        lx = ox + 12
        text(d, (lx, oy + 19), spaced("PEAK RAINFALL THIS HOUR"), font(11, "Bold"), col)
        text(d, (lx, oy + 47), fit(d, D.names[i], font(24, "Bold"), cw - 24), font(24, "Bold"), TEXT)
        text(d, (lx, oy + 66), fit(d, D.dist[i].upper(), font(13, "Bold"), cw - 24), font(13, "Bold"), SOFT)
        big = f"{hr[i]:.0f}" if hr[i] >= 10 else f"{hr[i]:.1f}"
        fb = font(44, "Bold")
        text(d, (lx, oy + 110), big, fb, col)
        text(d, (lx + d.textlength(big, font=fb) + 10, oy + 108), "mm / hr", font(16, "SemiBold"), SOFT)
        text(d, (lx, oy + 130), spaced("THIS GAUGE  ·  HOUR BY HOUR"), font(10, "Bold"), TEAL)
        bx0, bx1, by = lx, ox + cw - 12, oy + 172
        bw = (bx1 - bx0) / 24
        mxv = max(D.H[:, i].max(), 1)
        for h in range(24):
            v = D.H[h, i]
            if h + 1 > k:
                d.rectangle((bx0 + h * bw + 1, by - 2, bx0 + (h + 1) * bw - 2, by), fill=DIM_BAR + (255,))
                continue
            hh = max(2, 36 * v / mxv)
            fill = col + (255,) if h + 1 == k else (120, 160, 150, 255)
            d.rectangle((bx0 + h * bw + 1, by - hh, bx0 + (h + 1) * bw - 2, by), fill=fill)
        text(d, (lx, oy + 192), f"{D.cum[k][i]:.0f} mm so far", font(14, "Bold"), TEXT)
        f12 = font(13)
        for r, j in enumerate(order[1:3], 2):
            if hr[j] <= 0:
                break
            text(d, (lx, oy + 192 + 16 * (r - 1)), f"{r}   {fit(d, D.names[j], f12, 160)}   {hr[j]:.0f} mm",
                 f12, TEAL)
        im.paste(fade(lay, alpha), (MAP[0], MAP[1]), lay)

    # ---- rain clock -------------------------------------------------------------
    def dial(self, im, tau, f):
        """24-hour dial, 08:30 at the top. Slice length = state average rain in that hour,
        slice colour = the heaviest gauge in it. Hand = now; centre = stations with rain so far."""
        D = self.D
        ss = 2
        x0, y0, x1, y1 = DIAL
        w, h = x1 - x0, y1 - y0
        lay = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        cx, cy = (DC[0] - x0) * ss, (DC[1] - y0) * ss

        def box(r):
            return (cx - r * ss, cy - r * ss, cx + r * ss, cy + r * ss)

        def ang(hours):
            return -90 + hours * 15

        for k in range(24):                     # day / night track; night = 18:30 to 06:30
            night = 10 <= k < 22
            d.pieslice(box(R_BASE), ang(k), ang(k + 1), fill=(10, 30, 27) if night else (24, 62, 55))
        mx = max(D.state_hourly.max(), 1e-6)
        for k in range(24):
            if tau <= k:
                break
            frac = min(1.0, tau - k)
            v = D.state_hourly[k]
            if v <= 0:
                continue
            r = R_BASE * 0.92 + (R_MAX - R_BASE * 0.92) * math.sqrt(v / mx)
            d.pieslice(box(r), ang(k), ang(k + frac), fill=colour(max(D.peak_hourly[k], 0.1)) + (255,))
        for k in range(24):                     # slice gaps
            a = math.radians(ang(k))
            d.line((cx + R_IN * ss * math.cos(a), cy + R_IN * ss * math.sin(a),
                    cx + (R_MAX + 2) * ss * math.cos(a), cy + (R_MAX + 2) * ss * math.sin(a)), fill=CARD + (255,),
                   width=ss)
        d.ellipse(box(R_IN), fill=CARD + (255,), outline=CARD_EDGE + (255,), width=ss)
        # peak window arc, once the day is done
        if self.window and f > self.F_END:
            a = min(1.0, (f - self.F_END) / 12)
            i, j = self.window
            d.arc(box(R_MAX + 9), ang(i), ang(i + (j + 1 - i) * rr.ease(a)), fill=CORAL + (255,), width=4 * ss)
        # hand
        a = math.radians(ang(tau))
        d.line((cx + (R_IN + 2) * ss * math.cos(a), cy + (R_IN + 2) * ss * math.sin(a),
                cx + (R_MAX + 8) * ss * math.cos(a), cy + (R_MAX + 8) * ss * math.sin(a)), fill=CORAL + (255,),
               width=3 * ss)
        ex, ey = cx + (R_MAX + 8) * ss * math.cos(a), cy + (R_MAX + 8) * ss * math.sin(a)
        d.ellipse((ex - 5 * ss, ey - 5 * ss, ex + 5 * ss, ey + 5 * ss), fill=CORAL + (255,))
        # sun at 12:30 PM, moon at 12:30 AM
        for hrs, sun in ((4, True), (16, False)):
            b = math.radians(ang(hrs))
            sx, sy = cx + R_LAB * ss * math.cos(b), cy + R_LAB * ss * math.sin(b)
            r = 8 * ss
            if sun:
                d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(250, 204, 92, 255))
                for q in range(8):
                    c = q * math.pi / 4
                    d.line((sx + 1.4 * r * math.cos(c), sy + 1.4 * r * math.sin(c), sx + 1.9 * r * math.cos(c),
                            sy + 1.9 * r * math.sin(c)), fill=(250, 204, 92, 255), width=ss)
            else:
                d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(214, 232, 226, 255))
                d.ellipse((sx - r + 5 * ss, sy - r - 2 * ss, sx + r + 5 * ss, sy + r - 2 * ss), fill=CARD + (255,))
        lay = lay.resize((w, h), Image.LANCZOS)
        im.paste(lay, (x0, y0), lay)
        d = ImageDraw.Draw(im, "RGBA")
        f12 = font(13, "Bold")
        for hrs, lab in ((0, "8:30 AM"), (6, "2:30 PM"), (12, "8:30 PM"), (18, "2:30 AM")):
            b = math.radians(ang(hrs))
            text(d, (DC[0] + R_LAB * math.cos(b), DC[1] + R_LAB * math.sin(b) + 5), lab, f12, SOFT, "ms")
        n_rain = int(np.sum(D.at(tau) > 0))
        text(d, (DC[0], DC[1] + 8), f"{n_rain}", font(32, "Bold"), TEXT, "ms")
        text(d, (DC[0], DC[1] + 26), f"of {D.n} with rain", font(11, "Bold"), TEAL, "ms")
        if f > self.F_END:
            a = int(255 * min(1.0, (f - self.F_END) / 12))
            text(d, (DC[0], 1772), self.window_text(), font(17, "Bold"), CORAL + (a,), "ms")

    def window_text(self):
        if not self.window:
            return "No rain fell"
        i, j = self.window
        if j - i + 1 > 12:
            return "Rain was spread across the day"
        t = self.D.t0
        return f"Most of it fell {clock(t + timedelta(hours=i))} – {clock(t + timedelta(hours=j + 1))}"

    # ---- one map frame ------------------------------------------------------------
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
            fb = font(15, "Bold")
            bw = d.textlength(s, font=fb) + 20
            rr.rrect(d, (MAP[0] + 16, MAP[1] + 122, MAP[0] + 16 + bw, MAP[1] + 154), 6,
                     fill=(8, 26, 23, int(a * 0.85)), outline=col + (a,), width=2)
            text(d, (MAP[0] + 26, MAP[1] + 144), s, fb, col + (a,))
        self.inset(im, cam, min(1, zt * 1.5))
        if zt > 0.85 and k >= 1:
            into = (tau - (k - 1)) * (self.F_END - self.F_START) / 24       # frames into this hour
            self.popup(im, cam, k, min(1.0, into / 5) * min(1.0, (zt - 0.85) / 0.15))
        d = ImageDraw.Draw(im, "RGBA")
        self.clock_pill(d, tau, k)
        self.dial(im, tau, f)
        d = ImageDraw.Draw(im, "RGBA")
        self.panels(d, tau, k)
        return im

    def clock_pill(self, d, tau, k):
        t = self.D.t0 + timedelta(minutes=15 * math.floor(tau * 4 + 1e-6))
        x0, y0 = MAP[0] + 16, MAP[1] + 16          # top left: the full-state view leaves it empty
        rr.rrect(d, (x0, y0, x0 + 270, y0 + 96), 12, fill=(8, 26, 23, 215), outline=CARD_EDGE + (255,))
        text(d, (x0 + 22, y0 + 56), clock(t), font(46, "Bold"), TEXT)
        text(d, (x0 + 22, y0 + 82), f"{t:%a %d %b}".upper() + f"  ·  HOUR {k:02d} / 24", font(15, "Bold"), SOFT)

    def panels(self, d, tau, k):
        D = self.D
        # peak this hour
        if k >= 1 and D.n and D.H[k - 1].max() > 0:
            i = int(np.argmax(D.H[k - 1]))
            text(d, (602, 1436), f"{mm1(D.H[k - 1][i])} mm", font(40, "Bold"), CORAL, "ms")
            text(d, (602, 1468), fit(d, D.names[i], font(19, "Bold"), 265), font(19, "Bold"), TEXT, "ms")
            text(d, (602, 1492), D.dist[i], font(15), TEAL, "ms")
        else:
            text(d, (602, 1436), "–", font(40, "Bold"), CORAL, "ms")
            text(d, (602, 1474), "waiting for data" if k < 1 else "no rain this hour", font(17, "Bold"), SOFT, "ms")
        # highest hourly so far
        if k >= 1 and D.n and D.H[:k].max() > 0:
            h, i = np.unravel_index(np.argmax(D.H[:k]), D.H[:k].shape)
            text(d, (897, 1434), f"{mm1(D.H[h, i])} mm", font(36, "Bold"), TEXT, "ms")
            text(d, (897, 1466), fit(d, D.names[i], font(18, "Bold"), 265), font(18, "Bold"), TEXT, "ms")
            text(d, (897, 1490), f"{D.dist[i]}  ·  {clock(D.t0 + timedelta(hours=int(h) + 1))}", font(15), TEAL,
                 "ms")
        else:
            text(d, (897, 1434), "–", font(36, "Bold"), TEXT, "ms")
        # leaders so far
        now = D.at(tau)
        order = [i for i in np.argsort(-now)[:5] if now[i] > 0]
        top = now[order[0]] if order else 1
        f19 = font(19, "Bold")
        for r, i in enumerate(order):
            y = 1592 + 38 * r
            vs = mm1(now[i])
            vw = d.textlength(vs, font=f19)
            text(d, (480, y), f"{r + 1}   " + fit(d, D.names[i], f19, 500 - vw), f19, TEXT)
            text(d, (1020, y), vs, f19, TEXT, "rs")
            d.rectangle((480, y + 8, 480 + max(4, 540 * now[i] / top), y + 12), fill=colour(now[i]))

    # ---- sting and hook ----------------------------------------------------------
    def sting(self, f):
        """0.8 s: a coral drop falls and ripples; the wordmark comes up."""
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im, "RGBA")
        cx, ground = 540, 880
        t = f / (self.F_HOOK - 1)
        if f < 10:
            y = 640 + (ground - 640) * (f / 9) ** 2
            r = 22
            d.ellipse((cx - r, y - r, cx + r, y + r), fill=CORAL)
            d.polygon([(cx - r * 0.86, y - r * 0.5), (cx + r * 0.86, y - r * 0.5), (cx, y - 2.6 * r)], fill=CORAL)
        for q in range(3):
            s = (f - 9 - 4 * q) / 14
            if 0 < s <= 1:
                rx, ry = 40 + 260 * s, 10 + 64 * s
                d.ellipse((cx - rx, ground - ry, cx + rx, ground + ry), outline=CORAL + (int(255 * (1 - s)),),
                          width=3)
        a = int(255 * rr.ease((f - 7) / 10))
        if a > 0:
            text(d, (540, 1060), "MasRainman", font(96, "ExtraBold"), TEXT + (a,), "ms")
            text(d, (540, 1112), spaced("PAST 24 HOURS  ·  OBSERVED"), font(22, "Bold"), TEAL + (a,), "ms")
        return im

    def hook(self, f):
        """The answer first: headline, then the biggest 24-hour total counting up."""
        D = self.D
        t = f - self.F_HOOK
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im, "RGBA")
        if not D.n:
            return im
        i = int(np.argmax(D.total))
        v = float(D.total[i])
        hd = D.headline_district()
        cat = category(max(v for dd, v in D.district_max().items() if dd == hd))
        a1 = rr.ease(t / 8)
        text(d, (540, 300), spaced(f"{self.region['name'].upper()}  ·  PAST 24 HOURS"), font(20, "Bold"),
             CORAL + (int(255 * a1),), "ms")
        ft = font(76, file=self.R["native_font"])
        lines = rr.wrap(d, self.R["hook"](hd, cat), ft, 960)
        dy = int(30 * (1 - a1))
        for n, line in enumerate(lines[:3]):
            text(d, (540, 430 + 96 * n + dy), line, ft, TEXT + (int(255 * a1),), "ms")
        ye = 430 + 96 * (min(len(lines), 3) - 1)
        en = {"LIGHT": "Light rain", "MODERATE": "Moderate rain", "HEAVY": "Heavy rain",
              "VERY HEAVY": "Very heavy rain", "EXTREMELY HEAVY": "Extremely heavy rain"}.get(cat, "Rain") \
            + f" in {hd} district"
        text(d, (540, ye + 58 + dy), en, font(28), SOFT + (int(255 * a1),), "ms")
        # the number
        c = rr.ease_out((t - 14) / 34)
        if t >= 12:
            a2 = int(255 * rr.ease((t - 12) / 6))
            text(d, (540, 790), spaced("HIGHEST 24-HOUR TOTAL"), font(20, "Bold"), TEAL + (a2,), "ms")
            num = f"{v * c:.1f}"
            fn = font(230, "ExtraBold")
            nw = d.textlength(num, font=fn, features=FEAT)
            text(d, (540 - 40, 1010), num, fn, CORAL + (a2,), "ms")
            text(d, (540 - 40 + nw / 2 + 12, 1010), "mm", font(52, "Bold"), CORAL + (a2,))
        if t >= 40:
            a3 = int(255 * rr.ease((t - 40) / 8))
            text(d, (540, 1110), fit(d, D.names[i], font(50, "Bold"), 980), font(50, "Bold"), TEXT + (a3,), "ms")
            text(d, (540, 1158), f"{D.dist[i]} district", font(30), SOFT + (a3,), "ms")
            text(d, (540, 1214), f"24 hours ending {D.day.day} {D.day:%b %Y}, 8:30 AM IST", font(22), TEAL + (a3,),
                 "ms")
        if t >= 54:
            a4 = int(255 * rr.ease((t - 54) / 8))
            nudge = 10 * math.sin((t - 54) / 3)
            text(d, (540, 1480), "how it happened", font(34, "Bold"), CORAL + (a4,), "ms")
            ax = 540 + 150 + nudge
            d.line((ax, 1470, ax + 40, 1470), fill=CORAL + (a4,), width=4)
            d.polygon([(ax + 40, 1460), (ax + 56, 1470), (ax + 40, 1480)], fill=CORAL + (a4,))
        text(d, (540, 1880), f"{rr.BRAND}  ·  {rr.SITE}", font(18, "Bold"), TEAL, "ms")
        return im

    # ---- toppers card ---------------------------------------------------------
    def toppers(self, t):
        D = self.D
        im = self.head.copy()
        d = ImageDraw.Draw(im, "RGBA")
        order = [int(i) for i in np.argsort(-D.total)[:5] if D.total[i] > 0]
        a0 = int(255 * rr.ease(t / 8))
        text(d, (540, 340), spaced("RAINFALL TOPPERS"), font(50, "Bold"), TEXT + (a0,), "ms")
        text(d, (540, 380), f"Top 5 of {D.n:,} stations  ·  past 24 hours  ·  {self.R['source']} "
                            f"{self.R['network'].lower()}s", font(18), TEAL + (a0,), "ms")
        text(d, (540, 414), self.highlight(order), font(18, "Bold"), CORAL + (a0,), "ms")
        top = D.total[order[0]] if order else 1
        for r, i in enumerate(order):
            ra = min(1.0, max(0.0, (t - 6 - 7 * r) / 10))
            if ra <= 0:
                continue
            al = int(255 * ra)
            y0 = 450 + 200 * r + int(20 * (1 - ra))
            v = D.total[i]
            col = colour(v)
            if r == 0:
                d.rectangle((40, y0, 1040, y0 + 186), fill=(44, 52, 40, al))
                d.rectangle((40, y0, 44, y0 + 186), fill=CORAL + (al,))
            else:
                d.line((40, y0 - 7, 1040, y0 - 7), fill=CARD_EDGE + (al,))
            text(d, (66, y0 + 76), f"{r + 1:02d}", font(28, "Bold"), (CORAL if r == 0 else TEAL) + (al,))
            text(d, (140, y0 + 64), fit(d, D.names[i], font(36, "Bold"), 620), font(36, "Bold"), TEXT + (al,))
            text(d, (140, y0 + 100), f"{D.dist[i]}  ·  {self.R['network']}", font(19), TEAL + (al,))
            text(d, (1020, y0 + 80), mm1(v), font(54, "Bold"), TEXT + (al,), "rs")
            text(d, (1020, y0 + 106), "mm", font(16), TEAL + (al,), "rs")
            text(d, (1020, y0 + 134), spaced(category(v)), font(14, "Bold"), col + (al,), "rs")
            d.rectangle((140, y0 + 140, 880, y0 + 148), fill=DIM_BAR + (al,))
            d.rectangle((140, y0 + 140, 140 + 740 * (v / top) * rr.ease_out(ra), y0 + 148), fill=col + (al,))
        sa = min(1.0, max(0.0, (t - 44) / 10))
        if sa > 0:
            al = int(255 * sa)
            stats = [(f"{D.total.mean():.1f} mm" if D.n else "–", "STATION AVERAGE"),
                     (f"{int(np.sum(D.total >= HEAVY))}", "WITH HEAVY RAIN OR MORE"),
                     (f"{int(np.sum(D.total > 0))}", "STATIONS WITH RAIN")]
            for j, (val, lab) in enumerate(stats):
                x = 200 + 340 * j
                text(d, (x, 1520), val, font(46, "Bold"), TEXT + (al,), "ms")
                text(d, (x, 1552), spaced(lab), font(13, "Bold"), TEAL + (al,), "ms")
            text(d, (540, 1640), self.window_text(), font(26, "Bold"), CORAL + (al,), "ms")
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

    # ---- model scorecard -----------------------------------------------------------
    def scores(self):
        """Observed heavy-rain districts and each model's score. Falls back to moderate rain when no
        district had heavy rain."""
        if hasattr(self, "_scores"):
            return self._scores
        dm = self.D.district_max()
        thr, label = HEAVY, "heavy rain (≥ 64.5 mm"
        obs = sorted((d for d, v in dm.items() if v >= thr), key=lambda d: -dm[d])
        if not obs:
            thr, label = 15.6, "moderate rain (≥ 15.6 mm"
            obs = sorted((d for d, v in dm.items() if v >= thr), key=lambda d: -dm[d])
        rows = [(k, models.NAMES[k], models.score(self.fc.get(k), obs, thr)) for k in ("wn3", "ifs", "aifs")]
        have = [(k, s) for k, _, s in rows if s is not None]
        win = None
        if have and obs:
            best = max(have, key=lambda ks: (len(ks[1]["caught"]), -len(ks[1]["false"])))
            ties = [k for k, s in have if (len(s["caught"]), len(s["false"])) ==
                    (len(best[1]["caught"]), len(best[1]["false"]))]
            win = best[0] if len(ties) == 1 else "tie"
        self._scores = {"thr": thr, "label": label, "obs": obs, "rows": rows, "win": win}
        return self._scores

    def scorecard(self, t):
        S = self.scores()
        D = self.D
        im = self.head.copy()
        d = ImageDraw.Draw(im, "RGBA")
        a0 = int(255 * rr.ease(t / 8))
        run = models.run_for(D.day)
        run_ist = run.astimezone(tf.IST)
        text(d, (540, 340), spaced("DID THE MODELS GET IT RIGHT?"), font(44, "Bold"), TEXT + (a0,), "ms")
        text(d, (540, 380), f"24-hour rain forecast from the {run:%H} UTC run of {run.day} {run:%b} "
                            f"({clock(run_ist)} IST) vs the gauges", font(18), TEAL + (a0,), "ms")
        obs, n = S["obs"], len(S["obs"])
        msg = (f"Observed {S['label']} at a gauge) in {n} district{'s' if n != 1 else ''}" if n else
               "No district had moderate or heavy rain: nothing to score")
        text(d, (540, 428), msg, font(22, "Bold"), SOFT + (a0,), "ms")
        for r, (key, name, sc) in enumerate(S["rows"]):
            ra = rr.ease((t - 6 - 10 * r) / 10)
            if ra <= 0:
                continue
            al = int(255 * ra)
            y0 = 470 + 296 * r
            win = S["win"] == key and t >= 80
            rr.rrect(d, (40, y0, 1040, y0 + 280), 14, fill=CARD + (al,),
                     outline=(CORAL if win else CARD_EDGE) + (al,), width=3 if win else 1)
            text(d, (70, y0 + 58), name, font(34, "Bold"), TEXT + (al,))
            if sc is None:
                text(d, (70, y0 + 88), "WeatherNext 3 data was not available to this report" if key == "wn3"
                     else "Open data for this run was not available", font(16), TEAL + (al,))
                text(d, (540, y0 + 190), "no forecast", font(30, "Bold"), (90, 130, 120, al), "ms")
                continue
            text(d, (70, y0 + 88), "maximum inside each district vs the highest gauge there", font(16), TEAL + (al,))
            c = len(sc["caught"])
            bt = rr.ease_out((t - 14 - 14 * r) / 22)
            shown = round(c * max(0.0, bt))
            fb = font(64, "Bold")
            tail = f" / {n}"
            text(d, (1010, y0 + 80), tail, font(30, "Bold"), SOFT + (al,), "rs")
            text(d, (1010 - d.textlength(tail, font=font(30, "Bold")), y0 + 80), f"{shown}", fb, CORAL + (al,), "rs")
            text(d, (1010, y0 + 106), "districts caught", font(15), TEAL + (al,), "rs")
            d.rectangle((70, y0 + 128, 1010, y0 + 144), fill=DIM_BAR + (al,))
            if n:
                d.rectangle((70, y0 + 128, 70 + 940 * c / n * max(0.0, bt), y0 + 144), fill=CORAL + (al,))
            # district chips: filled = caught, outlined = missed
            x, y = 70, y0 + 162
            fc = font(16, "Bold")
            ca = int(al * min(1.0, max(0.0, (t - 20 - 14 * r) / 10)))
            for dn in obs:
                wdt = d.textlength(dn, font=fc) + 26
                if x + wdt > 1010:
                    x, y = 70, y + 40
                    if y > y0 + 210:
                        break
                hit = dn in sc["caught"]
                rr.rrect(d, (x, y, x + wdt, y + 32), 16, fill=(CORAL + (ca,)) if hit else None,
                         outline=(CORAL if hit else (70, 120, 108)) + (ca,), width=2)
                text(d, (x + 13, y + 23), dn, fc, ((20, 30, 28) if hit else SOFT) + (ca,))
                x += wdt + 10
            hr_ = f"hit rate {100 * c / n:.0f}%" if n else "hit rate –"
            fa = len(sc["false"])
            text(d, (70, y0 + 268), f"{hr_}  ·  {fa} false alarm{'s' if fa != 1 else ''} "
                                    f"(forecast {S['label'].split(' (')[0]}, none observed)", font(15),
                 TEAL + (ca,))
        if t >= 80:
            s = rr.ease_out((t - 80) / 10)
            al = int(255 * min(1.0, (t - 80) / 6))
            y = 1420
            if S["win"] is None:
                text(d, (540, y + 60), "No model forecasts to score today" if obs else "", font(30, "Bold"),
                     SOFT + (al,), "ms")
            else:
                text(d, (540, y + 10), spaced("TODAY'S WINNER"), font(18, "Bold"), TEAL + (al,), "ms")
                name = "It's a tie" if S["win"] == "tie" else models.NAMES[S["win"]]
                fw = font(int(56 * (0.7 + 0.3 * s)), "ExtraBold")
                pw = d.textlength(name, font=fw) + 70
                rr.rrect(d, (540 - pw / 2, y + 30, 540 + pw / 2, y + 120), 45, fill=CORAL + (al,))
                text(d, (540, y + 95), name, fw, BG + (al,), "ms")
                if S["win"] != "tie":
                    sc = dict((k, s_) for k, _, s_ in S["rows"])[S["win"]]
                    text(d, (540, y + 168), f"caught {len(sc['caught'])} of {n} districts with "
                                            f"{len(sc['false'])} false alarm{'s' if len(sc['false']) != 1 else ''}",
                         font(22), SOFT + (al,), "ms")
            text(d, (540, 1680), "Forecast maximum inside each district compared with the highest gauge there.",
                 font(15), TEAL + (al,), "ms")
            text(d, (540, 1702), "Not an official verification.", font(15), TEAL + (al,), "ms")
        return im

    def caption(self):
        D = self.D
        d0, d1 = D.t0.date(), D.day
        order = [int(i) for i in np.argsort(-D.total)[:5] if D.total[i] > 0]
        lines = [f"🌧 {self.region['name']} · Past 24 hours observed rainfall",
                 f"8:30 am {d0.day} {d0:%b} to 8:30 am {d1.day} {d1:%b %Y} · hour by hour", ""]
        if D.n:
            hd = D.headline_district()
            lines += [self.R["hook"](hd, category(D.district_max()[hd])), ""]
        lines.append("Rainfall toppers:")
        for r, i in enumerate(order, 1):
            lines.append(f"{r}. {D.names[i]} ({D.dist[i]}) – {mm1(D.total[i])} mm")
        if D.n and D.H.max() > 0:
            h, i = np.unravel_index(np.argmax(D.H), D.H.shape)
            lines += ["", f"Heaviest in one hour: {mm1(D.H[h, i])} mm at {D.names[i]} ({D.dist[i]}), "
                          f"hour ending {clock(D.t0 + timedelta(hours=int(h) + 1))}", self.window_text()]
        lines += [f"Rain at {int(np.sum(D.total > 0))} of {D.n} stations · "
                  f"{int(np.sum(D.total >= HEAVY))} with heavy rain or more"]
        S = self.scores()
        if S["obs"]:
            lines += ["", f"Model check ({S['label']}) at a gauge, {len(S['obs'])} districts):"]
            for k, name, sc in S["rows"]:
                lines.append(f"  {name}: " + ("no forecast" if sc is None else
                                              f"{len(sc['caught'])} caught, {len(sc['false'])} false alarms"))
        lines += ["", f"Source: {self.R['source']} · forecasts: ECMWF open data (CC BY 4.0)",
                  f"#{rr.BRAND} · {rr.SITE}"]
        return "\n".join(lines)

    # ---- render -----------------------------------------------------------------
    def at_frame(self, f):
        """The picture for any frame number (map frames are cached by the caller)."""
        if f < self.F_HOOK:
            return self.sting(f)
        if f < self.F_WIPE[0]:
            im = self.hook(f)
            if f < self.F_HOOK + 6:
                im = Image.blend(self.sting(self.F_HOOK - 1), im, (f - self.F_HOOK + 1) / 6)
            return im
        if f < self.F_MAP:
            a, b = self.F_WIPE
            x = int(W * rr.ease((f - a + 1) / (b - a)))
            im = self.hook(f)
            if not hasattr(self, "_first"):
                self._first = self.frame(self.F_MAP)
            first = self._first
            im.paste(first.crop((0, 0, x, H)), (0, 0))
            ImageDraw.Draw(im).rectangle((x - 3, 0, x + 3, H), fill=CORAL)
            return im
        return self.frame(f)

    def render(self, base):
        wr = rr.Writer(base + ".mp4", size=(W, H))
        last = None
        for f in range(self.F_TOP):
            if f <= self.F_END + 13:              # after that only the hold: the picture does not change
                last = self.at_frame(f)
            wr.add(last)
            if f == self.F_END:
                last.save(base + ".png", optimize=True)
        prev = last
        for t in range(self.F_SCORE - self.F_TOP):
            im = self.toppers(t)
            if t < 8:
                im = Image.blend(prev, im, (t + 1) / 8)
            wr.add(im)
        prev = im
        for t in range(self.N - self.F_SCORE):
            im = self.scorecard(t)
            if t < 8:
                im = Image.blend(prev, im, (t + 1) / 8)
            wr.add(im)
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
    print("  model forecasts …")
    fc = models.forecasts(day, geo)
    tag = "" if region_key == "tn" else f"{region_key}_"
    base = os.path.join(out_dir, f"rain_{tag}live24_{day.isoformat()}")
    V = Live24(region_key, D, geo, fc)
    print("Rendering …")
    wr = V.render(base)
    wr.close()
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(V.caption())
    print(f"  video : {base}.mp4  ({wr.n / FPS:.1f} s)")
    return base
