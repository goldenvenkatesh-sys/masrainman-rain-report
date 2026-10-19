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

Source: IMD Thiruvananthapuram's daily station rainfall PDF (about 85 rain gauges plus the AWS/ARG stations
that recorded rain). KSDMA's daily PDF has the same gauges, so IMD's file is used.

- `fetch_kerala.py` saves the day's IMD and KSDMA files into `data/kerala/raw/<date>/` (they are overwritten
  daily on the websites, so this builds the history). The *Kerala rainfall archive* workflow runs it 3 times a day.
- `kerala_data.py` reads the PDFs into `data/kerala/daily/<date>.json`.
- Station positions are in `data/kerala_stations.csv`. Rows marked "approximate" were placed by hand; correct
  them if you know better. New stations without a position are listed in `data/kerala/unmapped_stations.txt`.

```
python rain_report.py daily --region kl            # English and Malayalam videos
python rain_report.py accum --region kl --lang ml  # Malayalam only
```

On GitHub: *TN rainfall report* runs Kerala automatically at 12:15 IST; for a manual run choose region `kl` or `both`.
