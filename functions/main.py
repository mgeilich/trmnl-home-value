"""Cloud Run functions (Python 3.12) for the Home Value Estimate TRMNL recipe.

Project zestimate-trmnl-2026 (the Firebase project), region us-central1.

home_value    public.  GET ?zip=01983&price=412000&date=2019-06
              Returns the estimate JSON the recipe renders (always HTTP 200).
refresh_zhvi  private (no unauthenticated access). Downloads Zillow's ZIP-level ZHVI CSV
              and stores it in Firestore. Run monthly by Cloud Scheduler.

Firestore:
  zhvi_zip3/{zip3}   {"z": {zip5: {"s": "YYYY-MM", "v": "v1,v2,...", "city", "state"}}}
  zhvi_meta/latest   {"month": "YYYY-MM", "zips": n, "groups": n, "updated": timestamp}
"""

import json
import time
import urllib.request

import functions_framework
from google.cloud import firestore

import zhvi

_db = None
_cache: dict = {}          # zip3 -> (fetched_at, group); per-instance, data changes monthly
CACHE_SECONDS = 6 * 3600


def db():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


class FirestoreStore:
    def get_group(self, zip3):
        hit = _cache.get(zip3)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        snap = db().collection("zhvi_zip3").document(zip3).get()
        group = (snap.to_dict() or {}).get("z") if snap.exists else None
        if group is None:
            # Tell "ZIP prefix has no data" apart from "nothing loaded yet".
            loaded = db().collection("zhvi_meta").document("latest").get().exists
            group = {} if loaded else None
        if group is not None:
            _cache[zip3] = (time.time(), group)
        return group


@functions_framework.http
def home_value(request):
    if request.method != "GET":
        return ("Method not allowed", 405)
    body = zhvi.handle(
        request.args.get("zip", ""),
        request.args.get("price", ""),
        request.args.get("date", ""),
        store=FirestoreStore(),
    )
    return (json.dumps(body), 200, {"Content-Type": "application/json", "Cache-Control": "no-store"})


@functions_framework.http
def refresh_zhvi(request):
    started = time.time()
    req = urllib.request.Request(zhvi.ZHVI_URL, headers={"User-Agent": "home-value-trmnl/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        groups, latest = zhvi.parse_csv(zhvi.iter_lines(resp))

    batch, n = db().batch(), 0
    for zip3, members in groups.items():
        batch.set(db().collection("zhvi_zip3").document(zip3), {"z": members})
        n += 1
        if n % 20 == 0:           # keep each commit well under Firestore's 10 MiB request limit
            batch.commit()
            batch = db().batch()
    batch.commit()

    meta = {"month": latest, "zips": sum(len(m) for m in groups.values()), "groups": len(groups),
            "updated": firestore.SERVER_TIMESTAMP}
    db().collection("zhvi_meta").document("latest").set(meta)
    _cache.clear()
    summary = {"month": latest, "zips": meta["zips"], "groups": len(groups),
               "seconds": round(time.time() - started, 1)}
    print(json.dumps({"refresh_zhvi": summary}))
    return (json.dumps(summary), 200, {"Content-Type": "application/json"})
