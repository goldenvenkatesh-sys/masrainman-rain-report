"""Tamil names and spelling normalisation for Tamil Nadu districts."""
import re

# TN-SMART spelling -> Tamil
DISTRICT_TA = {
    "Ariyalur": "அரியலூர்",
    "Chengalpattu": "செங்கல்பட்டு",
    "Chennai": "சென்னை",
    "Coimbatore": "கோயம்புத்தூர்",
    "Cuddalore": "கடலூர்",
    "Dharmapuri": "தருமபுரி",
    "Dindigul": "திண்டுக்கல்",
    "Erode": "ஈரோடு",
    "Kallakurichi": "கள்ளக்குறிச்சி",
    "Kancheepuram": "காஞ்சிபுரம்",
    "Kanniyakumari": "கன்னியாகுமரி",
    "Karur": "கரூர்",
    "Krishnagiri": "கிருஷ்ணகிரி",
    "Madurai": "மதுரை",
    "Mayiladuthurai": "மயிலாடுதுறை",
    "Nagapattinam": "நாகப்பட்டினம்",
    "Namakkal": "நாமக்கல்",
    "Nilgiris": "நீலகிரி",
    "Perambalur": "பெரம்பலூர்",
    "Pudukkottai": "புதுக்கோட்டை",
    "Ramanathapuram": "இராமநாதபுரம்",
    "Ranipet": "இராணிப்பேட்டை",
    "Salem": "சேலம்",
    "Sivagangai": "சிவகங்கை",
    "Tenkasi": "தென்காசி",
    "Thanjavur": "தஞ்சாவூர்",
    "Theni": "தேனி",
    "Thiruchirappalli": "திருச்சிராப்பள்ளி",
    "Thiruvarur": "திருவாரூர்",
    "Thoothukudi": "தூத்துக்குடி",
    "Tirunelveli": "திருநெல்வேலி",
    "Tirupathur": "திருப்பத்தூர்",
    "Tiruppur": "திருப்பூர்",
    "Tiruvallur": "திருவள்ளூர்",
    "Tiruvannamalai": "திருவண்ணாமலை",
    "Vellore": "வேலூர்",
    "Villupuram": "விழுப்புரம்",
    "Virudhunagar": "விருதுநகர்",
}

# other spellings (boundary file etc.) -> TN-SMART spelling
_ALIASES = {
    "thenilgiris": "Nilgiris",
    "tuticorin": "Thoothukudi",
    "thoothukkudi": "Thoothukudi",
    "kanchipuram": "Kancheepuram",
    "kanyakumari": "Kanniyakumari",
    "thiruvallur": "Tiruvallur",
    "tiruchirappalli": "Thiruchirappalli",
    "trichy": "Thiruchirappalli",
    "sivaganga": "Sivagangai",
    "viluppuram": "Villupuram",
    "tiruvarur": "Thiruvarur",
    "tirupattur": "Tirupathur",
    "thirupathur": "Tirupathur",
    "tiruppur": "Tiruppur",
    "tirupur": "Tiruppur",
}
_KEY = {re.sub(r"[^a-z]", "", k.lower()): k for k in DISTRICT_TA}
_KEY.update(_ALIASES)


def canon(name: str) -> str:
    """Return the TN-SMART spelling of a district name (or the input if unknown)."""
    k = re.sub(r"[^a-z]", "", (name or "").lower())
    return _KEY.get(k, (name or "").strip())


def ta(name: str) -> str:
    c = canon(name)
    return DISTRICT_TA.get(c, c)


MONTHS_TA = ["ஜன.", "பிப்.", "மார்.", "ஏப்.", "மே", "ஜூன்", "ஜூலை", "ஆக.", "செப்.", "அக்.", "நவ.", "டிச."]


def date_ta(d, with_year=False):
    s = f"{MONTHS_TA[d.month - 1]} {d.day}"
    return f"{s}, {d.year}" if with_year else s


def time_ta(dt):
    h = dt.hour
    part = "காலை" if 5 <= h < 12 else "மதியம்" if 12 <= h < 15 else "மாலை" if 15 <= h < 19 else "இரவு"
    h12 = h % 12 or 12
    return f"{part} {h12}:{dt.minute:02d}"
