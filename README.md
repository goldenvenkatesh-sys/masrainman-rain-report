# MasRainman · Tamil Nadu rainfall report (animated)

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

## Tamil Nadu every 2 hours (rain so far)

The *TN rainfall so far* workflow runs every 2 hours, at 10:45 am, 12:45, 2:45 … 6:45 am IST, and makes the same
picture and video as the daily report for "rain since 8:30 am, so far", with a red LIVE tag, the time on the map,
and a bar showing how much of the 24 hours has gone by. Kerala is not changed: it stays once a day.

Each run downloads TN-SMART's running file again and adds it to the station totals kept by the earlier runs
(`data/raw/live/<rain-day>.json`, carried between runs in the Actions cache). A station that starts reporting late
is added when it appears, rain it reported earlier is kept even if a later file leaves it out, and a run with no
new rain anywhere posts nothing. The full 24 hours (8:30 am to 8:30 am) is the 09:45 daily report.

```
python rain_report.py live            # rain since 08:30 today, so far
python rain_report.py live --always   # make it even if nothing changed since the last run
```

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
