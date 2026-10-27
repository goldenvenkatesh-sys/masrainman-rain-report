# MasRainman · Tamil Nadu, Kerala, Karnataka and Andhra Pradesh rainfall report (animated)

Makes a Tamil infographic video (MP4 + GIF + PNG) from TN-SMART station rainfall.

- **daily**: one rain-day (24 h ending 08:30 IST). The map colours build up from zero.
- **accum**: total from a start date to today. The map fills day by day with a date counter.
- **live24** (Tamil Nadu): "Past 24 Hours Observed Rainfall", a 36.5 s portrait video (1080x1350). The rain-day
  is replayed hour by hour on a hillshaded terrain map (SRTM via AWS Terrain Tiles, cached in `data/raw/terrain/`);
  the camera follows the gauge with the heaviest rain each hour, then shows the whole state. Then come the five
  wettest stations, a 24-hour rain clock (when the rain fell) and "Did the models get it right?": heavy-rain
  districts caught by the ECMWF IFS and AIFS forecasts (ECMWF open data, `models.py`) and by WeatherNext 3 when
  its data is provided (`WN3_RAIN_URL` or `data/raw/models/wn3_<date>.json`; otherwise "no forecast"). Made with the daily report at 09:45 IST (`rain-report.yml`, mode `daily`, `both` or
  `live24`), or `python rain_report.py live24 --date 2026-10-26`. Other states can be added in `live24.py`
  (`REGIONS24`).

Data: `https://beta-tnsmart.rimes.int/Rainfall_Python_IDW/Hourly_json/hourly_rainfall_YYYY-MM-DD.json`
(one file per rain-day, about 1,565 stations with latitude/longitude). Downloaded days are cached in `data/raw/`.

## Run on GitHub (recommended)

1. Create a new repo (for example `masrainman-rain-report`) and upload everything in this folder, including `.github/`.
2. It runs every day at 09:45 IST and makes both reports. To run it yourself: Actions → *TN rainfall report* → *Run workflow*, and choose mode / dates.
3. Videos are also attached to each run under **Artifacts → rain-report**.

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
from KSNDMC's telemetric rain gauges, at the same times as Tamil Nadu (10:45 am, 12:45, 2:45 … 6:45 am IST).

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

## Andhra Pradesh: every 2 hours, daily, multi-day and monthly

Andhra Pradesh gets the same reports as Tamil Nadu, from the DES / APSDPS real-time weather stations:

- *Andhra Pradesh rainfall so far* (`ap-live.yml`): the white "rain since 8:30 am, so far" picture at the same
  times as Tamil Nadu and Karnataka (10:45 am, 12:45, 2:45 … 6:45 am IST).
- *TN rainfall report* at 09:55 IST: the dark 24-hour report and the total since Oct 1, and on the 1st of the month
  last month's total (a month with data on fewer than 80% of its days is skipped).

The caption starts with "Rainfall Report · Andhra Pradesh" (Karnataka's now says "· Karnataka") so the posts are
easy to tell apart.

Data:
- Live: the station map on desweather.ap.gov.in (`Livemap/Apsdpsmapsv1.html`) reads
  `http://desweather.ap.gov.in/webservice/rest/json1`, a list of all ~2,200 stations with latitude, longitude,
  district, mandal and `rain` = rain since 8:30 am (the same numbers as `Realtime/Rainfall.jsp`). Dry stations are
  listed with 0, -1 means not reporting, and a station whose last report (`ldate`) is older than the start of the
  rain-day is skipped.
- Finished days: `Realtime/Rainfall24.jsp` lists the last full 24 hours (8:30 am to 8:30 am) by station id. The
  09:55 run saves it to `data/ap/daily/<rain-day>.json` (rain by station id) and keeps every station's position and
  name in `data/ap/stations.json`. The site shows only the latest day, so the archive starts on 2026-10-24, and each
  2-hourly run also saves the day if the 09:55 run was skipped. The first monthly total will be November 2026.
- `ap_fetch.py` reads both. apsdps.ap.gov.in itself does not answer from outside India, but desweather.ap.gov.in does
  (over http; its https certificate is self-signed).
- District boundaries: `data/ap_districts.geojson`, the 28 districts the feed uses. It starts from the 26 districts of
  2022 and splits Prakasam (Markapuram) and Alluri Sitharama Raju (Polavaram) by the feed's own station districts;
  `tools/build_ap_districts.py` rebuilds it.
- English only for now.

```
python rain_report.py live --region ap
python ap_fetch.py daily                      # save the finished rain-day
python rain_report.py daily --region ap
python rain_report.py accum --region ap --start 2026-10-24
python rain_report.py accum --region ap --month 2026-11
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
