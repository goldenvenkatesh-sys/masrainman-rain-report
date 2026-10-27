"""
Satellite rain from NASA/JAXA GPM, once a day, for Tamil Nadu, Karnataka and Andhra Pradesh.

Two reports per state (English), saved in out/ with the same file names the other reports use,
so publish_reports.sh puts them on the website:

  sat24  "Satellite rain, past 24 hours": IMERG Early (half-hourly, 0.1°, all satellites blended).
         The 48 newest half-hours are added up. MP4 (the map fills half-hour by half-hour), PNG, caption.
         Files: rain_sat24_<end, IST>_en  /  rain_ka_sat24_…  /  rain_ap_sat24_…
  gpm3d  "Satellite radar, 3D": each pass of the GPM radar (Ku band, 2A.Ku) over the state in the
         last 24 hours with rain under it. Rain is drawn in 3D (height up to about 18 km), coloured by
         rain rate, then by rain / mixed / snow and ice. MP4 (camera turns around), PNG, caption.
         Files: rain_gpm3d_<pass time, IST>_en  /  rain_ka_gpm3d_…  /  rain_ap_gpm3d_…

Data: NASA GES DISC, found with NASA's CMR search. Needs a free NASA Earthdata login in the
environment: EARTHDATA_USER and EARTHDATA_PASS.

  python gpm_sat.py                         # both reports for tn ka ap
  python gpm_sat.py --what sat24 --region "tn"
  python gpm_sat.py --demo                  # made-up data, to check the pictures without NASA
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
from matplotlib import font_manager                  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
RAW = os.path.join(HERE, "data", "raw", "gpm")
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
IMERG = "GPM_3IMERGHHE"          # IMERG Early run, half-hourly (about 4 hours behind)
RADAR = "GPM_2AKu"               # GPM radar, Ku band, 3D rain profile

REGIONS = {  # key: (file tag, name, districts file)
    "tn": ("", "Tamil Nadu", "tn_districts.geojson"),
    "ka": ("ka_", "Karnataka", "ka_districts.geojson"),
    "ap": ("ap_", "Andhra Pradesh", "ap_districts.geojson"),
}
OTHER_STATES = ["tn_districts.geojson", "ka_districts.geojson", "ap_districts.geojson", "kl_districts.geojson"]

W, H = 1080, 1350
FPS = 20
BG = "#0b0e14"
TEXT = "#eceff4"
SOFT = "#c6ccd6"
MUTED = "#8a92a0"
GOLD = "#e8a33d"
EDGE = "#3a4150"

# 24-hour totals: same colours as the station reports (mm, log-ish)
STOPS = [(0.1, "#24406a"), (1, "#2c4a72"), (2.5, "#2f6f96"), (5, "#2a8f9c"), (10, "#2fa68f"),
         (15.6, "#69bb72"), (25, "#b5cc55"), (40, "#e3c94a"), (64.5, "#f0a23c"), (100, "#e0503e"),
         (150, "#c73a7c"), (204.5, "#b45cb8"), (300, "#efd9f5")]
TICKS24 = [1, 2.5, 5, 10, 25, 50, 100, 200]
CATS = [(0.1, "Light"), (15.6, "Moderate"), (64.5, "Heavy"), (115.6, "Very heavy"), (204.5, "Extremely heavy")]

# rain rate in the 3D view (mm/h), like JAXA's scale
RATE_B = [0.1, 0.5, 1, 2, 3, 5, 10, 15, 20, 25, 1000]
RATE_C = ["#1a1aa8", "#1f5cff", "#29a3ff", "#26d6c8", "#4fd94f", "#e6e633", "#ffb31a", "#ff7a1a", "#f03c1e", "#a30f14"]
RATE_CMAP = ListedColormap(RATE_C)
RATE_NORM = BoundaryNorm(RATE_B, RATE_CMAP.N)
PHASE_C = {0: "#f2f4f8", 1: "#5fd0ff", 2: "#2035ff"}          # 0 snow/ice, 1 mixed, 2 rain
PHASE_NAME = {2: "Rain", 1: "Mixed rain and snow", 0: "Snow and ice"}


# ----------------------------------------------------------------- fonts, logo, small helpers
def _fonts():
    fam = "DejaVu Sans"
    for fn in ("MuktaMalar-Regular.ttf", "MuktaMalar-SemiBold.ttf", "MuktaMalar-Bold.ttf", "MuktaMalar-ExtraBold.ttf"):
        p = os.path.join(HERE, "fonts", fn)
        if os.path.exists(p):
            font_manager.fontManager.addfont(p)
            fam = font_manager.FontProperties(fname=p).get_name()
    plt.rcParams["font.family"] = [fam, "DejaVu Sans"]


_fonts()


def put_logo(fig, height=92, pad=34):
    p = os.path.join(HERE, "assets", "masrainman_logo_dark.png")
    if not os.path.exists(p):
        return
    from PIL import Image
    im = Image.open(p).convert("RGBA")
    im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    fig.figimage(np.asarray(im), xo=W - im.width - pad, yo=H - height - 22, zorder=10)


def hex2rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


_L0, _L1 = math.log10(0.1), math.log10(300)


def ramp24(mm):
    """mm (array) → RGBA, transparent below 0.1 mm."""
    xs = np.array([math.log10(v) for v, _ in STOPS])
    cs = np.array([hex2rgb(c) for _, c in STOPS])
    t = np.log10(np.clip(mm, 0.1, 300))
    rgba = np.zeros(mm.shape + (4,))
    for i in range(3):
        rgba[..., i] = np.interp(t, xs, cs[:, i])
    rgba[..., 3] = np.where(mm >= 0.1, 0.95, 0.0)
    return rgba


def t_ist(t):
    return t.astimezone(IST).strftime("%I:%M %p %d %b").lstrip("0").replace(" 0", " ")


def load_geo(fn):
    p = os.path.join(HERE, "data", fn)
    if not os.path.exists(p):
        return []
    from shapely.geometry import shape
    out = []
    for f in json.load(open(p, encoding="utf-8"))["features"]:
        pr = f.get("properties") or {}
        name = pr.get("district") or pr.get("DISTRICT") or pr.get("name") or pr.get("dtname") or "?"
        out.append((str(name).title(), shape(f["geometry"])))
    return out


def rings(geom):
    polys = getattr(geom, "geoms", [geom])
    for p in polys:
        if hasattr(p, "exterior"):
            yield np.asarray(p.exterior.coords)


def box_for(geo, pad=0.45):
    from shapely.ops import unary_union
    b = unary_union([g for _, g in geo]).bounds
    return (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)


class Video:
    """Raw RGB frames → H.264 MP4 through ffmpeg."""

    def __init__(self, path, w=W, h=H, fps=FPS):
        exe = shutil.which("ffmpeg")
        if not exe:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.p = subprocess.Popen([exe, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                   "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                                   "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "veryfast",
                                   "-movflags", "+faststart", path], stdin=subprocess.PIPE)

    def add(self, fig, times=1):
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())[..., :3].tobytes()
        for _ in range(times):
            self.p.stdin.write(buf)

    def close(self):
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg failed")


# ----------------------------------------------------------------- NASA access
class EarthdataSession:
    """requests session that keeps the NASA login across GES DISC's redirect to urs.earthdata.nasa.gov."""

    def __init__(self):
        import requests
        user, pw = os.environ.get("EARTHDATA_USER"), os.environ.get("EARTHDATA_PASS")
        if not user or not pw:
            raise SystemExit("NASA Earthdata login missing: set the EARTHDATA_USER and EARTHDATA_PASS secrets.")

        class S(requests.Session):
            AUTH_HOST = "urs.earthdata.nasa.gov"

            def rebuild_auth(self, prepared, response):
                if "Authorization" in prepared.headers:
                    a, b = urlparse(response.request.url).hostname, urlparse(prepared.url).hostname
                    if a != b and self.AUTH_HOST not in (a, b):
                        del prepared.headers["Authorization"]

        self.s = S()
        self.s.auth = (user, pw)

    def get(self, url, dest):
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        for attempt in range(3):
            try:
                with self.s.get(url, stream=True, timeout=180) as r:
                    if r.status_code == 401:
                        raise SystemExit("NASA Earthdata login was refused (HTTP 401): check EARTHDATA_USER / "
                                         "EARTHDATA_PASS, and that 'NASA GESDISC DATA ARCHIVE' is approved in the "
                                         "Earthdata profile (Applications → Authorized Apps).")
                    r.raise_for_status()
                    if "text/html" in r.headers.get("content-type", ""):
                        raise RuntimeError("got a web page instead of data (login not accepted?)")
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                os.replace(tmp, dest)
                return dest
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"    download failed ({e}); try {attempt + 1}/3")
        raise RuntimeError("could not download " + url)


def cmr_search(short_name, t0, t1, bbox=None):
    """Granules (title, start, end, https data link), oldest first; newest version per start time."""
    import requests
    params = {"short_name": short_name, "page_size": 200, "sort_key": "start_date",
              "temporal": f"{t0:%Y-%m-%dT%H:%M:%SZ},{t1:%Y-%m-%dT%H:%M:%SZ}"}
    if bbox:
        params["bounding_box"] = ",".join(f"{v:.2f}" for v in bbox)
    out, page = {}, 1
    while True:
        r = requests.get(CMR, params={**params, "page_num": page}, timeout=60)
        r.raise_for_status()
        ents = r.json()["feed"]["entry"]
        for e in ents:
            link = next((l["href"] for l in e.get("links", [])
                         if l.get("rel", "").endswith("/data#") and l["href"].startswith("https")
                         and not l["href"].endswith((".xml", ".html"))), None)
            if not link:
                continue
            st = datetime.fromisoformat(e["time_start"].replace("Z", "+00:00"))
            en = datetime.fromisoformat(e["time_end"].replace("Z", "+00:00"))
            old = out.get(st)
            if old is None or e["title"] > old[0]:      # V07B > V07A …
                out[st] = (e["title"], st, en, link)
        if len(ents) < params["page_size"]:
            break
        page += 1
    return [out[k] for k in sorted(out)]


# ----------------------------------------------------------------- IMERG: past 24 hours
def imerg_read(path, box):
    import h5py
    with h5py.File(path, "r") as f:
        g = f["Grid"]
        lat, lon = g["lat"][:], g["lon"][:]
        var = "precipitation" if "precipitation" in g else "precipitationCal"
        i0, i1 = np.searchsorted(lon, box[0]), np.searchsorted(lon, box[2])
        j0, j1 = np.searchsorted(lat, box[1]), np.searchsorted(lat, box[3])
        p = g[var][0, i0:i1, j0:j1].astype("f4").T        # (lat, lon)
    p[~np.isfinite(p) | (p < 0)] = np.nan
    return lat[j0:j1], lon[i0:i1], p


def imerg_24h(sess, box):
    now = datetime.now(UTC)
    grans = cmr_search(IMERG, now - timedelta(hours=34), now)
    if not grans:
        raise RuntimeError("no IMERG Early files found for the last 34 hours")
    last_end = grans[-1][2]
    start = last_end - timedelta(hours=24)
    use = [g for g in grans if g[1] >= start - timedelta(minutes=1)]
    print(f"  IMERG Early: {len(use)} half-hours, {use[0][1]:%d %b %H:%M} → {last_end:%d %b %H:%M} UTC")
    if len(use) < 40:
        raise RuntimeError(f"only {len(use)} of 48 half-hours available")

    def one(g):
        return imerg_read(sess.get(g[3], os.path.join(RAW, "imerg", os.path.basename(urlparse(g[3]).path))), box)

    with cf.ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(one, use))
    lat, lon = parts[0][0], parts[0][1]
    steps = np.stack([p for _, _, p in parts]) * 0.5         # mm/h × half an hour → mm
    return dict(lat=lat, lon=lon, steps=steps, t0=use[0][1], t1=last_end, n=len(use))


def district_stats(geo, lat, lon, total):
    from shapely import contains_xy
    X, Y = np.meshgrid(lon, lat)
    rows = []
    for name, g in geo:
        m = contains_xy(g, X, Y) & np.isfinite(total)
        if not m.any():                      # tiny district: nearest grid cell to its centre
            c = g.representative_point()
            j, i = np.abs(lat - c.y).argmin(), np.abs(lon - c.x).argmin()
            m = np.zeros_like(total, bool)
            m[j, i] = True
        rows.append((name, float(np.nanmean(total[m])), float(np.nanmax(total[m]))))
    return sorted(rows, key=lambda r: -r[1])


def category(mm):
    c = "No rain"
    for lo, n in CATS:
        if mm >= lo:
            c = n
    return c


def draw_map_base(ax, box, region_geo, others):
    ax.set_facecolor("#0f1520")
    for fn, geo in others:
        for _, g in geo:
            for r in rings(g):
                ax.plot(r[:, 0], r[:, 1], color="#4a5263", lw=0.6, zorder=3)
    for _, g in region_geo:
        for r in rings(g):
            ax.plot(r[:, 0], r[:, 1], color="#aab3c2", lw=0.9, zorder=4)
    from shapely.ops import unary_union
    for r in rings(unary_union([g for _, g in region_geo])):
        ax.plot(r[:, 0], r[:, 1], color=TEXT, lw=2.0, zorder=5)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect(1 / math.cos(math.radians((box[1] + box[3]) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(EDGE)


def colourbar24(fig):
    cax = fig.add_axes([0.08, 0.075, 0.84, 0.022])
    x = np.logspace(_L0, _L1, 600)
    cax.imshow(ramp24(x)[None, :, :3], aspect="auto", extent=[_L0, _L1, 0, 1])
    cax.set_yticks([])
    cax.set_xticks([math.log10(v) for v in TICKS24])
    cax.set_xticklabels([f"{v:g}" for v in TICKS24], color=SOFT, fontsize=15)
    cax.tick_params(length=0, pad=6)
    for s in cax.spines.values():
        s.set_visible(False)
    fig.text(0.5, 0.032, "Rain in 24 hours (mm)  ·  Source: NASA/JAXA GPM IMERG Early (satellite estimate)",
             ha="center", color=MUTED, fontsize=14)


def make_sat24(key, data, out_dir):
    tag, name, gfile = REGIONS[key]
    geo = load_geo(gfile)
    others = [(f, load_geo(f)) for f in OTHER_STATES if f != gfile]
    box = box_for(geo)
    lat, lon, steps = data["lat"], data["lon"], data["steps"]
    jm = (lat >= box[1] - 0.1) & (lat <= box[3] + 0.1)
    im_ = (lon >= box[0] - 0.1) & (lon <= box[2] + 0.1)
    lat, lon, steps = lat[jm], lon[im_], steps[:, jm][:, :, im_]
    cum = np.nancumsum(steps, axis=0)
    total = cum[-1]
    stats = district_stats(geo, lat, lon, total)
    top = stats[:5]
    ext = [lon[0] - 0.05, lon[-1] + 0.05, lat[0] - 0.05, lat[-1] + 0.05]
    t0, t1 = data["t0"], data["t1"]
    end_tag = t1.astimezone(IST).strftime("%Y-%m-%d_%H%M")
    base = f"rain_{tag}sat24_{end_tag}_en"

    from scipy.ndimage import zoom
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
    fig.text(0.06, 0.955, "SATELLITE RAINFALL", color=GOLD, fontsize=20, weight="bold")
    fig.text(0.06, 0.918, f"{name} · past 24 hours", color=TEXT, fontsize=34, weight="bold")
    fig.text(0.06, 0.888, f"{t_ist(t0)} → {t_ist(t1)} IST", color=SOFT, fontsize=19)
    put_logo(fig)
    ax = fig.add_axes([0.06, 0.335, 0.88, 0.535])
    draw_map_base(ax, box, geo, others)
    layer = ax.imshow(np.zeros((2, 2, 4)), extent=ext, origin="lower", zorder=2, interpolation="bilinear")
    clock = ax.text(0.02, 0.97, "", transform=ax.transAxes, color=TEXT, fontsize=18, va="top",
                    bbox=dict(boxstyle="round,pad=0.35", fc="#0b0e14cc", ec=EDGE), zorder=8)
    colourbar24(fig)
    tbl = fig.text(0.06, 0.288, "", color=TEXT, fontsize=17, va="top", linespacing=1.05)
    fig.text(0.06, 0.298, "Wettest districts (average over the district)", color=GOLD, fontsize=16, va="bottom")

    def set_step(k):
        f = np.nan_to_num(cum[k])
        layer.set_data(ramp24(np.clip(zoom(f, 4, order=1), 0, None)))
        tk = t0 + timedelta(minutes=30 * (k + 1))
        clock.set_text(f"up to {t_ist(tk)}")

    rows = [f"{i + 1}. {n} — {m:.1f} mm avg, {x:.0f} mm max ({category(m)})" for i, (n, m, x) in enumerate(top)]
    vid = Video(os.path.join(out_dir, base + ".mp4"))
    for k in range(len(cum)):
        set_step(k)
        vid.add(fig, 2 if k < len(cum) - 1 else 1)
    set_step(len(cum) - 1)
    for i in range(len(rows)):
        tbl.set_text("\n".join(rows[:i + 1]))
        vid.add(fig, 8)
    vid.add(fig, FPS * 4)
    vid.close()
    fig.savefig(os.path.join(out_dir, base + ".png"), facecolor=BG)
    plt.close(fig)

    wet = [s for s in stats if s[1] >= 15.6]
    lead = (f"{len(wet)} districts had moderate or heavier rain on average; wettest {top[0][0]} at {top[0][1]:.1f} mm."
            if wet else "No district had moderate rain on average.")
    cap = [f"🛰 Satellite Rainfall · {name}", f"Past 24 hours · {t_ist(t0)} to {t_ist(t1)} IST", "", lead, "",
           "Wettest districts (average, max):"]
    cap += [f"{i + 1}. {n} – {m:.1f} mm (max {x:.0f} mm)" for i, (n, m, x) in enumerate(top)]
    cap += ["", "Satellite estimate, not gauge readings. Source: NASA/JAXA GPM IMERG Early",
            "#MasRainman · masrainman.blogspot.com"]
    open(os.path.join(out_dir, base + ".txt"), "w", encoding="utf-8").write("\n".join(cap) + "\n")
    print("  made", base)
    return base


# ----------------------------------------------------------------- GPM radar 3D passes
def radar_read(path):
    import h5py
    with h5py.File(path, "r") as f:
        sw = "FS" if "FS" in f else "NS"
        g = f[sw]
        lat, lon = g["Latitude"][:], g["Longitude"][:]
        rate = g["SLV/precipRate"][:].astype("f4")
        surf = g["SLV/precipRateNearSurface"][:].astype("f4")
        phase = g["DSD/phase"][:] if "DSD/phase" in g else None
        st = g["ScanTime"]
        times = [datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=UTC)
                 for y, mo, d, h, mi, s in zip(st["Year"][:], st["Month"][:], st["DayOfMonth"][:],
                                               st["Hour"][:], st["Minute"][:], st["Second"][:])]
        nb = rate.shape[2]
        if "PRE/height" in g:
            hgt = g["PRE/height"][:].astype("f4") / 1000.0
        else:  # bins are 125 m apart; the last bin is at the ellipsoid
            zen = g["PRE/localZenithAngle"][:] if "PRE/localZenithAngle" in g else np.zeros(lat.shape)
            hgt = ((nb - 1 - np.arange(nb))[None, None, :] * 0.125 * np.cos(np.radians(zen))[..., None]).astype("f4")
    rate[rate < 0] = 0
    surf[surf < 0] = 0
    return dict(swath=sw, lat=lat, lon=lon, rate=rate, surf=surf, phase=phase, hgt=hgt, times=times)


def pass_cut(d, box, pad=1.0):
    """The part of a pass over the box: scans with any ray inside (box + pad)."""
    inside = ((d["lon"] >= box[0] - pad) & (d["lon"] <= box[2] + pad) &
              (d["lat"] >= box[1] - pad) & (d["lat"] <= box[3] + pad))
    scans = np.where(inside.any(axis=1))[0]
    if len(scans) < 5:
        return None
    s = slice(scans[0], scans[-1] + 1)
    c = {k: (v[s] if isinstance(v, np.ndarray) else v) for k, v in d.items() if k != "times"}
    c["times"] = d["times"][s]
    c["inside"] = inside[s]
    return c


def make_gpm3d(key, c, out_dir):
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    tag, name, gfile = REGIONS[key]
    geo = load_geo(gfile)
    others = [load_geo(f) for f in OTHER_STATES if f != gfile]
    tmid = c["times"][len(c["times"]) // 2]
    base = f"rain_{tag}gpm3d_{tmid.astimezone(IST):%Y-%m-%d_%H%M}_en"

    lat, lon, rate, hgt = c["lat"], c["lon"], c["rate"], c["hgt"]
    m = (rate >= 0.2) & (hgt > 0) & (hgt < 19)
    LAT = np.broadcast_to(lat[..., None], rate.shape)[m]
    LON = np.broadcast_to(lon[..., None], rate.shape)[m]
    Z, R = hgt[m], rate[m]
    PH = (c["phase"][m] // 100).astype(int) if c["phase"] is not None else None
    if PH is not None:
        ok = PH <= 2
        LAT, LON, Z, R, PH = LAT[ok], LON[ok], Z[ok], R[ok], PH[ok]
    n = len(R)
    cap_n = 70000
    if n > cap_n:            # keep the heavier rain, thin the light rain
        rng = np.random.default_rng(1)
        keep = (R >= 5) | (rng.random(n) < (cap_n - (R >= 5).sum()) / max((R < 5).sum(), 1))
        LAT, LON, Z, R = LAT[keep], LON[keep], Z[keep], R[keep]
        PH = PH[keep] if PH is not None else None
    order = np.argsort(R)    # heavy rain drawn last (on top)
    LAT, LON, Z, R = LAT[order], LON[order], Z[order], R[order]
    PH = PH[order] if PH is not None else None

    x0, x1 = float(np.nanmin(lon)), float(np.nanmax(lon))
    y0, y1 = float(np.nanmin(lat)), float(np.nanmax(lat))
    kx = math.cos(math.radians((y0 + y1) / 2))
    surf = c["surf"]
    sm = surf >= 0.2

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
    fig.text(0.06, 0.955, "SATELLITE RADAR · 3D", color=GOLD, fontsize=20, weight="bold")
    fig.text(0.06, 0.918, f"{name} · GPM pass", color=TEXT, fontsize=34, weight="bold")
    fig.text(0.06, 0.888, f"{t_ist(tmid)} IST  ({tmid:%H:%M} UTC)", color=SOFT, fontsize=19)
    put_logo(fig)
    ax = fig.add_axes([-0.06, 0.13, 1.12, 0.78], projection="3d", facecolor=BG)
    ax.set_box_aspect(((x1 - x0) * kx, (y1 - y0), 0.42 * max((x1 - x0) * kx, y1 - y0)), zoom=1.2)

    def outlines():
        for geo_ in others + [geo]:
            lw, col = (1.4, "#dfe4ec") if geo_ is geo else (0.6, "#5a6375")
            for _, g in geo_:
                for r in rings(g):
                    ok = (r[:, 0] > x0 - 0.5) & (r[:, 0] < x1 + 0.5) & (r[:, 1] > y0 - 0.5) & (r[:, 1] < y1 + 0.5)
                    if ok.any():
                        rr = r.copy()
                        rr[~ok] = np.nan
                        ax.plot(rr[:, 0], rr[:, 1], 0, color=col, lw=lw, zorder=1)
        for e in (0, -1):    # swath edges
            ax.plot(lon[:, e], lat[:, e], 0, color="#6f86b8", lw=1.0, ls="--")

    def setup():
        ax.cla()
        ax.set_facecolor(BG)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_zlim(0, 18)
        for a in (ax.xaxis, ax.yaxis, ax.zaxis):
            a.set_pane_color((0.06, 0.08, 0.12, 1.0))
            a._axinfo["grid"]["color"] = (0.25, 0.28, 0.34, 0.5)
        ax.tick_params(colors=MUTED, labelsize=11)
        ax.set_zlabel("km", color=MUTED, fontsize=12)
        outlines()

    leg = fig.add_axes([0.08, 0.075, 0.84, 0.022])
    foot = fig.text(0.5, 0.032, "", ha="center", color=MUTED, fontsize=14)

    def legend_rate():
        leg.cla()
        leg.imshow(np.arange(len(RATE_C))[None, :], cmap=RATE_CMAP, aspect="auto", extent=[0, len(RATE_C), 0, 1])
        leg.set_yticks([])
        leg.set_xticks(range(len(RATE_C)))
        leg.set_xticklabels([f"{v:g}" for v in RATE_B[:-1]], color=SOFT, fontsize=14)
        leg.tick_params(length=0, pad=6)
        for s in leg.spines.values():
            s.set_visible(False)
        foot.set_text("Rain rate (mm/h)  ·  Source: NASA/JAXA GPM radar (Ku band, 2A.Ku)")

    def legend_phase():
        leg.cla()
        cols = [PHASE_C[2], PHASE_C[1], PHASE_C[0]]
        leg.imshow(np.array([[0, 1, 2]]), cmap=ListedColormap(cols), aspect="auto", extent=[0, 3, 0, 1])
        leg.set_yticks([])
        leg.set_xticks([0.5, 1.5, 2.5])
        leg.set_xticklabels([PHASE_NAME[2], PHASE_NAME[1], PHASE_NAME[0]], color=SOFT, fontsize=15)
        leg.tick_params(length=0, pad=6)
        for s in leg.spines.values():
            s.set_visible(False)
        foot.set_text("What is falling  ·  Source: NASA/JAXA GPM radar (Ku band, 2A.Ku)")

    def scene(by_phase):
        setup()
        ax.scatter(lon[sm], lat[sm], np.zeros(sm.sum()), c=surf[sm], cmap=RATE_CMAP, norm=RATE_NORM,
                   s=10, marker="s", alpha=0.55, depthshade=False, linewidths=0)
        if by_phase and PH is not None:
            col = np.array([hex2rgb(PHASE_C[p]) for p in (0, 1, 2)])[PH]
            ax.scatter(LON, LAT, Z, c=col, s=2.2, alpha=0.45, depthshade=False, linewidths=0)
        else:
            ax.scatter(LON, LAT, Z, c=R, cmap=RATE_CMAP, norm=RATE_NORM, s=2.2, alpha=0.5,
                       depthshade=False, linewidths=0)

    vid = Video(os.path.join(out_dir, base + ".mp4"))
    legend_rate()
    scene(False)
    n1 = FPS * 6
    for i in range(n1):
        ax.view_init(elev=26 + 6 * math.sin(i / n1 * math.pi), azim=-75 + 95 * i / n1)
        vid.add(fig)
        if i == n1 // 2:
            fig.savefig(os.path.join(out_dir, base + ".png"), facecolor=BG)
    if PH is not None:
        legend_phase()
        scene(True)
        n2 = FPS * 5
        for i in range(n2):
            ax.view_init(elev=18, azim=20 - 70 * i / n2)
            vid.add(fig)
    vid.close()
    plt.close(fig)

    # numbers for the caption: the part of the pass over the state's box
    ins = c["inside"]
    s_in = surf[ins]
    top_rate = float(np.nanmax(s_in)) if s_in.size else 0.0
    top_h = float(np.nanmax(Z[R >= 0.5])) if (R >= 0.5).any() else 0.0
    j, i = np.unravel_index(np.argmax(np.where(ins, surf, -1)), surf.shape)
    where = nearest_district(lat[j, i], lon[j, i], geo, others)
    cap = [f"🛰 Satellite Radar 3D · {name}", f"GPM pass at {t_ist(tmid)} IST", "",
           f"Heaviest rain under the radar: {top_rate:.1f} mm/h ({where}).",
           f"Rain clouds reached about {top_h:.0f} km high.", "",
           "One snapshot as the satellite flew over, not a 24-hour total.",
           "Source: NASA/JAXA GPM Dual-frequency Precipitation Radar (Ku band)",
           "#MasRainman · masrainman.blogspot.com"]
    open(os.path.join(out_dir, base + ".txt"), "w", encoding="utf-8").write("\n".join(cap) + "\n")
    print("  made", base)
    return base


def nearest_district(la, lo, geo, others):
    from shapely.geometry import Point
    p = Point(lo, la)
    best = None
    for g_ in [geo] + others:
        for n, g in g_:
            d = 0 if g.contains(p) else g.distance(p)
            if best is None or d < best[0]:
                best = (d, n)
    if best is None:
        return f"{la:.1f}°N {lo:.1f}°E"
    return best[1] if best[0] == 0 else f"sea/land near {best[1]}"


def radar_passes(sess, regions):
    """Download the radar passes of the last 24 h that cross any of the regions with rain."""
    boxes = {k: box_for(load_geo(REGIONS[k][2])) for k in regions}
    big = (min(b[0] for b in boxes.values()) - 1, min(b[1] for b in boxes.values()) - 1,
           max(b[2] for b in boxes.values()) + 1, max(b[3] for b in boxes.values()) + 1)
    now = datetime.now(UTC)
    grans = cmr_search(RADAR, now - timedelta(hours=72), now, big)
    if not grans:
        print("  no radar passes in the last 72 hours (NASA's radar files can be a day or two behind)")
        return []
    newest = grans[-1][2]
    # past 24 h; if NASA's radar files are behind, the 24 h up to the newest file
    use = [g for g in grans if g[2] >= min(now, newest) - timedelta(hours=24)]
    print(f"  radar: {len(use)} pass(es) to check (newest file ends {newest:%d %b %H:%M} UTC)")
    found = []
    for g in use:
        p = sess.get(g[3], os.path.join(RAW, "radar", os.path.basename(urlparse(g[3]).path)))
        d = radar_read(p)
        for k, b in boxes.items():
            c = pass_cut(d, b)
            if c is None:
                continue
            rain = int(((c["rate"] >= 0.5) & c["inside"][..., None]).sum())
            print(f"    {g[0][:60]} over {k}: {rain} rain points")
            if rain >= 300:
                found.append((k, rain, c))
        os.remove(p)                     # big files: don't keep them
    best = {}
    for k, rain, c in sorted(found, key=lambda x: -x[1]):
        if len(best.setdefault(k, [])) < 2:  # at most two passes per state
            best[k].append(c)
    return [(k, c) for k, cs in best.items() for c in cs]


# ----------------------------------------------------------------- made-up data for checking the pictures
def demo_imerg(box):
    lat = np.arange(box[1] - 1, box[3] + 1, 0.1) + 0.05
    lon = np.arange(box[0] - 1, box[2] + 1, 0.1) + 0.05
    X, Y = np.meshgrid(lon, lat)
    rng = np.random.default_rng(3)
    steps = []
    for k in range(48):
        cx, cy = box[2] - (box[2] - box[0]) * k / 48, (box[1] + box[3]) / 2 + math.sin(k / 8)
        f = 30 * np.exp(-(((X - cx) / 0.6) ** 2 + ((Y - cy) / 0.9) ** 2)) + rng.gamma(0.3, 0.8, X.shape)
        steps.append(np.where(f > 0.3, f, 0) * 0.5)
    t1 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    return dict(lat=lat, lon=lon, steps=np.array(steps), t0=t1 - timedelta(hours=24), t1=t1, n=48)


def demo_pass(box):
    ns, nr, nb = 220, 49, 176
    cx, cy = (box[0] + box[2]) / 2 + 0.8, (box[1] + box[3]) / 2
    s = np.linspace(-1, 1, ns)[:, None]
    r = np.linspace(-1, 1, nr)[None, :]
    lat = cy + s * 4.5 + r * 0.4
    lon = cx + s * 1.2 - r * 1.1
    hgt = np.broadcast_to((nb - 1 - np.arange(nb)) * 0.125, (ns, nr, nb)).astype("f4")
    core = np.exp(-(((lon - cx - 0.5) / 0.5) ** 2 + ((lat - cy + 0.5) / 0.9) ** 2))
    rng = np.random.default_rng(5)
    top = 4 + 11 * core + rng.random((ns, nr)) * 2
    surf = 30 * core ** 1.5 + rng.gamma(0.4, 1.0, (ns, nr))
    rate = np.where(hgt < top[..., None], surf[..., None] * np.exp(-hgt / 6), 0).astype("f4")
    phase = np.where(hgt < 4.6, 200, np.where(hgt < 5.1, 150, 50)).astype("u1")
    t = datetime.now(UTC) - timedelta(hours=7)
    d = dict(swath="FS", lat=lat, lon=lon, rate=rate, surf=surf.astype("f4"), phase=phase, hgt=hgt,
             times=[t + timedelta(seconds=0.6 * i) for i in range(ns)])
    return pass_cut(d, box)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="GPM satellite rain reports")
    ap.add_argument("--region", default="tn ka ap")
    ap.add_argument("--what", default="both", choices=["both", "sat24", "gpm3d"])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--demo", action="store_true", help="made-up data (no NASA login needed)")
    a = ap.parse_args()
    regions = [r for r in a.region.replace(",", " ").split() if r in REGIONS]
    os.makedirs(a.out, exist_ok=True)
    made, problems = [], []
    sess = None if a.demo else EarthdataSession()

    if a.what in ("both", "sat24"):
        print("Satellite rain, past 24 hours (IMERG Early)")
        try:
            if a.demo:
                allbox = box_for(sum((load_geo(REGIONS[k][2]) for k in regions), []))
                data = demo_imerg(allbox)
            else:
                allbox = box_for(sum((load_geo(REGIONS[k][2]) for k in regions), []), pad=1.0)
                data = imerg_24h(sess, allbox)
            for k in regions:
                made.append(make_sat24(k, data, a.out))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            problems.append(f"sat24: {e}")
            print("  FAILED:", e)

    if a.what in ("both", "gpm3d"):
        print("Satellite radar 3D passes (GPM 2A.Ku)")
        try:
            if a.demo:
                passes = [(k, demo_pass(box_for(load_geo(REGIONS[k][2])))) for k in regions[:1]]
            else:
                passes = radar_passes(sess, regions)
            if not passes:
                print("  No radar pass with rain over these states in the last 24 hours.")
            for k, c in passes:
                made.append(make_gpm3d(k, c, a.out))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            problems.append(f"gpm3d: {e}")
            print("  FAILED:", e)

    print(f"Made {len(made)} report(s).")
    if problems:
        print("Problems:", "; ".join(problems))
        sys.exit(1 if not made else 0)


if __name__ == "__main__":
    main()
