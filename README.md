# MasRainman · Tamil Nadu, Kerala and Karnataka rainfall report (animated)

Makes a Tamil infographic video (MP4 + GIF + PNG) from TN-SMART station rainfall.

- **daily**: one rain-day (24 h ending 08:30 IST). The map colours build up from zero.
- **accum**: total from a start date to today. The map fills day by day with a date counter.

Data: `https://beta-tnsmart.rimes.int/Rainfall_Python_IDW/Hourly_json/hourly_rainfall_YYYY-MM-DD.json`
(one file per rain-day, about 1,565 stations with latitude/longitude). Downloaded days are cached in `data/raw/`.

## Run on GitHub (recommended)

1. Create a new repo (for example `masrainman-rain-report`) and upload everything in this folder, including `.github/`.
2. Optional, to post to Telegram: Settings → Secrets and variables → Actions → add
   - `TELEGRAM_BOT_TOKEN`: your bot token
   - `RAIN_REPORT_CHAT_IDS`: chat ids, comma separated (for example your id and the users' group id `-100…`)
3. It runs every day at 09:45 IST and makes both reports. To run it yourself: Actions → *TN rainfall report* → *Run workflow*, and choose mode / dates.
4. Videos are also attached to each run under **Artifacts → rain-report**.

## Where to find the reports (phone friendly)

Every report (24-hour, multi-day, monthly and the 2-hourly "rain so far") is also saved to the
**`reports` branch** by `publish_reports.sh`:

- **MasRainman model-maps site** (Cloudflare, login protected): *Rain reports* page, with preview and
  MP4 / GIF / PNG download buttons for Tamil Nadu, Kerala and Karnataka.
- **GitHub phone app**: open this repo → switch the branch to `reports` → the README shows the latest
  report of each kind with a picture; tap a file, then ⋯ → *Download*.

Kept: the newest 12 "rain so far" pictures per state, 10 days of daily / multi-day reports and
13 months of monthly totals. The branch is saved as one fresh commit each time, so old videos don't
pile up in the repo.

Optional secret **`WN3_DISPATCH_TOKEN`** (fine-grained token, *Actions: read and write* on
`masrainman-weathernext3`): the site republishes within a few minutes of each report. Without it the
site checks for new reports every 2 hours.

## Tamil Nadu every 2 hours (rain so far)

The *TN rainfall so far* workflow runs every 2 hours, at 10:45 am, 12:45, 2:45 … 6:45 am IST, and makes the same
picture and video as the daily report for "rain since 8:30 am, so far", on a white background, with a red LIVE tag,
the time on the map, and a bar showing how much of the 24 hours has gone by. The 24-hour, multi-day and monthly
reports stay dark. Kerala is not changed: it stays once a day.

Each run downloads TN-SMART's running file again and adds it to the station totals kept by the earlier runs
(`data/raw/live/<rain-day>.json`, carried between runs in the Actions cache). A station that starts reporting late
is added when it appears, rain it reported earlier is kept even if a later file leaves it out, and a run with no
new rain anywhere posts nothing. The full 24 hours (8:30 am to 8:30 am) is the 09:45 daily report.

```
python rain_report.py live            # rain since 08:30 today, so far
python rain_report.py live --always   # make it even if nothing changed since the last run
```

### Making "every 2 hours" reliable

GitHub's timer sometimes starts runs late or skips them. To make sure every slot runs, add a free job at
**cron-job.org** at 10:45, 12:45, … 06:45 IST (every 2 hours at :45):
`POST https://api.github.com/repos/goldenvenkatesh-sys/masrainman-rain-report/actions/workflows/tn-live.yml/dispatches`
with headers `Authorization: Bearer <fine-grained token with Actions: read & write on this repo>` and
`Accept: application/vnd.github+json`, and body `{"ref":"main"}`. The GitHub timer can stay on too: if both
fire, the second run finds no new rain and posts nothing.

## Karnataka every 2 hours (rain so far)

The *Karnataka rainfall so far* workflow makes the same white "rain since 8:30 am, so far" picture for Karnataka
from KSNDMC's telemetric rain gauges, at the same times as Tamil Nadu (10:45 am, 12:45, 2:45 … 6:45 am IST), and posts it to Telegram.

Data: the map inside the KSNDMC weather dashboard (`ksndmc.org/default.aspx/WebDashboard`) is
`https://ksndmc.org:122/`, and that page asks its own server for the 15-minute rainfall table (ASP.NET callback
`showRain15Q`). It lists every gauge with at least 0.5 mm since 8:30 am (about 3,000 on a wet day) by district,
taluk and gram panchayat / hobli, but without coordinates. `ksndmc_fetch.py` reads it.

- Gauge positions are in `data/ka_gauges.csv`. Each gauge is named after its gram panchayat, so it is placed at the
  centre of the village of that name in the same taluk (village centres from the DataMeet Indian Village Boundaries,
  ODbL). About 1 in 8 have no matching village and go to the middle of the other gauges of the same hobli.
  `tools/build_ka_gauges.py` rebuilds the file. A gauge that is not in the file yet is placed the same way at run time.
- Gauges that are dry are not listed, so the map puts 0 mm wherever no gauge with rain is within 10 km, and districts
  are ranked by the average of the map over their area instead of by the average of the listed gauges.
- District boundaries: `data/ka_districts.geojson` (31 districts; Vijayanagara cut out of Ballari from its 2011 taluks).
- English only for now. A Kannada version needs a Kannada font and the district names in Kannada.

```
python rain_report.py live --region ka
```

## Monthly total

On the 1st of every month the *TN rainfall report* also makes last month's total (rain-days 1st to last day), for
Tamil Nadu at 09:45 and Kerala at 12:15. A month with data on fewer than 80% of its days is skipped. To make one
yourself: `python rain_report.py accum --month 2026-10`, or run the workflow with mode `month`.

## Run on your PC

```
pip install -r requirements.txt
python rain_report.py daily
python rain_report.py daily --date 2026-10-18
python rain_report.py accum --start 2026-10-01
python rain_report.py accum --start 2026-10-01 --end 2026-10-18
python rain_report.py accum --start 2026-10-01 --live    # add today's running day too
```

Output files are in `out/`. Each report also writes a `.txt` caption in Tamil.

**Windows note:** Tamil letters need Pillow's text shaping (libraqm). If the script prints
"WARNING: Pillow has no libraqm", the Tamil text may look broken on Windows. Use GitHub Actions,
or run inside WSL (Ubuntu), where it works out of the box.

## Settings (top of `rain_report.py`)

- `BRAND_LINE`, `SITE`: footer text
- `SCALE_DAILY`, `SCALE_ACCUM`: colour classes and labels
- `IDW_RADIUS_KM`, `IDW_POWER`: map interpolation
- `W, H, FPS`: video size and speed

## Kerala (English + Malayalam)

Sources: IMD Thiruvananthapuram's daily station rainfall PDF (about 85 rain gauges plus the AWS/ARG stations
that recorded rain), plus Kerala Water Resources Department gauges and KSEB dams (below). KSDMA's daily PDF has
the same gauges as IMD's, and its 24-hour map is published only as a picture, so neither is used for numbers.

- `fetch_kerala.py` saves the day's IMD and KSDMA files into `data/kerala/raw/<date>/` (they are overwritten
  daily on the websites, so this builds the history). The *Kerala rainfall archive* workflow runs it 3 times a day.
- `kerala_data.py` reads the PDFs into `data/kerala/daily/<date>.json`.
- `kerala_extra.py` adds more rain gauges on top of IMD's:
  - **Water Resources Department** (about 360 manual gauges) from Kerala WRIS (`wris.kerala.gov.in`). Their
    readings are typed in over the next few days, so each run re-reads the last 12 days into `data/kerala/wris/`.
  - **KSEB dams** (18 reservoirs) from the daily post on `dams.kseb.in`, into `data/kerala/kseb/`.
  A day still needs IMD's PDF to get a report; the extra gauges are added to it, and a WRIS gauge with the same
  name as an IMD one is left out.
- Station positions are in `data/kerala_stations.csv` (types `rg`, `aws`, `sw` for Water Resources Dept, `dam`).
  Water Resources gauge positions (type `sw`) come from the National Water Data Portal's Kerala SW station list
  (`data/kerala/kerala_sw_stations_nwdp.csv`), checked to fall inside the right district; about 120 gauges that
  actually report are placed. If WRIS's own map server comes back, `data/kerala/wris_stations.json` is saved too. Rows marked "approximate" (including the dams) were placed by hand; correct them if you
  know better. New stations without a position are listed in `data/kerala/unmapped_stations.txt`.

```
python rain_report.py daily --region kl            # English and Malayalam videos
python rain_report.py accum --region kl --lang ml  # Malayalam only
```

On GitHub: *TN rainfall report* runs Kerala automatically at 12:15 IST; for a manual run choose region `kl` or `both`.
