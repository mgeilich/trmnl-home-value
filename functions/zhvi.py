"""Home value estimates from the Zillow Home Value Index (ZHVI), by ZIP code.

Pure logic, no Google Cloud imports, so it can be unit-tested. main.py wires it to
Firestore and HTTP.

Data: Zillow Research publishes the ZHVI for every ZIP it covers as one monthly CSV
(free for public use with attribution to Zillow):
  https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv
Columns: RegionID, SizeRank, RegionName (the ZIP), RegionType, StateName, State, City,
Metro, CountyName, then one column per month ("2000-01-31", ...).

Storage: one Firestore document per 3-digit ZIP prefix, so a request reads one small
document. Each ZIP's series is stored as a compact comma-separated string of whole
dollars (empty where Zillow has no value) together with the month it starts at.

Estimate: purchase price x (ZHVI now / ZHVI in the purchase month). This tracks typical
homes in the ZIP, not the specific house.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Iterable, Iterator, Optional, Protocol

ZHVI_URL = ("https://files.zillowstatic.com/research/public_csvs/zhvi/"
            "Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv")
FIRST_MONTH_COL = 9

MSG_NO_ZIP = "Add your 5-digit ZIP code in the plugin settings."
MSG_BAD_ZIP = "That ZIP code doesn't look right. Use the 5-digit ZIP, e.g. 01983."
MSG_ZIP_NOT_COVERED = ("Zillow doesn't publish a home value index for ZIP {zip}. "
                       "Try a nearby ZIP code.")
MSG_NO_DATA = "Home value data is still loading. It will appear at the next refresh."
MSG_BAD_DATE = "Purchase date should look like 2019-06 (year and month)."


# ---------------------------------------------------------------- parsing (refresh)

def parse_csv(lines: Iterable[str]) -> "tuple[dict, str]":
    """Parse the ZHVI ZIP CSV. Returns ({zip3: {zip5: entry}}, latest_month 'YYYY-MM')."""
    reader = csv.reader(lines)
    header = next(reader)
    months = [h[:7] for h in header[FIRST_MONTH_COL:]]
    groups: dict = {}
    for row in reader:
        if len(row) < FIRST_MONTH_COL + 1:
            continue
        z = row[2].strip().zfill(5)
        if not re.fullmatch(r"\d{5}", z):
            continue
        raw = row[FIRST_MONTH_COL:]
        vals = [str(round(float(x))) if x.strip() else "" for x in raw]
        # Trim leading/trailing empty months; record where the series starts.
        start = next((i for i, v in enumerate(vals) if v), None)
        if start is None:
            continue
        end = max(i for i, v in enumerate(vals) if v)
        groups.setdefault(z[:3], {})[z] = {
            "s": months[start],
            "v": ",".join(vals[start:end + 1]),
            "city": row[6].strip(),
            "state": row[5].strip(),
        }
    return groups, months[-1]


def iter_lines(stream: io.BufferedIOBase) -> Iterator[str]:
    """Decode a byte stream (e.g. an HTTP response) into text lines without loading it all."""
    return io.TextIOWrapper(stream, encoding="utf-8", newline="")


# ---------------------------------------------------------------- estimate (request)

def _month_index(start: str, month: str) -> int:
    sy, sm = int(start[:4]), int(start[5:7])
    y, m = int(month[:4]), int(month[5:7])
    return (y - sy) * 12 + (m - sm)


def _add_months(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _series(entry: dict) -> "list[Optional[int]]":
    return [int(x) if x else None for x in entry["v"].split(",")]


def _value_at(entry: dict, month: str) -> "tuple[Optional[int], str]":
    """ZHVI for `month`, clamped to the series; falls back to the nearest earlier value."""
    series = _series(entry)
    i = _month_index(entry["s"], month)
    i = max(0, min(i, len(series) - 1))
    for j in range(i, -1, -1):
        if series[j] is not None:
            return series[j], _add_months(entry["s"], j)
    for j in range(i + 1, len(series)):
        if series[j] is not None:
            return series[j], _add_months(entry["s"], j)
    return None, month


def _latest(entry: dict) -> "tuple[Optional[int], str]":
    return _value_at(entry, _add_months(entry["s"], len(_series(entry)) - 1))


def parse_month(text: str) -> Optional[str]:
    """Accept 2019-06, 2019-06-14, 06/2019, 6/14/2019. Return 'YYYY-MM' or None."""
    t = (text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})/(?:\d{1,2}/)?(\d{4})", t)
        if not m:
            return None
        y, mo = int(m.group(2)), int(m.group(1))
    if not (1 <= mo <= 12 and 1900 <= y <= 2100):
        return None
    return f"{y:04d}-{mo:02d}"


def parse_price(text: str) -> Optional[int]:
    t = re.sub(r"[,$\s]", "", text or "")
    if not t:
        return None
    try:
        v = float(t[:-1]) * 1000 if t[-1:].lower() == "k" else float(t)
    except ValueError:
        return None
    return int(round(v)) if v > 0 else None


def _round_to(v: float, step: int = 1000) -> int:
    return int(round(v / step) * step)


class Store(Protocol):
    def get_group(self, zip3: str) -> Optional[dict]: ...


def handle(zip_code: str, purchase_price: str = "", purchase_date: str = "", *, store: Store) -> dict:
    """Build the JSON the TRMNL recipe renders. Always returns a dict (HTTP 200)."""
    z = re.sub(r"\D", "", zip_code or "")[:5]
    if not zip_code or not zip_code.strip():
        return {"message": MSG_NO_ZIP}
    if len(z) != 5:
        return {"message": MSG_BAD_ZIP}

    group = store.get_group(z[:3])
    if group is None:
        return {"message": MSG_NO_DATA}
    entry = group.get(z)
    if not entry:
        return {"message": MSG_ZIP_NOT_COVERED.format(zip=z)}

    now_val, now_month = _latest(entry)
    if now_val is None:
        return {"message": MSG_ZIP_NOT_COVERED.format(zip=z)}
    year_ago_val, _ = _value_at(entry, _add_months(now_month, -12))

    out = {
        "zip": z,
        "city": entry.get("city", ""),
        "state": entry.get("state", ""),
        "zip_typical_value": now_val,
        "zip_change_1y_pct": round((now_val / year_ago_val - 1) * 100, 1) if year_ago_val else None,
        "as_of": f"{now_month}-01",
        "source": "zillow_zhvi",
    }

    price = parse_price(purchase_price)
    month = parse_month(purchase_date) if purchase_date and purchase_date.strip() else None
    if purchase_date and purchase_date.strip() and month is None:
        out["message"] = MSG_BAD_DATE

    if price and month:
        then_val, then_month = _value_at(entry, month)
        estimate = _round_to(price * now_val / then_val)
        out.update(
            mode="estimate",
            price=estimate,
            purchase_price=price,
            purchase_month=f"{month}-01",
            index_month_used=f"{then_month}-01",
            gain=estimate - price,
            gain_pct=round((estimate / price - 1) * 100, 1),
        )
    else:
        out.update(mode="zip", price=_round_to(now_val))
    return out
