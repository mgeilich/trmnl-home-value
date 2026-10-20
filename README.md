# Home Value Estimate — TRMNL recipe

Shows what a home is worth on a [TRMNL](https://trmnl.com) e-paper display. The user enters a ZIP code and, optionally, what they paid and when. The plugin grows the purchase price by how much typical home values in that ZIP have changed since, using the **Zillow Home Value Index (ZHVI)**, which Zillow Research publishes monthly for ~26,000 ZIP codes. Without a purchase price it shows the ZIP's typical home value.

No sign-up and no API key for anyone. Data: Zillow Home Value Index, Zillow Research ([free for public use with attribution](https://www.zillowgroup.com/developers/api/public-data/real-estate-metrics)).

> The estimate follows typical homes in the ZIP, not the specific house, so it won't reflect renovations or what makes a house different. It is not a Zestimate or an appraisal.

## How it works

```
TRMNL ──GET /home_value?zip=01983&price=725000&date=2019-06──▶ home_value (public Cloud Run function)
                                                                   │ reads 1 document
                                                                   ▼
Cloud Scheduler (20th of each month) ──▶ refresh_zhvi (private) ──▶ Firestore
                                          downloads Zillow's ZIP ZHVI CSV (~124 MB)
```

- `estimate = purchase price × ZHVI(latest month) ÷ ZHVI(purchase month)`, rounded to $1,000.
- Purchase months before the ZIP's data starts (Jan 2000 at the earliest) use the first available month.
- Response (always HTTP 200): `mode` (`estimate` or `zip`), `price`, `purchase_price`, `purchase_month`, `gain`, `gain_pct`, `zip`, `city`, `state`, `zip_typical_value`, `zip_change_1y_pct`, `as_of`, or `message` when something needs fixing.
- Nothing about users is stored: requests carry the ZIP and purchase details, and Firestore holds only Zillow's public ZIP data.

Firestore layout: `zhvi_zip3/{first 3 digits of ZIP}` → `{"z": {zip: {"s": "YYYY-MM", "v": "v1,v2,…", "city", "state"}}}` (883 documents, largest ~210 KB) and `zhvi_meta/latest`.

## Deployed setup

| Piece | Where |
| --- | --- |
| Google Cloud / Firebase project | `zestimate-trmnl-2026`, Blaze plan |
| Public function | `home_value` — https://us-central1-zestimate-trmnl-2026.cloudfunctions.net/home_value |
| Private refresh function | `refresh_zhvi` (us-central1, 1 GiB, 9 min timeout, not publicly callable) |
| Schedule | Cloud Scheduler job `zhvi-monthly-refresh`, 06:00 New York time on the 20th of each month |
| Database | Firestore (native), `nam5` |

Expected cost: within Google Cloud's free allowances (a few thousand function calls and reads a day, one ~1-minute refresh a month).

### Redeploying (Cloud Shell or any machine with `gcloud`)

```sh
cd functions
python3 -m unittest test_zhvi          # no network needed

gcloud config set project zestimate-trmnl-2026
gcloud functions deploy home_value --gen2 --runtime=python312 --region=us-central1 \
  --source=. --entry-point=home_value --trigger-http --allow-unauthenticated \
  --memory=256Mi --timeout=30s --max-instances=3
gcloud functions deploy refresh_zhvi --gen2 --runtime=python312 --region=us-central1 \
  --source=. --entry-point=refresh_zhvi --trigger-http --no-allow-unauthenticated \
  --memory=1Gi --timeout=540s --max-instances=1
```

Run a refresh by hand: `gcloud scheduler jobs run zhvi-monthly-refresh --location=us-central1`.

Test: `curl "https://us-central1-zestimate-trmnl-2026.cloudfunctions.net/home_value?zip=01983&price=725000&date=2019-06"`

## The recipe

```
src/settings.yml          plugin settings, polling URL, form fields
src/shared.liquid         turns the response into display strings (shared by all views)
src/full.liquid           full screen
src/half_horizontal.liquid
src/half_vertical.liquid
src/quadrant.liquid
.trmnlp.yml               local dev config for trmnlp (not uploaded)
```

Form fields: ZIP Code (required), Purchase Price, Purchase Month (e.g. `2019-06`), Display Name (all optional).

| Layout | Shows |
| --- | --- |
| Full | Place, estimate, "Bought for … in …", gain $, gain %, typical value in ZIP, ZIP 12-month change |
| Half horizontal | Place, estimate, purchase line, gain $ and % |
| Half vertical | Town, estimate, purchase month, four facts in a 2×2 grid |
| Quadrant | Town, estimate, gain since purchase |

All four adapt to TRMNL OG (800×480, 1-bit) and TRMNL X (landscape and portrait). Every view credits "Zillow Home Value Index" / "Zillow ZHVI" in its title bar, as Zillow's terms require.

### Developing and publishing

The recipe follows the layout of [trmnlp](https://github.com/usetrmnl/trmnlp), TRMNL's local dev tool (Docker, or `gem install trmnl_preview` on Ruby 4.0+): `trmnlp serve`, `trmnlp lint`, `trmnlp login && trmnlp push`.

The `author_bio` field links to this repository, which gives users the contact method TRMNL requires; issues can be filed here.

## Next: real Zestimates

Zillow offers a Zestimates API to approved partners ([Zillow Group developers](https://www.zillowgroup.com/developers/api/zestimate/zestimates-api/)). If access is granted, `home_value` can return the house's own Zestimate when an address is given, falling back to the ZIP index otherwise; the recipe's layouts already handle a single `price`.

## Files

```
functions/zhvi.py         parsing + estimate logic (no Google Cloud imports; unit-tested)
functions/main.py         home_value and refresh_zhvi HTTP functions, Firestore access
functions/test_zhvi.py    unit tests
functions/requirements.txt
```
