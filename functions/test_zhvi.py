"""Unit tests for zhvi.py. Run from this folder: python3 -m unittest test_zhvi"""

import unittest

import zhvi

HEADER = "RegionID,SizeRank,RegionName,RegionType,StateName,State,City,Metro,CountyName,2000-01-31,2000-02-29,2000-03-31,2000-04-30,2001-01-31"
# Note: the real file has every month; this fixture's last column is a jump, which is fine
# for parsing, but estimate tests below build entries directly with contiguous months.
CSV = "\n".join([
    HEADER,
    '1,1,90210,zip,CA,CA,Beverly Hills,"Los Angeles, CA",Los Angeles County,100000.4,,110000,120000,130000',
    "2,2,2134,zip,MA,MA,Allston,Boston,Suffolk County,,200000,210000,,",
    "3,3,99999,zip,AK,AK,Nowhere,,,,,,,",
])


class FakeStore:
    def __init__(self, groups):
        self.groups = groups

    def get_group(self, zip3):
        return self.groups.get(zip3)


def entry(start, values, city="Beverly Hills", state="CA"):
    return {"s": start, "v": ",".join("" if v is None else str(v) for v in values), "city": city, "state": state}


# 25 months: 2024-01 .. 2026-01, value rising 1,000 a month from 500,000.
SERIES = entry("2024-01", [500000 + 1000 * i for i in range(25)])
STORE = FakeStore({"902": {"90210": SERIES}})


class ParseTests(unittest.TestCase):
    def test_parse_csv(self):
        groups, latest = zhvi.parse_csv(CSV.splitlines())
        self.assertEqual(latest, "2001-01")
        top = groups["902"]["90210"]
        self.assertEqual(top["s"], "2000-01")
        self.assertEqual(top["v"], "100000,,110000,120000,130000")
        self.assertEqual(top["city"], "Beverly Hills")
        allston = groups["021"]["02134"]               # zero-padded, leading/trailing blanks trimmed
        self.assertEqual(allston["s"], "2000-02")
        self.assertEqual(allston["v"], "200000,210000")
        self.assertNotIn("999", groups)            # ZIP with no values is skipped

    def test_parse_month(self):
        for text, want in [("2018-05", "2018-05"), ("2018-5-14", "2018-05"), ("05/2018", "2018-05"),
                           ("5/14/2018", "2018-05"), ("2019-13", None), ("junk", None), ("", None)]:
            self.assertEqual(zhvi.parse_month(text), want, text)

    def test_parse_price(self):
        for text, want in [("$412,000", 412000), ("412000", 412000), ("412k", 412000), ("", None),
                           ("abc", None), ("-5", None)]:
            self.assertEqual(zhvi.parse_price(text), want, text)


class HandleTests(unittest.TestCase):
    def test_zip_only(self):
        out = zhvi.handle("90210", store=STORE)
        self.assertEqual(out["mode"], "zip")
        self.assertEqual(out["price"], 524000)
        self.assertEqual(out["zip_typical_value"], 524000)
        self.assertEqual(out["as_of"], "2026-01-01")
        self.assertEqual(out["zip_change_1y_pct"], round((524000 / 512000 - 1) * 100, 1))

    def test_estimate_from_purchase(self):
        out = zhvi.handle("90210", "$400,000", "2024-07", store=STORE)  # index 506,000 then, 524,000 now
        self.assertEqual(out["mode"], "estimate")
        self.assertEqual(out["price"], round(400000 * 524000 / 506000 / 1000) * 1000)
        self.assertEqual(out["purchase_price"], 400000)
        self.assertEqual(out["gain"], out["price"] - 400000)
        self.assertEqual(out["purchase_month"], "2024-07-01")

    def test_purchase_before_series_uses_first_value(self):
        out = zhvi.handle("90210", "400000", "1998-03", store=STORE)
        self.assertEqual(out["index_month_used"], "2024-01-01")

    def test_gap_uses_previous_month(self):
        store = FakeStore({"902": {"90210": entry("2024-01", [100, None, 120, 130])}})
        out = zhvi.handle("90210", "1000", "2024-02", store=store)
        self.assertEqual(out["index_month_used"], "2024-01-01")
        self.assertEqual(out["price"], 1000)   # 1000 * 130/100 = 1300, rounded to nearest 1,000

    def test_bad_inputs(self):
        self.assertEqual(zhvi.handle("", store=STORE)["message"], zhvi.MSG_NO_ZIP)
        self.assertEqual(zhvi.handle("123", store=STORE)["message"], zhvi.MSG_BAD_ZIP)
        self.assertIn("90299", zhvi.handle("90299", store=STORE)["message"])
        self.assertEqual(zhvi.handle("55555", store=STORE)["message"], zhvi.MSG_NO_DATA)
        out = zhvi.handle("90210", "400000", "someday", store=STORE)
        self.assertEqual(out["message"], zhvi.MSG_BAD_DATE)
        self.assertEqual(out["mode"], "zip")   # still shows the ZIP value

    def test_zip_plus_four(self):
        self.assertEqual(zhvi.handle("90210-1234", store=STORE)["zip"], "90210")


if __name__ == "__main__":
    unittest.main()
