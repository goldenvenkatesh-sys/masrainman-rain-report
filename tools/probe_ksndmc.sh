#!/usr/bin/env bash
# One-off: look at KSNDMC / Varunamitra pages from a GitHub runner (they are blocked elsewhere).
set +e
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
mkdir -p probe
for u in \
  https://www.ksndmc.org/ \
  https://www.ksndmc.org/ReportHomePage.aspx \
  https://www.ksndmc.org/Weather_info.aspx \
  https://varunamitra.karnataka.gov.in/ \
  "https://varunamitra.karnataka.gov.in/Default/Index?service=CurrentRainfall" \
  "https://varunamitra.karnataka.gov.in/Default/Index?service=RainForecast" \
  https://dms.ksndmc.org/ \
  https://www.ksndmc.org/Rainfall.aspx ; do
  f=probe/$(echo "$u" | md5sum | cut -c1-8).html
  code=$(curl -sS -L -m 40 -A "$UA" -o "$f" -w "%{http_code} %{url_effective} %{size_download}" "$u")
  echo "=== $u -> $code"
  # links, forms, scripts, iframes, ajax urls
  grep -oiE '(href|src|action)="[^"]+"' "$f" | sort -u | grep -viE '\.(css|png|jpg|gif|ico|svg|woff2?)"' | head -80
  grep -oiE "(url|ajax|fetch|getJSON|\.post|\.get)\s*[:(]\s*['\"][^'\"]+['\"]" "$f" | sort -u | head -40
  grep -oiE '<(title|h[1-3])[^>]*>[^<]{0,120}' "$f" | head -10
done
