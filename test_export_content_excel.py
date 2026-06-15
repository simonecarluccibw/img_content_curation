import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import export_content_excel as exporter


class ExportContentExcelTests(unittest.TestCase):
    def test_build_export_row_maps_pipeline_fields(self):
        row = {
            "Listing_ICEID": "ICE-123",
            "Listing_MappedID": "77519",
            "Listing_Brand": "Best Western",
            "Listing_Name": "Example Hotel",
            "Asset_MediaType": "Image",
            "Asset_Index": "4",
            "Asset_PublicID": "public-asset",
            "Amenity_Category": "Pool",
            "Amenity_CustomTags": "overview-pool, amenity-pool",
            "Caption": "A quiet swim",
            "Description": "A bright pool framed by loungers.",
            "Alt_Text": "Outdoor hotel pool with loungers",
            "Asset_Link": "https://example.test/image.jpg",
        }

        self.assertEqual(exporter.build_export_row(row), {
            "IceID": "ICE-123",
            "MappedID": "77519",
            "Hotel": "Best Western Example Hotel",
            "AssetType": "PH",
            "Index": "4",
            "PublicID": "public-asset",
            "Category": "Pool",
            "Custom Tags": "overview-pool, amenity-pool",
            "Caption": "A quiet swim",
            "Description": "A bright pool framed by loungers.",
            "Alt Text": "Outdoor hotel pool with loungers",
            "URL": "https://example.test/image.jpg",
        })

    def test_build_export_row_uses_only_requested_source_columns(self):
        row = {
            "Listing_Name": "Example Hotel",
            "Asset_MediaType": "Image",
            "Asset_FileID": "asset-file-id",
            "Amenity_CustomTag1": "overview-spa",
            "Caption_Experience": "Wrong caption",
            "Description_Experience": "Wrong description",
        }

        export_row = exporter.build_export_row(row)

        self.assertEqual(export_row["Hotel"], "Example Hotel")
        self.assertEqual(export_row["AssetType"], "PH")
        self.assertEqual(export_row["PublicID"], "")
        self.assertEqual(export_row["Custom Tags"], "")
        self.assertEqual(export_row["Caption"], "")
        self.assertEqual(export_row["Description"], "")

    def test_write_excel_creates_xlsx_with_requested_columns(self):
        rows = [{
            "IceID": "ICE-123",
            "MappedID": "77519",
            "Hotel": "Example Hotel",
            "AssetType": "Image",
            "Index": "1",
            "PublicID": "public-asset",
            "Category": "Spa",
            "Custom Tags": "overview-spa",
            "Caption": "Rest well",
            "Description": "Calm spa seating.",
            "Alt Text": "Spa seating area",
            "URL": "https://example.test/image.jpg",
        }]

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "content_export.xlsx"
            exporter.write_excel(path, rows)

            self.assertTrue(path.exists())
            with zipfile.ZipFile(path) as archive:
                sheet = archive.read("xl/worksheets/sheet1.xml")

        root = ElementTree.fromstring(sheet)
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        text_values = [node.text for node in root.findall(".//x:t", ns)]

        self.assertEqual(text_values[: len(exporter.EXPORT_COLUMNS)], exporter.EXPORT_COLUMNS)
        self.assertIn("Example Hotel", text_values)
        self.assertIn("https://example.test/image.jpg", text_values)


if __name__ == "__main__":
    unittest.main()
