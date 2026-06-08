import csv
import tempfile
import unittest
from pathlib import Path

import pipeline_hotels as p


class PipelineTests(unittest.TestCase):
    def test_resolve_by_propid_and_name_dedup(self):
        hotels = p.OrderedDict(
            {
                "1": {"propid": "1", "name": "Alpha", "rows": []},
                "2": {"propid": "2", "name": "Beta", "rows": []},
            }
        )
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            targets = p.resolve_targets(
                hotels=hotels,
                output_dir=outdir,
                propids=["1"],
                hotel_names=["Alpha"],
                next_hotels=None,
                force=False,
            )
        self.assertEqual(targets, ["1"])

    def test_next_hotels_skips_existing(self):
        hotels = p.OrderedDict(
            {
                "1": {"propid": "1", "name": "Alpha", "rows": []},
                "2": {"propid": "2", "name": "Beta", "rows": []},
                "3": {"propid": "3", "name": "Gamma", "rows": []},
            }
        )
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            p.output_path_for_hotel(outdir, "1", "Alpha").write_text("done")
            targets = p.resolve_targets(
                hotels=hotels,
                output_dir=outdir,
                propids=[],
                hotel_names=[],
                next_hotels=2,
                force=False,
            )
        self.assertEqual(targets, ["2", "3"])

    def test_process_hotel_writes_all_fields(self):
        hotel = {
            "propid": "99",
            "name": "Test Hotel",
            "rows": [
                {"Listing_MappedID": "99", "Listing_Name": "Test Hotel", "Asset_Link": "http://img/1"}
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "99_test-hotel.csv"
            p.process_hotel(
                hotel=hotel,
                output_path=out,
                fieldnames=["Listing_MappedID", "Listing_Name", "Asset_Link"],
                force=False,
                ai_func=lambda _: {
                    "AI_Caption_Basic": "A room",
                    "AI_Description_Basic": "A room with bed",
                    "AI_Caption_Experience": "Rest in comfort",
                    "AI_Description_Experience": "Elegant room with soft light.",
                    "AI_Image_Tag": "tag-citta",
                    "AI_Alt_Text": "Hotel room with a bed and lamp",
                },
            )
            with out.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                rows = list(reader)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["AI_Image_Tag"], "tag-citta")


if __name__ == "__main__":
    unittest.main()
