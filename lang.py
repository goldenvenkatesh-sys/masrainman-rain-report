"""Text for the report in each language. Add or edit wording here."""
from tn_names import ta as tn_ta, MONTHS_TA, time_ta

KL_ML = {
    "Thiruvananthapuram": "തിരുവനന്തപുരം", "Kollam": "കൊല്ലം", "Pathanamthitta": "പത്തനംതിട്ട",
    "Alappuzha": "ആലപ്പുഴ", "Kottayam": "കോട്ടയം", "Idukki": "ഇടുക്കി", "Ernakulam": "എറണാകുളം",
    "Thrissur": "തൃശ്ശൂർ", "Palakkad": "പാലക്കാട്", "Malappuram": "മലപ്പുറം", "Kozhikode": "കോഴിക്കോട്",
    "Wayanad": "വയനാട്", "Kannur": "കണ്ണൂർ", "Kasaragod": "കാസർകോട്",
}
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_ML = ["ജനു.", "ഫെബ്.", "മാർ.", "ഏപ്രി.", "മേയ്", "ജൂൺ", "ജൂലൈ", "ഓഗ.", "സെപ്.", "ഒക്ടോ.", "നവ.", "ഡിസ."]


def _time_en(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def _time_ml(dt):
    h = dt.hour
    part = "രാവിലെ" if 5 <= h < 12 else "ഉച്ചയ്ക്ക്" if 12 <= h < 15 else "വൈകിട്ട്" if 15 <= h < 19 else "രാത്രി"
    return f"{part} {h % 12 or 12}:{dt.minute:02d}"


LANGS = {
    "ta": {
        "font": "mukta",
        "months": MONTHS_TA,
        "time": time_ta,
        "district": tn_ta,
        "mm": "மி.மீ.",
        "title": "மழைப்பொழிவு அறிக்கை",
        "daily_sub": "24 மணி நேர மழை · {d0} காலை 8:30 முதல் {d1} காலை 8:30 வரை",
        "accum_sub": "பல நாள் மொத்த மழை · {d0} முதல் {d1} வரை ({n} நாட்கள்)",
        "live_note": " (இன்றைய மழை இதுவரை உட்பட)",
        "live_tag": "நேரலை", "live_badge": "{t} வரை",
        "live_sub": "இதுவரை பெய்த மழை · {d0} காலை 8:30 முதல் {d1} {t} வரை",
        "head_full": "{n} நிலையங்களில் {thr} மி.மீ.க்கு மேல் மழை பதிவு; மாவட்டங்களில் {dist} {avg} மி.மீ. நிலையச் சராசரியுடன் முதலிடம்.",
        "head_one": "1 நிலையத்தில் {thr} மி.மீ.க்கு மேல் மழை பதிவு; மாவட்டங்களில் {dist} {avg} மி.மீ. நிலையச் சராசரியுடன் முதலிடம்.",
        "head_dist": "மாவட்டங்களில் {dist} {avg} மி.மீ. நிலையச் சராசரியுடன் முதலிடம்.",
        "head_none": "மாநிலம் முழுவதும் குறிப்பிடத்தக்க மழை பதிவாகவில்லை.",
        "c_rep": "தரவு அளித்த நிலையங்கள்", "c_rep_sub": "{n} மாவட்டங்கள்",
        "c_rain": "மழை பெய்த நிலையங்கள்", "c_rain_daily": "மாநில சராசரி {x} மி.மீ.", "c_rain_accum": "சராசரி {x} மழை நாட்கள்",
        "c_thr_daily": "≥ 64.5 மி.மீ. நிலையங்கள்", "c_thr_daily_sub": "கனமழை அளவு",
        "c_thr_accum": "≥ 100 மி.மீ. பெற்ற நிலையங்கள்", "c_thr_accum_sub": "மொத்த மழை அளவு",
        "c_max": "அதிகபட்ச நிலையம்",
        "map_note": "IDW மேற்பரப்பு (இடைக்கணிப்பு)",
        "ins_avg": "சராசரி மழை", "ins_max": "அதிகபட்ச நிலையம்",
        "top_st": "முதல் 5 நிலையங்கள் (மி.மீ.)", "top_di": "முதல் 5 மாவட்டங்கள் · சராசரி மழை (மி.மீ.)",
        "legend": "மழை அளவு வகைகள் · ஒவ்வொரு வகையிலும் நிலையங்கள்", "legend_mm": "அனைத்தும் மி.மீ.", "zero": "0 மி.மீ.",
        "source": "ஆதாரம்: {src} · IDW p={p}, ஆரம் ≈ {r} கி.மீ.", "made": "உருவாக்கம்: {d} {m} {y}, {t}",
        "brand": "தயாரிப்பு: MasRainman",
        "cap_title": "🌧 மழைப்பொழிவு அறிக்கை", "cap_top": "முதல் 5 நிலையங்கள்:", "cap_src": "ஆதாரம்: {src}",
    },
    "en": {
        "font": "baloo",
        "months": MONTHS_EN,
        "time": _time_en,
        "district": lambda d: d,
        "mm": "mm",
        "title": "Rainfall Report",
        "daily_sub": "24-hour rainfall · 8:30 am {d0} to 8:30 am {d1}",
        "accum_sub": "Total rainfall · {d0} to {d1} ({n} days)",
        "live_note": " (incl. today so far)",
        "live_tag": "LIVE", "live_badge": "till {t}",
        "live_sub": "Rainfall so far · 8:30 am {d0} to {t} {d1}",
        "head_full": "{n} stations recorded over {thr} mm; {dist} leads the districts with a station average of {avg} mm.",
        "head_one": "1 station recorded over {thr} mm; {dist} leads the districts with a station average of {avg} mm.",
        "head_dist": "{dist} leads the districts with a station average of {avg} mm.",
        "head_none": "No significant rainfall recorded across the state.",
        "c_rep": "Stations reporting", "c_rep_sub": "{n} districts",
        "c_rain": "Stations with rain", "c_rain_daily": "State average {x} mm", "c_rain_accum": "Avg {x} rainy days",
        "c_thr_daily": "Stations ≥ 64.5 mm", "c_thr_daily_sub": "Heavy rain or more",
        "c_thr_accum": "Stations ≥ 100 mm", "c_thr_accum_sub": "Total for the period",
        "c_max": "Highest station",
        "map_note": "IDW surface (interpolated)",
        "ins_avg": "District average", "ins_max": "Highest station",
        "top_st": "Top 5 stations (mm)", "top_di": "Top 5 districts · average (mm)",
        "legend": "Rainfall classes · stations in each", "legend_mm": "All values in mm", "zero": "0 mm",
        "source": "Source: {src} · IDW p={p}, radius ≈ {r} km", "made": "Made: {d} {m} {y}, {t}",
        "brand": "By MasRainman",
        "cap_title": "🌧 Rainfall Report", "cap_top": "Top 5 stations:", "cap_src": "Source: {src}",
    },
    "ml": {
        "font": "baloo",
        "months": MONTHS_ML,
        "time": _time_ml,
        "district": lambda d: KL_ML.get(d, d),
        "mm": "മി.മീ.",
        "title": "മഴ റിപ്പോർട്ട്",
        "daily_sub": "24 മണിക്കൂർ മഴ · {d0} രാവിലെ 8:30 മുതൽ {d1} രാവിലെ 8:30 വരെ",
        "accum_sub": "ആകെ മഴ · {d0} മുതൽ {d1} വരെ ({n} ദിവസം)",
        "live_note": " (ഇന്നത്തെ മഴ ഉൾപ്പെടെ)",
        "live_tag": "തത്സമയം", "live_badge": "{t} വരെ",
        "live_sub": "ഇതുവരെയുള്ള മഴ · {d0} രാവിലെ 8:30 മുതൽ {d1} {t} വരെ",
        "head_full": "{n} സ്റ്റേഷനുകളിൽ {thr} മി.മീ.യിൽ കൂടുതൽ മഴ; ജില്ലകളിൽ {dist} {avg} മി.മീ. ശരാശരിയോടെ മുന്നിൽ.",
        "head_one": "1 സ്റ്റേഷനിൽ {thr} മി.മീ.യിൽ കൂടുതൽ മഴ; ജില്ലകളിൽ {dist} {avg} മി.മീ. ശരാശരിയോടെ മുന്നിൽ.",
        "head_dist": "ജില്ലകളിൽ {dist} {avg} മി.മീ. ശരാശരിയോടെ മുന്നിൽ.",
        "head_none": "സംസ്ഥാനത്ത് കാര്യമായ മഴ രേഖപ്പെടുത്തിയില്ല.",
        "c_rep": "റിപ്പോർട്ട് ചെയ്ത സ്റ്റേഷനുകൾ", "c_rep_sub": "{n} ജില്ലകൾ",
        "c_rain": "മഴ ലഭിച്ച സ്റ്റേഷനുകൾ", "c_rain_daily": "ശരാശരി {x} മി.മീ.", "c_rain_accum": "ശരാശരി {x} മഴ ദിവസങ്ങൾ",
        "c_thr_daily": "≥ 64.5 മി.മീ. സ്റ്റേഷനുകൾ", "c_thr_daily_sub": "കനത്ത മഴ",
        "c_thr_accum": "≥ 100 മി.മീ. സ്റ്റേഷനുകൾ", "c_thr_accum_sub": "ആകെ മഴ",
        "c_max": "ഏറ്റവും കൂടുതൽ",
        "map_note": "IDW ഉപരിതലം (ഇന്റർപൊളേഷൻ)",
        "ins_avg": "ജില്ലാ ശരാശരി", "ins_max": "ഏറ്റവും കൂടിയ സ്റ്റേഷൻ",
        "top_st": "മുന്നിലുള്ള 5 സ്റ്റേഷനുകൾ (മി.മീ.)", "top_di": "മുന്നിലുള്ള 5 ജില്ലകൾ · ശരാശരി (മി.മീ.)",
        "legend": "മഴ വിഭാഗങ്ങൾ · ഓരോന്നിലും സ്റ്റേഷനുകൾ", "legend_mm": "എല്ലാം മി.മീ.യിൽ", "zero": "0 മി.മീ.",
        "source": "ഉറവിടം: {src} · IDW p={p}, ആരം ≈ {r} കി.മീ.", "made": "തയ്യാറാക്കിയത്: {d} {m} {y}, {t}",
        "brand": "അവതരണം: MasRainman",
        "cap_title": "🌧 മഴ റിപ്പോർട്ട്", "cap_top": "മുന്നിലുള്ള 5 സ്റ്റേഷനുകൾ:", "cap_src": "ഉറവിടം: {src}",
    },
}


def date_str(L, d, year=False):
    m = L["months"][d.month - 1]
    if L is LANGS["en"]:
        return f"{d.day} {m}" + (f" {d.year}" if year else "")
    return f"{m} {d.day}" + (f", {d.year}" if year else "")
