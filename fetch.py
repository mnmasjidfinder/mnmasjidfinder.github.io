#!/usr/bin/env python3
"""Pull Minnesota masajid, their prayer timings and events into data/masajid.json.

Sources, in order of trust:
  1. Masjidal / Athan+ API     -> live adhan + iqamah + Jumu'ah times (set by the masjid)
  2. Mohid pages (us.mohid.co) -> Jumu'ah khutbah/iqamah, daily iqamah, events
  3. WordPress "Events Calendar" REST API on the masjid's site -> upcoming events
  4. Manual values in mosques_seed.json
  5. Calculated adhan times (ISNA, 15 deg / 15 deg, standard Asr) from coordinates
     -> used only when no live source exists. These are NOT iqamah times.

The mosque list itself comes from OpenStreetMap (Overpass API) plus mosques_seed.json.
Standard library only. Run:  python3 fetch.py
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import html
import json
import math
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TZ = ZoneInfo("America/Chicago")
UA = "Mozilla/5.0 (mn-masjid-finder; personal prayer-times tool)"
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OSM_QUERY = """[out:json][timeout:120];
area["ISO3166-2"="US-MN"]->.a;
(nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);
 nwr["building"="mosque"]["name"](area.a););
out center tags;"""
NOMINATIM = "https://nominatim.openstreetmap.org"
GEO_UA = "mn-masjid-finder/0.1 (personal prayer-times tool)"
_last_geo = [0.0]

# ---------------------------------------------------------------- HTTP helpers

_CTX = ssl.create_default_context()


def http_get(url: str, data: bytes | None = None, timeout: int = 25, ua: str = UA) -> str:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read().decode("utf-8", "ignore")


def http_json(url: str, **kw):
    return json.loads(http_get(url, **kw))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- geography

# (city, lat, lon, region)
CITIES = [
    ("Minneapolis", 44.9778, -93.2650, "Minneapolis"),
    ("Saint Paul", 44.9537, -93.0900, "St. Paul & East Metro"),
    ("Bloomington", 44.8408, -93.2983, "South Metro"),
    ("Burnsville", 44.7677, -93.2777, "South Metro"),
    ("Eagan", 44.8041, -93.1669, "South Metro"),
    ("Apple Valley", 44.7319, -93.2177, "South Metro"),
    ("Lakeville", 44.6497, -93.2427, "South Metro"),
    ("Rosemount", 44.7394, -93.1258, "South Metro"),
    ("Inver Grove Heights", 44.8480, -93.0427, "South Metro"),
    ("Prior Lake", 44.7133, -93.4227, "South Metro"),
    ("Shakopee", 44.7974, -93.5273, "South Metro"),
    ("Chaska", 44.7894, -93.6022, "West Metro"),
    ("Afton", 44.9028, -92.7835, "St. Paul & East Metro"),
    ("East Bethel", 45.3194, -93.2025, "North Metro"),
    ("St. Anthony", 45.0205, -93.2180, "North Metro"),
    ("Circle Pines", 45.1486, -93.1516, "North Metro"),
    ("Otsego", 45.2741, -93.5913, "North Metro"),
    ("Minnetonka", 44.9133, -93.5033, "West Metro"),
    ("Savage", 44.7792, -93.3363, "South Metro"),
    ("Richfield", 44.8833, -93.2830, "South Metro"),
    ("Eden Prairie", 44.8547, -93.4708, "West Metro"),
    ("Edina", 44.8897, -93.3499, "West Metro"),
    ("Hopkins", 44.9247, -93.4049, "West Metro"),
    ("St. Louis Park", 44.9483, -93.3480, "West Metro"),
    ("Plymouth", 45.0105, -93.4555, "West Metro"),
    ("Maple Grove", 45.0725, -93.4558, "North Metro"),
    ("Brooklyn Park", 45.0941, -93.3563, "North Metro"),
    ("Brooklyn Center", 45.0761, -93.3327, "North Metro"),
    ("Columbia Heights", 45.0408, -93.2630, "North Metro"),
    ("Fridley", 45.0861, -93.2633, "North Metro"),
    ("Coon Rapids", 45.1200, -93.2877, "North Metro"),
    ("Blaine", 45.1608, -93.2349, "North Metro"),
    ("New Brighton", 45.0655, -93.2019, "North Metro"),
    ("Roseville", 45.0061, -93.1566, "St. Paul & East Metro"),
    ("Maplewood", 44.9530, -92.9952, "St. Paul & East Metro"),
    ("Woodbury", 44.9239, -92.9594, "St. Paul & East Metro"),
    ("Oakdale", 44.9630, -92.9649, "St. Paul & East Metro"),
    ("Rochester", 44.0121, -92.4802, "Southern Minnesota"),
    ("Mankato", 44.1636, -93.9994, "Southern Minnesota"),
    ("Owatonna", 44.0839, -93.2260, "Southern Minnesota"),
    ("Faribault", 44.2950, -93.2688, "Southern Minnesota"),
    ("Northfield", 44.4583, -93.1616, "Southern Minnesota"),
    ("Austin", 43.6666, -92.9746, "Southern Minnesota"),
    ("Winona", 44.0499, -91.6393, "Southern Minnesota"),
    ("Albert Lea", 43.6480, -93.3683, "Southern Minnesota"),
    ("Worthington", 43.6199, -95.5964, "Southern Minnesota"),
    ("Willmar", 45.1219, -95.0433, "Central Minnesota"),
    ("St. Cloud", 45.5579, -94.1632, "Central Minnesota"),
    ("Marshall", 44.4469, -95.7884, "Southern Minnesota"),
    ("Duluth", 46.7867, -92.1005, "Northern Minnesota"),
    ("Moorhead", 46.8738, -96.7678, "Northern Minnesota"),
    ("East Grand Forks", 47.9300, -97.0245, "Northern Minnesota"),
    ("Bemidji", 47.4736, -94.8803, "Northern Minnesota"),
]
METRO_CITY_REGION = {c[0].lower(): c[3] for c in CITIES}
METRO_CITY_REGION.update({"st. paul": "St. Paul & East Metro", "st paul": "St. Paul & East Metro",
                          "saint cloud": "Central Minnesota", "st cloud": "Central Minnesota"})


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def nearest_city(lat, lon):
    return min(CITIES, key=lambda c: km(lat, lon, c[1], c[2]))


def locate(m):
    """Fill city + region."""
    lat, lon = m.get("lat"), m.get("lon")
    city = m.get("city")
    if not city:  # a city named in the masjid's own name beats the nearest-city guess
        nm = m["name"].lower().replace("_", " ")
        city = next((c[0] for c in CITIES if c[0].lower() in nm), None)
    if not city and lat is not None:
        c = nearest_city(lat, lon)
        city = c[0] if km(lat, lon, c[1], c[2]) < 15 else f"near {c[0]}"
    m["city"] = city or "Unknown"
    key = (city or "").lower().replace("near ", "")
    region = METRO_CITY_REGION.get(key)
    if not region and lat is not None:
        region = nearest_city(lat, lon)[3]
    m["region"] = m.get("region") or region or "Other"


# ---------------------------------------------------------------- prayer calc

def _d(x):  # deg helpers
    return math.radians(x)


def _r(x):
    return math.degrees(x)


def calc_times(date: dt.date, lat: float, lon: float, fajr_angle=15.0, isha_angle=15.0, asr_factor=1):
    """Astronomical adhan times (local wall clock, America/Chicago). Algorithm per praytimes.org."""
    jd = date.toordinal() + 1721424.5 - lon / 360.0

    def sun(jd_):
        D = jd_ - 2451545.0
        g = (357.529 + 0.98560028 * D) % 360
        q = (280.459 + 0.98564736 * D) % 360
        L = (q + 1.915 * math.sin(_d(g)) + 0.020 * math.sin(_d(2 * g))) % 360
        e = 23.439 - 0.00000036 * D
        RA = _r(math.atan2(math.cos(_d(e)) * math.sin(_d(L)), math.cos(_d(L)))) / 15
        eqt = q / 15 - (RA % 24)
        eqt = (eqt + 12) % 24 - 12
        decl = _r(math.asin(math.sin(_d(e)) * math.sin(_d(L))))
        return decl, eqt

    def mid_day(t):
        _, eqt = sun(jd + t / 24)
        return (12 - eqt) % 24

    def angle_time(angle, t, ccw):
        decl, _ = sun(jd + t / 24)
        noon = mid_day(t)
        cosv = (-math.sin(_d(angle)) - math.sin(_d(decl)) * math.sin(_d(lat))) / (
            math.cos(_d(decl)) * math.cos(_d(lat)))
        cosv = max(-1.0, min(1.0, cosv))
        v = _r(math.acos(cosv)) / 15
        return noon + (-v if ccw else v)

    def asr_time(t):
        decl, _ = sun(jd + t / 24)
        ang = -_r(math.atan(1 / (asr_factor + math.tan(abs(_d(lat - decl))))))
        return angle_time(ang, t, False)

    t = {"fajr": 5, "sunrise": 6, "dhuhr": 12, "asr": 13, "maghrib": 18, "isha": 18}
    for _ in range(2):
        t = {
            "fajr": angle_time(fajr_angle, t["fajr"], True),
            "sunrise": angle_time(0.833, t["sunrise"], True),
            "dhuhr": mid_day(t["dhuhr"]),
            "asr": asr_time(t["asr"]),
            "maghrib": angle_time(0.833, t["maghrib"], False),
            "isha": angle_time(isha_angle, t["isha"], False),
        }
    t["dhuhr"] += 1 / 60  # 1 min safety after zawal
    noon_local = dt.datetime.combine(date, dt.time(12), TZ)
    offset = noon_local.utcoffset().total_seconds() / 3600
    out = {}
    for k, v in t.items():
        h = (v + offset - lon / 15) % 24
        mins = int(round(h * 60)) % 1440
        out[k] = fmt12(mins // 60, mins % 60)
    return out


# ---------------------------------------------------------------- time parsing

def fmt12(h, m):
    ap = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {ap}"


def norm_time(s: str | None) -> str | None:
    """'1:00PM' / '13:00' / '01:00 pm' -> '1:00 PM'. Returns None if unparseable."""
    if not s:
        return None
    s = s.strip().upper().replace(".", "")
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", s)
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2]), m[3]
    if ap == "PM" and h < 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return fmt12(h, mi)


def minutes(s: str | None) -> int | None:
    s = norm_time(s)
    if not s:
        return None
    hm, ap = s.split()
    h, m = map(int, hm.split(":"))
    h = h % 12 + (12 if ap == "PM" else 0)
    return h * 60 + m


# ---------------------------------------------------------------- sources

def src_masjidal(mid: str, today: dt.date):
    d0 = today.isoformat()
    d1 = (today + dt.timedelta(days=6)).isoformat()
    j = http_json(f"https://masjidal.com/api/v1/time/range?masjid_id={urllib.parse.quote(mid)}"
                  f"&from_date={d0}&to_date={d1}")
    if j.get("status") != "success":
        raise RuntimeError(f"masjidal status {j.get('status')}")
    salah, iqamah = j["data"]["salah"], j["data"]["iqamah"]
    days = []
    jumuah = []
    for s, q in zip(salah, iqamah):
        days.append({
            "date": s["date"],
            "hijri": f'{s.get("hijri_date", "")} {s.get("hijri_month", "")}'.strip(),
            "adhan": {k2: norm_time(s.get(k1)) for k1, k2 in
                      [("fajr", "fajr"), ("sunrise", "sunrise"), ("zuhr", "dhuhr"),
                       ("asr", "asr"), ("maghrib", "maghrib"), ("isha", "isha")]},
            "iqamah": {k2: norm_time(q.get(k1)) for k1, k2 in
                       [("fajr", "fajr"), ("zuhr", "dhuhr"), ("asr", "asr"),
                        ("maghrib", "maghrib"), ("isha", "isha")]},
        })
        if not jumuah:
            for k in sorted(q):
                if k.startswith("jummah") and norm_time(q[k]) and norm_time(q[k]) not in [j["start"] for j in jumuah]:
                    jumuah.append({"khutbah": None, "iqamah": None, "start": norm_time(q[k])})
    return {"days": days, "jumuah": jumuah, "source": "Masjidal (set by masjid)"}


def _text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h).replace("\xa0", " "))


def src_mohid(url: str):
    h = http_get(url)
    t = _text(h)
    out = {"source": "Mohid page"}
    # Jumu'ah: "Friday Khutba 1 12:15 PM ... Friday Iqama 1 12:45 PM"
    kh = dict(re.findall(r"Friday Khutba\s*(\d)\s*(\d{1,2}:\d{2}\s*[AP]M)", t, re.I))
    iq = dict(re.findall(r"Friday Iqama\s*(\d)\s*(\d{1,2}:\d{2}\s*[AP]M)", t, re.I))
    out["jumuah"] = [{"khutbah": norm_time(kh.get(n)), "iqamah": norm_time(iq.get(n)),
                      "start": norm_time(kh.get(n) or iq.get(n))}
                     for n in sorted(set(kh) | set(iq))]
    # Daily: "Fajar 06:00 AM 05:36 AM" (iqama then azaan)
    names = {"fajar": "fajr", "fajr": "fajr", "duhar": "dhuhr", "dhuhr": "dhuhr", "zuhr": "dhuhr",
             "asr": "asr", "maghrib": "maghrib", "isha": "isha"}
    iqd, adh = {}, {}
    for nm, a, b in re.findall(r"\b(Fajar|Fajr|Duhar|Dhuhr|Zuhr|Asr|Maghrib|Isha)\s+"
                               r"(\d{1,2}:\d{2}\s*[AP]M)\s+(\d{1,2}:\d{2}\s*[AP]M)", t, re.I):
        k = names[nm.lower()]
        iqd.setdefault(k, norm_time(a))
        adh.setdefault(k, norm_time(b))
    if iqd:
        out["days"] = [{"date": dt.date.today().strftime("%A, %b %-d, %Y"), "adhan": adh, "iqamah": iqd}]
    # Events: embedded fullCalendar JSON
    m = re.search(r"events:\s*(\[.*?\])\s*,\s*timeFormat", h, re.S)
    events = []
    if m:
        try:
            for e in json.loads(m[1]):
                events.append({"title": html.unescape(e.get("title", "")), "start": e.get("start"),
                               "url": e.get("url") or url})
        except json.JSONDecodeError:
            pass
    out["events"] = events
    return out


def src_wp_events(base: str, today: dt.date):
    base = base.rstrip("/")
    j = http_json(f"{base}/wp-json/tribe/events/v1/events?per_page=25&start_date={today.isoformat()}")
    return [{"title": html.unescape(re.sub(r"<[^>]+>", "", e.get("title", ""))),
             "start": e.get("start_date"), "end": e.get("end_date"), "url": e.get("url"),
             "venue": (e.get("venue") or {}).get("venue") if isinstance(e.get("venue"), dict) else None}
            for e in j.get("events", [])]


def discover(website: str):
    """Scan a masjid homepage for known timing widgets / event feeds."""
    found = {}
    try:
        h = http_get(website, timeout=20)
    except Exception as e:
        return {"site_error": str(e)[:120]}
    ids = re.findall(r"masjid_id=([A-Za-z0-9]{6,12})", h)
    if ids:
        found["masjidal_id"] = ids[0]
    mo = re.search(r"https?://(?:us\.)?mohid\.co/[a-z]{2}/[\w-]+/[\w-]+", h)
    if mo:
        found["mohid_url"] = mo[0]
    if "tribe-events" in h or "tribe_events" in h:
        p = urllib.parse.urlparse(website)
        found["events_wp"] = f"{p.scheme}://{p.netloc}"
    return found


# ---------------------------------------------------------------- pipeline

def load_osm():
    cache = DATA / "osm_cache.json"
    body = urllib.parse.urlencode({"data": OSM_QUERY}).encode()
    for attempt in range(1):
        for url in OVERPASS:
            try:
                j = http_json(url, data=body, timeout=150, ua="mn-masjid-finder/0.1")
                if "elements" in j:
                    cache.write_text(json.dumps(j))
                    log(f"OSM: {len(j['elements'])} places from {url}")
                    return j["elements"], "live"
            except Exception as e:
                log(f"OSM {url}: {str(e)[:80]}")
        time.sleep(5 * (attempt + 1))
    if cache.exists():
        try:
            elements = json.loads(cache.read_text())["elements"]
            log("OSM unavailable, using cached list")
            return elements, "cached"
        except (ValueError, KeyError) as e:
            log(f"OSM cache unreadable: {e}")
    # Stop without writing anything, so the last good site stays up.
    raise SystemExit("Could not reach OpenStreetMap and no usable cache exists.")


def osm_to_mosque(e):
    t = e.get("tags", {})
    if not t.get("name"):
        return None
    c = e.get("center", e)
    street = " ".join(x for x in [t.get("addr:housenumber"), t.get("addr:street")] if x)
    if t.get("addr:unit"):
        street += f" {t['addr:unit']}"
    city = t.get("addr:city")
    addr = ", ".join(x for x in [street, city, " ".join(
        x for x in ["MN", t.get("addr:postcode")] if x) if (street or city) else None] if x)
    return {
        "osm_id": f"{e['type']}/{e['id']}",
        "name": t["name"],
        "address": addr or None,
        "city": city,
        "lat": c.get("lat"), "lon": c.get("lon"),
        "phone": t.get("phone") or t.get("contact:phone"),
        "website": t.get("website") or t.get("contact:website"),
        "denomination": t.get("denomination"),
    }


class Geo:
    """Nominatim geocoding with an on-disk cache (max 1 request/second, per OSM usage policy)."""

    def __init__(self):
        self.path = DATA / "geocode_cache.json"
        self.cache = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _get(self, key, url):
        if key in self.cache:
            return self.cache[key]
        wait = 1.1 - (time.time() - _last_geo[0])
        if wait > 0:
            time.sleep(wait)
        _last_geo[0] = time.time()
        try:
            self.cache[key] = http_json(url, ua=GEO_UA, timeout=20)
        except Exception as e:
            log(f"geocode {key}: {str(e)[:80]}")
            return None
        self.path.write_text(json.dumps(self.cache, indent=1))
        return self.cache[key]

    def forward(self, address):
        q = urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        r = self._get("f:" + address, f"{NOMINATIM}/search?{q}")
        return (float(r[0]["lat"]), float(r[0]["lon"])) if r else None

    def city(self, lat, lon):
        q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14})
        r = self._get(f"r:{lat:.5f},{lon:.5f}", f"{NOMINATIM}/reverse?{q}") or {}
        a = r.get("address", {})
        return a.get("city") or a.get("town") or a.get("village") or a.get("hamlet")


# ---------------------------------------------------------------- Masjidal directory

MN_STATE = {"mn", "minnesota"}


def load_masjidal_directory():
    """Every masjid registered on Masjidal, filtered to Minnesota. ~47 pages; cached for when it's down."""
    cache = DATA / "masjidal_cache.json"
    try:
        out, page, pages = [], 1, 1
        while page <= pages:
            req = urllib.request.Request(f"https://masjidal.com/api/v2/masjids?per-page=50&page={page}",
                                         headers={"User-Agent": GEO_UA})
            with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
                d = json.loads(r.read())
            pages = d["_meta"]["pageCount"]
            out += [i for i in d["items"] if (i.get("state") or "").strip().lower() in MN_STATE]
            page += 1
            time.sleep(0.3)
        cache.write_text(json.dumps(out, indent=1))
        log(f"Masjidal directory: {len(out)} Minnesota masjids")
        return out
    except Exception as e:
        log(f"Masjidal directory unavailable ({str(e)[:60]}), using cache")
        return json.loads(cache.read_text()) if cache.exists() else []


def _clean(v):
    v = (v or "").strip()
    return None if v in ("", "-") else re.sub(r"\s+", " ", v)


def _nice_name(n):
    n = _clean(n) or ""
    n = re.sub(r",?\s+MN$", "", n)  # "… Rochester MN" -> "… Rochester"
    if n.isupper():  # "MASJID AL-HIKMA ASSOCIATION" -> "Masjid Al-Hikma Association"
        n = re.sub(r"[A-Za-z][A-Za-z']*", lambda w: w[0][0].upper() + w[0][1:].lower(), n)
        n = re.sub(r"\b(Icbmn|Iccc|Mas|Nwicc|Isfm|Mfic)\b", lambda w: w[0].upper(), n)
    return n


def _better_name(osm_name, reg_name, reg_was_caps):
    """Prefer the masjid's registered name unless it's a shouted acronym or less descriptive."""
    return reg_name if (not reg_was_caps and len(reg_name) >= len(osm_name)) else osm_name


def masjidal_to_mosque(i):
    city = (_clean(i.get("city")) or "").replace(", MN", "")
    street = _clean(i.get("address")) or ""
    street = street.split(",")[0]
    addr = ", ".join(x for x in [street, city, " ".join(x for x in ["MN", _clean(i.get("zipcode"))] if x)] if x)
    try:
        lat, lon = float(i["latitude"]), float(i["longitude"])
    except (KeyError, TypeError, ValueError):
        lat = lon = None
    return {"name": _nice_name(i.get("name")), "caps": (_clean(i.get("name")) or "").isupper(), "address": addr, "city": city or None, "lat": lat, "lon": lon,
            "phone": _clean(i.get("phone")), "website": _clean(i.get("website_url")),
            "masjidal_id": i["id"], "from_directory": True}


_DIRS = r"(?:n|s|e|w|ne|nw|se|sw|north|south|east|west|northeast|northwest|southeast|southwest)"


def addr_key(a):
    """'8201 Park Avenue South' and '8201 Park Ave S' -> '8201 park'."""
    m = re.match(rf"\s*(\d+)\s+(?:{_DIRS}\.?\s+)?([a-z0-9]+)", (a or "").lower())
    return f"{m[1]} {m[2]}" if m else None


_GENERIC = {"masjid", "masjed", "mosque", "islamic", "center", "centre", "of", "the", "mn", "minnesota",
            "community", "cultural", "association", "al", "inc", "mas"}


def core_name(n):
    words = re.findall(r"[a-z0-9]+", (n or "").lower().replace("-", " "))
    return " ".join(w for w in words if w not in _GENERIC)


def find_same(m, mosques, near_km=0.06):
    """The entry in `mosques` that is the same masjid as `m`, if any.
    Two Masjidal directory entries are only merged when they share a street address."""
    for o in mosques:
        if o is m:
            continue
        if m.get("masjidal_id") and o.get("masjidal_id") == m["masjidal_id"]:
            return o
        if m.get("osm_id") and o.get("osm_id") == m["osm_id"]:
            return o
    have = m.get("lat") is not None
    for o in mosques:
        if o is m or o.get("lat") is None or not have:
            continue
        d = km(m["lat"], m["lon"], o["lat"], o["lon"])
        ka, kb = addr_key(m.get("address")), addr_key(o.get("address"))
        if ka and ka == kb and d < 3:
            return o
        if m.get("from_directory") and o.get("from_directory"):
            continue
        if d < near_km:
            return o
        cm, co = core_name(m.get("name")), core_name(o.get("name"))
        if cm and cm == co and d < 10:
            return o
    return None


def build_list():
    seed_file = json.loads((ROOT / "mosques_seed.json").read_text())
    seed = seed_file["mosques"]
    exclude = set(seed_file.get("exclude_masjidal", []))
    elements, osm_state = load_osm()
    mosques = [m for m in (osm_to_mosque(e) for e in elements) if m]
    geo = Geo()

    # 1. Masjidal directory: attach IDs to masajid we already have, add the rest.
    for i in load_masjidal_directory():
        if i["id"] in exclude:
            continue
        d = masjidal_to_mosque(i)
        tgt = find_same(d, mosques)
        if tgt is None:
            mosques.append(d)
        elif not tgt.get("masjidal_id"):
            tgt["masjidal_id"] = d["masjidal_id"]
            for k in ("phone", "website"):
                tgt[k] = tgt.get(k) or d[k]
            if tgt.get("osm_id") and core_name(tgt["name"]) != core_name(d["name"]):
                keep = _better_name(tgt["name"], d["name"], d["caps"])
                tgt["aka"] = d["name"] if keep == tgt["name"] else tgt["name"]
                tgt["name"] = keep
            if not tgt.get("address"):
                tgt["address"] = d["address"]

    # 2. Hand-maintained entries in mosques_seed.json win over everything.
    by_osm = {m["osm_id"]: m for m in mosques if m.get("osm_id")}
    by_name = {m["name"].lower(): m for m in mosques}
    for s in seed:
        s = dict(s)
        if s.get("lat") is None and s.get("address"):
            ll = geo.forward(s["address"])
            if ll:
                s["lat"], s["lon"] = ll
        tgt = by_osm.get(s.get("osm_id")) or by_name.get(s.get("name", "").lower())
        if not tgt and not s.get("osm_id"):
            tgt = find_same(s, mosques, near_km=0.08)
            if tgt and core_name(tgt["name"]) != core_name(s.get("name", "")):
                s.setdefault("aka", tgt["name"])
        if tgt:
            if tgt.get("lat") is not None:
                s.pop("lat", None), s.pop("lon", None)  # keep OSM's precise point
            tgt.update({k: v for k, v in s.items() if v is not None})
        else:
            mosques.append(s)
    for m in mosques:
        if not m.get("city") and m.get("lat") is not None:
            m["city"] = geo.city(m["lat"], m["lon"])
        c = (m.get("city") or "").strip()
        m["city"] = {"saint paul": "St. Paul", "st paul": "St. Paul", "st cloud": "St. Cloud",
                     "saint cloud": "St. Cloud", "st anthony": "St. Anthony",
                     "saint anthony": "St. Anthony", "east grand fork": "East Grand Forks"}.get(c.lower(), c) or None
        locate(m)
        m["id"] = re.sub(r"[^a-z0-9]+", "-", (m["name"] + "-" + m["city"]).lower()).strip("-")
    return mosques, osm_state


def enrich(m, today):
    m.setdefault("events", [])
    m["sources"] = []
    m["errors"] = []
    if m.get("website") and not (m.get("masjidal_id") and m.get("events_wp")):
        for k, v in discover(m["website"]).items():
            if k == "site_error":
                m["errors"].append(f"website: {v}")
            else:
                m.setdefault(k, v)
    if m.get("masjidal_id"):
        try:
            r = src_masjidal(m["masjidal_id"], today)
            m["days"], m["timing_source"] = r["days"], r["source"]
            if r["jumuah"]:
                m["jumuah"], m["jumuah_source"] = r["jumuah"], r["source"]
            m["sources"].append("masjidal")
        except Exception as e:
            m["errors"].append(f"masjidal: {str(e)[:100]}")
    if m.get("mohid_url"):
        try:
            r = src_mohid(m["mohid_url"])
            if r.get("jumuah") and not m.get("jumuah_source"):
                m["jumuah"], m["jumuah_source"] = r["jumuah"], r["source"]
            if r.get("days") and not m.get("days"):
                m["days"], m["timing_source"] = r["days"], r["source"]
            m["events"] += r.get("events", [])
            m["sources"].append("mohid")
        except Exception as e:
            m["errors"].append(f"mohid: {str(e)[:100]}")
    if m.get("events_wp"):
        try:
            m["events"] += src_wp_events(m["events_wp"], today)
            m["sources"].append("wp-events")
        except Exception as e:
            m["errors"].append(f"events: {str(e)[:100]}")
    if m.get("jumuah") and not m.get("jumuah_source"):  # manual values from seed
        for j in m["jumuah"]:
            j["start"] = norm_time(j.get("start") or j.get("khutbah") or j.get("iqamah"))
        m["jumuah_source"] = "Entered by hand" + (f" – {m['jumuah_note']}" if m.get("jumuah_note") else "")
    if not m.get("days") and m.get("lat") is not None:
        m["days"] = [{"date": (today + dt.timedelta(days=i)).strftime("%A, %b %-d, %Y"),
                      "adhan": calc_times(today + dt.timedelta(days=i), m["lat"], m["lon"]),
                      "iqamah": {}} for i in range(7)]
        m["timing_source"] = "Calculated adhan (ISNA) – confirm iqamah with masjid"
    # keep only upcoming events, sorted
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    m["events"] = sorted([e for e in m["events"] if (e.get("start") or "")[:10] >= now],
                         key=lambda e: e.get("start") or "")
    m["jumuah"] = m.get("jumuah") or []
    for j in m["jumuah"]:
        j["start_min"] = minutes(j.get("start"))
    m["live"] = "masjidal" in m["sources"] or bool(m.get("jumuah_source", "").startswith("Mohid"))
    return m


def main():
    DATA.mkdir(exist_ok=True)
    today = dt.datetime.now(TZ).date()
    mosques, osm_state = build_list()
    log(f"{len(mosques)} masajid; fetching timings…")
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        mosques = list(ex.map(lambda m: enrich(m, today), mosques))
    mosques.sort(key=lambda m: (m["region"], m["name"]))
    out = {
        "generated_at": dt.datetime.now(TZ).isoformat(timespec="minutes"),
        "osm": osm_state,
        "count": len(mosques),
        "mosques": mosques,
    }
    (DATA / "masajid.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (DATA / "masajid.js").write_text("window.MASAJID = " + json.dumps(out, ensure_ascii=False) + ";\n")
    live = sum(1 for m in mosques if m["live"])
    jm = sum(1 for m in mosques if m["jumuah"])
    ev = sum(len(m["events"]) for m in mosques)
    log(f"Done: {len(mosques)} masajid, {live} with live times, {jm} with Jumu'ah times, {ev} upcoming events.")
    for m in mosques:
        if m["errors"]:
            log(f"  ! {m['name']}: {'; '.join(m['errors'])}")


if __name__ == "__main__":
    main()
