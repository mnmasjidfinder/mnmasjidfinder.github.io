# MN Masjid Finder

Pulls Minnesota masajid, their prayer timings, Jumu'ah times and events, and shows them in a searchable page.

## Use

```bash
python3 fetch.py                      # refresh data/masajid.json (about 1 min)
python3 -m http.server 8765           # then open http://localhost:8765
```

You can search by name, city or address, filter by region, and filter by a Jumu'ah time window (for example 12:00–12:30). You can sort by earliest Jumu'ah. The "Live times only" toggle hides calculated times.

## Where the data comes from

| Source | Gives | How it's found |
|---|---|---|
| OpenStreetMap (Overpass) | mosque list, address, coordinates, website | every run; falls back to `data/osm_cache.json` |
| Masjidal / Athan+ API | adhan, iqamah and Jumu'ah, 7 days | `masjid_id` auto-detected from the masjid website's widget, or set in seed |
| Mohid (us.mohid.co) | Jumu'ah khutbah/iqamah, daily iqamah, events | link auto-detected on website, or `mohid_url` in seed |
| WordPress Events Calendar | upcoming events | auto-detected, or `events_wp` in seed |
| Calculation (ISNA 15°/15°) | adhan only | fallback for everything else |
| OSM Nominatim | coordinates for hand-added addresses, city names | cached in `data/geocode_cache.json` |

MAS Minnesota's eight masjids and ETCIC all publish live times through Masjidal. Their IDs came from the widgets on masmn.org and etcic.org.

## Adding or fixing a masjid

Edit `mosques_seed.json`. You can add a mosque missing from OpenStreetMap, attach a `masjidal_id` or `mohid_url`, or type in Jumu'ah times by hand:

```json
{"osm_id": "way/313727189", "jumuah": [{"khutbah": "1:00 PM"}, {"khutbah": "2:00 PM"}], "jumuah_note": "confirmed by phone 2026-11"}
```

Then re-run `python3 fetch.py`.

## Publishing

**GitHub Pages (public, refreshes daily).** `.github/workflows/refresh.yml` runs `fetch.py` every morning (and on each push or a manual "Run workflow"), commits the refreshed `data/` back, and deploys `index.html` + data to GitHub Pages. Visitors' browsers also pull the latest Masjidal times directly when the page opens, so those stay current between runs.

One-time setup: in the repo's **Settings → Pages**, set **Source** to **GitHub Actions**.

**claude.ai artifact (snapshot).** `python3 build_artifact.py` writes `dist/masjid-finder.html`, a single file with the data inlined, for republishing the artifact.
