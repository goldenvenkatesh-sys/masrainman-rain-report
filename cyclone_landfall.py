"""
Cyclone landfall animation from satellite rain (NASA/JAXA GPM IMERG Early, every 30 minutes, 0.1°).

For a storm described in data/cyclones/<name>.json (IMD track fixes, landfall point, time window, map box)
it draws, half-hour by half-hour, the rain rate around the storm with the IMD track and the storm's
position, marks the landfall, and ends with the total rain of the whole window and the wettest districts.

Outputs in out/ (MP4 1080x1350, GIF, PNG of the total, caption):
    cyclone_<name>_landfall_en.mp4 / .gif / .png / .txt

    python cyclone_landfall.py data/cyclones/murasu.json          # needs EARTHDATA_USER / EARTHDATA_PASS
    python cyclone_landfall.py data/cyclones/murasu.json --demo   # made-up rain, to check the look

IMERG Early is about 4 hours behind real time, so a landfall can be drawn the same morning.
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlparse

import numpy as np

import gpm_sat as g
from gpm_sat import plt, IST, UTC, BG, TEXT, SOFT, MUTED, GOLD, EDGE

FPS = 12
STATES = ["ap_districts.geojson", "tn_districts.geojson", "tg_districts.geojson",
          "ka_districts.geojson", "kl_districts.geojson"]
STATE_NAMES = {"ap_districts.geojson": "Andhra Pradesh", "tn_districts.geojson": "Tamil Nadu",
               "tg_districts.geojson": "Telangana", "ka_districts.geojson": "Karnataka",
               "kl_districts.geojson": "Kerala"}
LAND = "#1a2230"
SEA = "#0a1220"

# rain rate (mm/h): own MasRainman scale, transparent below 0.5 mm/h
RATE_STOPS = [(0.5, "#1f3f8f"), (1, "#2a6fd1"), (2, "#25a6d9"), (4, "#22c7a5"), (7, "#7bd34e"),
              (10, "#e9dd3a"), (15, "#f6a833"), (20, "#ef6a2c"), (30, "#d83a4a"), (45, "#b43b9e"),
              (70, "#f3d6f7")]
RATE_TICKS = [0.5, 1, 2, 4, 7, 10, 15, 20, 30, 45, 70]


def ramp_rate(r):
    xs = np.log10([v for v, _ in RATE_STOPS])
    cs = np.array([g.hex2rgb(c) for _, c in RATE_STOPS])
    t = np.log10(np.clip(r, RATE_STOPS[0][0], RATE_STOPS[-1][0]))
    rgba = np.zeros(r.shape + (4,))
    for i in range(3):
        rgba[..., i] = np.interp(t, xs, cs[:, i])
    rgba[..., 3] = np.clip((r - 0.3) / 0.4, 0, 1) * 0.92
    return rgba


def t_ist(t, fmt="%-I:%M %p %-d %b"):
    return t.astimezone(IST).strftime(fmt)


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ----------------------------------------------------------------- track
class Track:
    def __init__(self, fixes):
        self.t = [parse_t(f["t"]) for f in fixes]
        self.lat = [f["lat"] for f in fixes]
        self.lon = [f["lon"] for f in fixes]
        lf = [i for i, f in enumerate(fixes) if f.get("landfall")]
        self.landfall = (self.t[lf[0]], self.lat[lf[0]], self.lon[lf[0]]) if lf else None
        self.end = self.t[-1]

    def at(self, t):
        """Interpolated (lat, lon) at t, or None outside the fixes."""
        if t < self.t[0] or t > self.t[-1]:
            return None
        x = [(v - self.t[0]).total_seconds() for v in self.t]
        s = (t - self.t[0]).total_seconds()
        return float(np.interp(s, x, self.lat)), float(np.interp(s, x, self.lon))

    def line_upto(self, t):
        pts = [(la, lo) for tt, la, lo in zip(self.t, self.lat, self.lon) if tt <= t]
        p = self.at(t)
        if p:
            pts.append(p)
        return np.array(pts) if pts else np.zeros((0, 2))


# ----------------------------------------------------------------- data
def load_imerg(storm, box, demo=False):
    t0, t1 = parse_t(storm["start_utc"]), parse_t(storm["end_utc"])
    if demo:
        return demo_rain(storm, box, t0, t1)
    sess = g.EarthdataSession()
    grans = [x for x in g.cmr_search(g.IMERG, t0, t1) if t0 <= x[1] < t1]
    if not grans:
        raise SystemExit("No IMERG Early half-hours for that window yet (it is about 4 hours behind).")
    print(f"  IMERG Early: {len(grans)} half-hours, {grans[0][1]:%d %b %H:%M} → {grans[-1][2]:%d %b %H:%M} UTC")

    def one(x):
        return g.imerg_read(sess.get(x[3], os.path.join(g.RAW, "imerg", os.path.basename(urlparse(x[3]).path))),
                            box)

    with cf.ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(one, grans))
    lat, lon = parts[0][0], parts[0][1]
    rate = np.stack([p for _, _, p in parts])              # mm/h, (time, lat, lon)
    times = [x[1] for x in grans]
    return lat, lon, rate, times


def demo_rain(storm, box, t0, t1):
    """Made-up spiral rain bands following the track (for checking the drawing only)."""
    tr = Track(storm["track"])
    lat = np.arange(box[1] + 0.05, box[3], 0.1)
    lon = np.arange(box[0] + 0.05, box[2], 0.1)
    X, Y = np.meshgrid(lon, lat)
    times, frames = [], []
    t = t0
    rng = np.random.default_rng(3)
    noise = rng.random((len(lat), len(lon)))
    while t < t1:
        c = tr.at(t) or (tr.lat[-1] + 0.03 * (t - tr.end).total_seconds() / 3600,
                         tr.lon[-1] - 0.02 * (t - tr.end).total_seconds() / 3600)
        dy, dx = Y - c[0], (X - c[1]) * math.cos(math.radians(c[0]))
        r = np.hypot(dx, dy) * 111
        th = np.arctan2(dy, dx)
        k = (t - t0).total_seconds() / 3600
        bands = 0.5 + 0.5 * np.sin(2 * th - r / 45 + k * 0.6)
        eye = np.clip((r - 18) / 25, 0, 1)
        land = 1.0 if t < (tr.landfall[0] if tr.landfall else t1) else 0.75
        rr = land * 40 * np.exp(-r / 160) * bands ** 2.5 * eye * (0.7 + 0.6 * noise)
        frames.append(np.where(rr > 0.3, rr, np.nan).astype("f4"))
        times.append(t)
        t += timedelta(minutes=30)
    return lat, lon, np.stack(frames), times


# ----------------------------------------------------------------- drawing
def draw_base(ax, box, states):
    ax.set_facecolor(SEA)
    for fn, geo in states:
        for _, gg in geo:
            for r in g.rings(gg):
                ax.fill(r[:, 0], r[:, 1], color=LAND, lw=0, zorder=1)
    for fn, geo in states:
        for _, gg in geo:
            for r in g.rings(gg):
                ax.plot(r[:, 0], r[:, 1], color="#3c4558", lw=0.5, zorder=3)
    from shapely.ops import unary_union
    for fn, geo in states:
        if geo:
            for r in g.rings(unary_union([gg for _, gg in geo]).buffer(0)):
                ax.plot(r[:, 0], r[:, 1], color="#c9d1de", lw=1.3, zorder=4)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect(1 / math.cos(math.radians((box[1] + box[3]) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(EDGE)


def storm_icon(ax, z=9):
    """Cyclone symbol: ring + two curved arms (drawn once, moved each frame)."""
    th = np.linspace(0, 1.25 * math.pi, 40)
    arm = np.c_[np.cos(th) * (0.18 + 0.12 * th / math.pi), np.sin(th) * (0.18 + 0.12 * th / math.pi)]
    a1 = ax.plot([], [], color="#ff5a3c", lw=3.2, zorder=z, solid_capstyle="round")[0]
    a2 = ax.plot([], [], color="#ff5a3c", lw=3.2, zorder=z, solid_capstyle="round")[0]
    ring = ax.plot([], [], "o", ms=13, mfc="white", mec="#ff5a3c", mew=3, zorder=z + 1)[0]
    lab = ax.text(0, 0, "", color="white", fontsize=17, weight="bold", zorder=z + 1,
                  bbox=dict(boxstyle="round,pad=0.25", fc="#0b0e14bb", ec="none"))

    def move(lat, lon, name):
        k = 1 / math.cos(math.radians(lat))
        a1.set_data(lon + arm[:, 0] * k, lat + arm[:, 1])
        a2.set_data(lon - arm[:, 0] * k, lat - arm[:, 1])
        ring.set_data([lon], [lat])
        lab.set_position((lon + 0.32, lat + 0.18))
        lab.set_text(name)

    def hide():
        for a in (a1, a2, ring):
            a.set_data([], [])
        lab.set_text("")

    return move, hide


def rate_bar(fig):
    cax = fig.add_axes([0.08, 0.085, 0.84, 0.022])
    lo, hi = math.log10(RATE_TICKS[0]), math.log10(RATE_TICKS[-1])
    x = np.logspace(lo, hi, 600)
    rgba = ramp_rate(x)
    rgba[..., 3] = 1
    cax.imshow(rgba[None, :, :3], aspect="auto", extent=[lo, hi, 0, 1])
    cax.set_yticks([])
    cax.set_xticks([math.log10(v) for v in RATE_TICKS])
    cax.set_xticklabels([f"{v:g}" for v in RATE_TICKS], color=SOFT, fontsize=14)
    cax.tick_params(length=0, pad=6)
    for s in cax.spines.values():
        s.set_visible(False)
    return cax


def total_bar(fig):
    cax = fig.add_axes([0.08, 0.085, 0.84, 0.022])
    x = np.logspace(g._L0, g._L1, 600)
    cax.imshow(g.ramp24(x)[None, :, :3], aspect="auto", extent=[g._L0, g._L1, 0, 1])
    cax.set_yticks([])
    cax.set_xticks([math.log10(v) for v in g.TICKS24])
    cax.set_xticklabels([f"{v:g}" for v in g.TICKS24], color=SOFT, fontsize=14)
    cax.tick_params(length=0, pad=6)
    for s in cax.spines.values():
        s.set_visible(False)
    fig.text(0.5, 0.045, "Total rain in this window (mm)  ·  NASA/JAXA GPM IMERG Early (satellite estimate)  ·  "
             "track: IMD", ha="center", color=MUTED, fontsize=13)


def make(storm_path, out_dir, demo=False, gif_width=720):
    storm = json.load(open(storm_path, encoding="utf-8"))
    name = storm["name"]
    box = storm["box"]
    tr = Track(storm["track"])
    lat, lon, rate, times = load_imerg(storm, box, demo)
    states = [(fn, g.load_geo(fn)) for fn in STATES]
    ext = [lon[0] - 0.05, lon[-1] + 0.05, lat[0] - 0.05, lat[-1] + 0.05]
    from scipy.ndimage import zoom

    base = f"cyclone_{name.lower()}_landfall_en"
    os.makedirs(out_dir, exist_ok=True)
    fig = plt.figure(figsize=(g.W / 100, g.H / 100), dpi=100, facecolor=BG)
    t0, t1 = times[0], times[-1] + timedelta(minutes=30)
    g.header(fig, "CYCLONE LANDFALL · SATELLITE RAIN", f"Cyclone {name}",
             f"Every 30 minutes · {t_ist(t0)} → {t_ist(t1)} IST")
    ax = fig.add_axes([0.04, 0.17, 0.92, 0.68])
    draw_base(ax, box, states)
    for town, la, lo in storm.get("towns", []):
        if box[0] < lo < box[2] and box[1] < la < box[3]:
            ax.plot(lo, la, "o", ms=4, color="#e8ecf2", zorder=6)
            ax.text(lo - 0.06, la, town, color="#e8ecf2", fontsize=13, ha="right", va="center", zorder=6)
    layer = ax.imshow(np.zeros((2, 2, 4)), extent=ext, origin="lower", zorder=2, interpolation="bilinear")
    # full IMD track faint, travelled part bright
    ax.plot(tr.lon, tr.lat, color="#ffffff", lw=1.4, ls=(0, (3, 3)), alpha=0.45, zorder=7)
    done = ax.plot([], [], color="#ffffff", lw=2.6, zorder=8)[0]
    ax.plot(tr.lon, tr.lat, "o", ms=5, color="#ffffff", alpha=0.7, zorder=8)
    move, hide = storm_icon(ax)
    lf_mark = ax.plot([], [], marker="*", ms=26, color="#ffd23f", mec="#0b0e14", mew=1.5, zorder=11)[0]
    lf_text = ax.text(0, 0, "", color="#ffd23f", fontsize=17, weight="bold", zorder=11,
                      bbox=dict(boxstyle="round,pad=0.3", fc="#0b0e14dd", ec="#ffd23f"))
    clock = ax.text(0.02, 0.975, "", transform=ax.transAxes, color=TEXT, fontsize=21, weight="bold",
                    va="top", zorder=12, bbox=dict(boxstyle="round,pad=0.4", fc="#0b0e14dd", ec=EDGE))
    status = ax.text(0.02, 0.905, "", transform=ax.transAxes, color=SOFT, fontsize=16, va="top", zorder=12)
    cax = rate_bar(fig)
    unit = fig.text(0.5, 0.045, "Rain rate (mm per hour)  ·  NASA/JAXA GPM IMERG Early (satellite estimate)  ·  "
                    "track: IMD", ha="center", color=MUTED, fontsize=13)
    foot = fig.text(0.5, 0.125, storm.get("track_note", ""), ha="center", color=MUTED, fontsize=13)

    lf_t = tr.landfall[0] if tr.landfall else None
    vid_path = os.path.join(out_dir, base + ".mp4")
    vid = g.Video(vid_path, fps=FPS)
    for k, t in enumerate(times):
        mid = t + timedelta(minutes=15)
        f = rate[k]
        layer.set_data(ramp_rate(np.nan_to_num(zoom(np.nan_to_num(f), 3, order=1))))
        clock.set_text(t_ist(mid, "%-I:%M %p, %-d %b") + " IST")
        pts = tr.line_upto(mid)
        if len(pts):
            done.set_data(pts[:, 1], pts[:, 0])
        p = tr.at(mid)
        if p:
            move(p[0], p[1], name)
        else:
            hide()
        landed = lf_t is not None and mid >= lf_t
        if landed:
            lf_mark.set_data([tr.landfall[2]], [tr.landfall[1]])
            lf_text.set_position((tr.landfall[2] + 0.25, tr.landfall[1] - 0.45))
            lf_text.set_text(f"Landfall · {t_ist(lf_t, '%-I:%M %p %-d %b')}")
            status.set_text(storm.get("after_landfall", "") if p is None else storm.get("landfall_text", ""))
        else:
            status.set_text(f"Over the sea · {storm.get('category_at_landfall', '')}")
        # hold a little longer on the landfall frame
        reps = 2
        if landed and lf_t is not None and mid - timedelta(minutes=30) < lf_t:
            reps = FPS * 2
        vid.add(fig, reps)

    # ---- ending: total rain of the whole window, with the wettest districts
    total = np.nansum(rate, axis=0) * 0.5                       # mm
    layer.set_data(g.ramp24(np.clip(zoom(total, 3, order=1), 0, None)))
    hide()
    clock.set_text(f"Total rain · {t_ist(t0, '%-d %b %-I %p')} → {t_ist(t1, '%-d %b %-I %p')}")
    status.set_text("")
    cax.remove()
    unit.remove()
    total_bar(fig)
    rows = []
    for fn, geo in states:
        if not geo:
            continue
        for dn, m, x in g.district_stats(geo, lat, lon, total):
            rows.append((dn, STATE_NAMES[fn], m, x))
    rows.sort(key=lambda r: -r[2])
    top = rows[:5]
    foot.set_text("")
    tb = ax.text(0.975, 0.025, "", transform=ax.transAxes, color=TEXT, fontsize=15, ha="right", va="bottom",
                 linespacing=1.2, zorder=12, bbox=dict(boxstyle="round,pad=0.5", fc="#0b0e14ee", ec=GOLD))
    lines = [f"{i + 1}. {d} ({s}) — {m:.0f} mm avg, {x:.0f} mm max" for i, (d, s, m, x) in enumerate(top)]
    vid.add(fig, FPS)
    for i in range(len(lines)):
        tb.set_text("Wettest districts (satellite)\n" + "\n".join(lines[:i + 1]))
        vid.add(fig, 6)
    vid.add(fig, FPS * 4)
    vid.close()
    fig.savefig(os.path.join(out_dir, base + ".png"), facecolor=BG)
    plt.close(fig)

    # GIF (smaller, from the MP4)
    exe = g.shutil.which("ffmpeg")
    if not exe:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    gif = os.path.join(out_dir, base + ".gif")
    vf = f"fps=10,scale={gif_width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer"
    subprocess.run([exe, "-v", "error", "-y", "-i", vid_path, "-vf", vf, gif], check=True)

    cap = [f"🌀 Cyclone {name} · landfall seen from space",
           f"Satellite rain every 30 minutes, {t_ist(t0)} to {t_ist(t1)} IST", ""]
    if tr.landfall:
        cap.append(f"{storm.get('landfall_text', 'Landfall')} around {t_ist(lf_t, '%-I:%M %p on %-d %b')} IST "
                   f"({storm.get('category_at_landfall', '')}, {storm.get('winds_at_landfall', '')}).")
    cap += ["", "Wettest districts in this window (satellite average, max):"]
    cap += [f"{i + 1}. {d}, {s} – {m:.0f} mm (max {x:.0f} mm)" for i, (d, s, m, x) in enumerate(top)]
    cap += ["", "Satellite estimate, not gauge readings. Rain: NASA/JAXA GPM IMERG Early. Track: IMD.",
            "#MasRainman · masrainman.blogspot.com"]
    open(os.path.join(out_dir, base + ".txt"), "w", encoding="utf-8").write("\n".join(cap) + "\n")
    print("  made", base, "-", len(times), "half-hours")
    return base


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cyclone landfall animation from IMERG satellite rain")
    ap.add_argument("storm", help="data/cyclones/<name>.json")
    ap.add_argument("--out", default=g.OUT)
    ap.add_argument("--demo", action="store_true", help="made-up rain (no NASA login needed)")
    a = ap.parse_args()
    make(a.storm, a.out, a.demo)
