"""Synthetic CEOS metadata, geometry and human-review boundary regressions.

These small fixtures are deliberately not satellite imagery or an InSAR
end-to-end validation. Bytes follow the JAXA tables independently of the
reader's installed schema so a P2/P3 offset mix-up is observable.
"""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import alos_coverage as coverage


SCIENCE = bool(os.environ.get("ALOSSTACK_TEST_ENV"))


class ReviewBoundary(unittest.TestCase):
    def inspection(self):
        return dict(revision="revision-a", left=dict(selected_ids=["a"]), right=dict(selected_ids=["b"]),
                    intersection=dict(status="overlap"))

    def payload(self, **changes):
        item = dict(revision="revision-a", left_ids=["a"], right_ids=["b"], selected_product_ids=["a", "b"],
                    outcome="confirmed", notes="人工核對了兩組涵蓋區域。", reviewer="研究者")
        item.update(changes)
        return item

    def test_confirmation_is_coverage_only_never_unlocks_science(self):
        out = coverage.validate_review(self.payload(processing_unlocked=True), self.inspection())
        self.assertEqual(out["outcome"], "confirmed")
        self.assertEqual(out["scope"], "human_metadata_coverage_review")
        self.assertEqual(out["scientific_validation"], "not_performed")
        self.assertIs(out["processing_unlocked"], False)

    def test_pending_requires_no_reviewer_but_still_binds_revision(self):
        out = coverage.validate_review(self.payload(outcome="pending", notes="", reviewer=""), self.inspection())
        self.assertEqual(out["outcome"], "pending")
        for fields in ({"revision": ""}, {"revision": "other"}, {"left_ids": ["c"]}, {"selected_product_ids": ["b"]}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                coverage.validate_review(self.payload(**fields), self.inspection())

    def test_unknown_or_no_overlap_cannot_be_confirmed(self):
        for state in ("unknown", "no_overlap"):
            inspection = self.inspection()
            inspection["intersection"]["status"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                coverage.validate_review(self.payload(), inspection)
            rejected = coverage.validate_review(self.payload(outcome="not_compatible"), inspection)
            self.assertEqual(rejected["outcome"], "not_compatible")
            self.assertFalse(rejected["processing_unlocked"])

    def test_changed_sources_or_selection_make_old_review_stale(self):
        review = coverage.validate_review(self.payload(), self.inspection())
        self.assertFalse(coverage.review_status(review, self.inspection())["stale"])
        for changed in ({**self.inspection(), "revision": "new-source"}, {**self.inspection(), "left": {"selected_ids": ["c"]}}):
            result = coverage.review_status(review, changed)
            self.assertTrue(result["stale"])
            self.assertEqual(result["outcome"], "pending")
            self.assertFalse(result["processing_unlocked"])
        self.assertEqual(coverage.review_status(None, self.inspection())["outcome"], "pending")

    def test_invalid_human_review_is_not_silently_accepted(self):
        for fields in ({"outcome": "approved"}, {"reviewer": ""}, {"notes": ""}, {"notes": "bad\x00text"},
                       {"notes": "x" * 8001}, {"left_ids": ["a", "a"]}, {"left_ids": "a"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                coverage.validate_review(self.payload(**fields), self.inspection())


@unittest.skipUnless(SCIENCE, "Requires selected science environment (Shapely/PyProj/ISCE2)")
class GeometryBoundary(unittest.TestCase):
    def product(self, identifier, x=121, y=24, frame="0480", sensor="ALOS-2"):
        info = coverage.outline_from_points([[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]])
        return dict(id=identifier, sensor=sensor, date="20200101", metadata=dict(frame=frame, orbit_direction="ascending", look_direction="right"),
                    footprint_status="metadata_outline", footprint=info["footprint"], errors=[], source_fingerprint="source-" + identifier)

    def test_same_frame_in_different_places_is_not_a_match(self):
        a, b = self.product("a"), self.product("b", x=130, sensor="ALOS-4")
        out = coverage.inspect_coverage([a, b], ["a"], ["b"])
        self.assertEqual(out["intersection"]["status"], "no_overlap")
        self.assertEqual(out["pairwise"][0]["area_km2"], 0)
        self.assertFalse(out["processing_unlocked"])

    def test_different_frame_ids_can_overlap_without_being_compatible(self):
        a, b = self.product("a"), self.product("b", x=121.5, frame="9999", sensor="ALOS-4")
        out = coverage.inspect_coverage([a, b], ["a"], ["b"])
        self.assertEqual(out["intersection"]["status"], "overlap")
        self.assertGreater(out["intersection"]["area_km2"], 100)
        self.assertAlmostEqual(out["intersection"]["fraction_left"], .5, places=3)
        self.assertEqual(out["intersection"]["bounds"], [121.5, 24, 122, 25])
        self.assertTrue(all(not c["compatibility_decision"] for c in out["checks"]))
        self.assertEqual(out["scientific_validation"], "not_performed")

    def test_zero_nan_range_invalid_and_bowtie_are_unknown_not_repaired(self):
        cases = [
            [[0, 24], [1, 24], [1, 25], [0, 25]],
            [[121, 0], [122, 0], [122, 1], [121, 1]],
            [[121, 24], [float("nan"), 24], [122, 25], [121, 25]],
            [[181, 24], [182, 24], [182, 25], [181, 25]],
            [[121, 94], [122, 94], [122, 95], [121, 95]],
            [[121, 24], [122, 25], [122, 24], [121, 25]],
            [[121, 24], [121, 24], [121, 24], [121, 24]],
        ]
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ValueError):
                coverage.outline_from_points(points)

    def test_dateline_is_split_and_overlap_not_global(self):
        a, b = self.product("a"), self.product("b")
        info = coverage.outline_from_points([[179, 10], [-179, 10], [-179, 11], [179, 11]])
        a["footprint"] = info["footprint"]
        b["footprint"] = coverage.outline_from_points([[179.5, 10.2], [-179.5, 10.2], [-179.5, 10.8], [179.5, 10.8]])["footprint"]
        self.assertEqual(info["footprint"]["type"], "MultiPolygon")
        self.assertTrue(info["crosses_dateline"])
        self.assertEqual(len(info["bounds_parts"]), 2)
        self.assertLess(info["area_km2"], 30000)
        result = coverage.inspect_coverage([a, b], ["a"], ["b"])
        self.assertEqual(result["intersection"]["status"], "overlap")
        self.assertTrue(result["intersection"]["crosses_dateline"])
        self.assertAlmostEqual(result["intersection"]["fraction_right"], 1, places=4)

    def test_unknown_selected_frame_keeps_whole_group_intersection_unknown(self):
        a, b, unknown = self.product("a"), self.product("b", x=121.3), self.product("u")
        unknown.update(footprint=None, footprint_status="unknown")
        result = coverage.inspect_coverage([a, b, unknown], ["a", "u"], ["b"])
        self.assertEqual(result["left"]["coverage_scope"], "partial_preview")
        self.assertEqual(result["left"]["unknown_ids"], ["u"])
        self.assertIsNotNone(result["left"]["coverage"])
        self.assertEqual(result["intersection"]["status"], "unknown")
        self.assertIsNone(result["intersection"]["area_km2"])
        self.assertEqual([p["status"] for p in result["pairwise"]], ["overlap", "unknown"])

    def test_boundary_touch_is_not_positive_area_overlap(self):
        a, b = self.product("a"), self.product("b", x=122)
        intersection = coverage.inspect_coverage([a, b], ["a"], ["b"])["intersection"]
        self.assertEqual(intersection["status"], "no_overlap")
        self.assertTrue(intersection["touches"])

    def test_revision_changes_for_sources_geometry_selection_or_group_swap(self):
        a, b, c = self.product("a"), self.product("b", x=121.2), self.product("c", x=121.4)
        old = coverage.inspect_coverage([a, b, c], ["a"], ["b"])
        same = coverage.inspect_coverage([c, b, a], ["a"], ["b"])
        self.assertEqual(old["revision"], same["revision"])
        b["source_fingerprint"] = "changed"
        new = coverage.inspect_coverage([a, b, c], ["a"], ["b"])
        self.assertNotEqual(old["revision"], new["revision"])
        swapped = coverage.inspect_coverage([a, b, c], ["b"], ["a"])
        self.assertNotEqual(new["revision"], swapped["revision"])
        selected = coverage.inspect_coverage([a, b, c], ["a", "c"], ["b"])
        self.assertNotEqual(new["revision"], selected["revision"])

    def test_invalid_selection_and_invalid_geojson_never_claim_overlap(self):
        a, b = self.product("a"), self.product("b")
        for left, right in (([], ["b"]), (["a"], ["a"]), (["a", "a"], ["b"]), (["missing"], ["b"])):
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                coverage.inspect_coverage([a, b], left, right)
        b["footprint"]["coordinates"][0][0][0] = float("inf")
        # Invalid geometry must not be used, even if upstream labels it known.
        self.assertIsNone(coverage._known_geometry(b))
        b["footprint"]["coordinates"][0][0][0] = 500
        result = coverage.inspect_coverage([a, b], ["a"], ["b"])
        self.assertEqual(result["intersection"]["status"], "unknown")


@unittest.skipUnless(SCIENCE, "Requires installed ISCE2 scalar CEOS decoder")
class CEOSCoverage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def record(size):
        raw = bytearray(b" " * size)
        raw[:4] = struct.pack(">I", 1)
        raw[8:12] = struct.pack(">I", size)
        return raw

    @staticmethod
    def ascii(raw, offset, length, value):
        raw[offset:offset + length] = str(value).encode("ascii").ljust(length)[:length]

    def product(self, name="one", sensor=2, x=121, y=24, level="1.1", direction="ASCEND", map_corners=False, images=True,
                center=True, pol="HH", zero_corner=False):
        directory = self.root / name
        directory.mkdir()
        if sensor == 2:
            scene_id = "ALOS2000010480-200101"
            suffix = scene_id + "-FBDR1.1__A"
        else:
            scene_id = "ALOS41370480260327FWDPRA0107"
            suffix = scene_id + "-1.1__-"
        scene = self.record(4096 if sensor == 2 else 4680)
        for start, length, value in ((20, 32, scene_id), (68, 32, "20200101120000000"), (396, 16, "ALOS" + str(sensor)),
                                     (412, 32, "ALOS" + str(sensor) + " -L -0315-"), (444, 8, "00001"), (476, 8, "90.000"),
                                     (710, 16, "52.3957978"), (1094, 16, level), (164, 16, "GRS80")):
            self.ascii(scene, start, length, value)
        self.ascii(scene, 1534 if sensor == 2 else 1822, 8, direction)
        # A misleading reserved field must never be used as an orbital flag.
        self.ascii(scene, 100, 16, "DESCEND")
        if sensor == 4:
            self.ascii(scene, 1534, 8, "DESCEND")
        if center:
            self.ascii(scene, 116, 16, y + .5)
            self.ascii(scene, 132, 16, x + .5)
        leader = self.record(720)
        for start, value in ((180, 1), (186, len(scene)), (192, int(map_corners)), (198, 1620 if map_corners else 0)):
            self.ascii(leader, start, 6, value)
        raw = leader + scene
        if map_corners:
            projection = self.record(1620)
            corners = [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]]
            for i, (lon, lat) in enumerate(corners):
                self.ascii(projection, 1072 + i * 32, 16, lat)
                self.ascii(projection, 1088 + i * 32, 16, lon)
            raw += projection
        led = directory / ("LED-" + suffix)
        led.write_bytes(raw)
        if images:
            fdr = self.record(720)
            count, stride = 9, 800 if sensor == 4 else 560
            for start, length, value in ((180, 6, count), (186, 6, stride), (248, 8, 2)):
                self.ascii(fdr, start, length, value)
            records = []
            for index in range(count):
                row = self.record(stride)
                row[36:44] = struct.pack(">II", 2020, 1)
                row[52:56] = struct.pack(">HH", "HV".index(pol[0]), "HV".index(pol[1]))
                coordinates = [y + index / 8, y + index / 8, y + index / 8, x, x + .5, x + 1]
                if zero_corner and index == 0:
                    coordinates[0] = 0
                for i, value in enumerate(coordinates):
                    row[192 + i * 4:196 + i * 4] = struct.pack(">i", round(value * 1e6))
                records.append(row)
            (directory / ("IMG-" + pol + "-" + suffix)).write_bytes(fdr + b"".join(records))
        return led

    def test_native_layouts_read_metadata_and_sampled_outline(self):
        for sensor in (2, 4):
            with self.subTest(sensor=sensor):
                led = self.product(name=str(sensor), sensor=sensor)
                out = coverage.read_product(led)
                self.assertEqual(out["errors"], [])
                self.assertEqual(out["sensor"], "ALOS-" + str(sensor))
                self.assertEqual(out["date"], "20200101")
                self.assertEqual(out["polarizations"], ["HH"])
                self.assertEqual(out["bounds"], [121, 24, 122, 25])
                self.assertEqual(out["footprint_status"], "metadata_outline")
                self.assertEqual(out["metadata"]["orbit_direction"], "ascending")
                self.assertEqual(out["metadata"]["look_direction"], "right")
                self.assertAlmostEqual(out["metadata"]["range_sampling_mhz"], 52.3957978)
                self.assertLess(sum(s["bytes_read"] for s in out["source_files"]), 9000)
                self.assertFalse(out["processing_unlocked"])

    def test_non_orbital_indicator_and_reserved_field_do_not_guess_direction(self):
        out = coverage.read_product(self.product(direction="INCREASE"))
        self.assertIsNone(out["metadata"]["orbit_direction"])
        self.assertEqual(out["metadata"]["along_line_indicator"], "INCREASE")
        self.assertTrue(any("ASCEND" in w for w in out["warnings"]))

    def test_center_only_and_zero_corners_remain_unknown(self):
        center = coverage.read_product(self.product(name="center", images=False))
        self.assertEqual(center["metadata"]["center"], [121.5, 24.5])
        self.assertIsNone(center["footprint"])
        zero = coverage.read_product(self.product(name="zero", zero_corner=True))
        self.assertEqual(zero["footprint_status"], "unknown")
        self.assertIsNone(zero["bounds"])
        self.assertTrue(any("零" in w for w in zero["warnings"]))

    def test_map_corners_for_processed_level_use_geodetic_not_projected_fields(self):
        led = self.product(level="1.5", images=False, map_corners=True)
        raw = bytearray(led.read_bytes())
        # Projected northing/easting fields immediately before geodetic fields.
        raw[720 + 4096 + 944:720 + 4096 + 1072] = b"9" * 128
        led.write_bytes(raw)
        out = coverage.read_product(led)
        self.assertEqual(out["bounds"], [121, 24, 122, 25])
        self.assertEqual(out["footprint_source"]["record"], "map_projection")

    def test_bad_ascii_numeric_is_not_silently_converted_to_zero(self):
        led = self.product(map_corners=True, images=False)
        raw = bytearray(led.read_bytes())
        self.ascii(raw, 720 + 710, 16, "not-a-number")
        self.ascii(raw, 720 + 4096 + 1072, 16, "NaN")
        led.write_bytes(raw)
        out = coverage.read_product(led)
        self.assertIsNone(out["metadata"]["range_sampling_mhz"])
        self.assertIsNone(out["footprint"])
        self.assertTrue(any("數值欄位不合法" in w for w in out["warnings"]))

    def test_invalid_latitude_truncated_image_and_polarization_are_visible(self):
        bad = coverage.read_product(self.product(name="lat", y=91))
        self.assertIsNone(bad["footprint"])
        led = self.product(name="trunc")
        image = next(led.parent.glob("IMG-*"))
        image.write_bytes(image.read_bytes()[:-1])
        out = coverage.read_product(led)
        self.assertIsNone(out["footprint"])
        self.assertTrue(any("大小" in e for e in out["errors"]))
        led = self.product(name="pol")
        image = next(led.parent.glob("IMG-*"))
        image.rename(image.with_name(image.name.replace("IMG-HH", "IMG-HV")))
        self.assertTrue(any("極化" in e for e in coverage.read_product(led)["errors"]))

    def test_converted_p3_retains_source_sensor_and_p2_layout_with_synthetic_frame(self):
        led = self.product()
        (led.parent / "conversion.json").write_text(json.dumps(dict(source_sensor="ALOS-4", output_format="PALSAR-2 CEOS compatibility")))
        out = coverage.read_product(led)
        self.assertEqual(out["sensor"], "ALOS-4")
        self.assertEqual(out["metadata"]["layout"], "PALSAR-2")
        self.assertEqual(out["metadata"]["source_sensor"], "ALOS-4")
        self.assertEqual(out["metadata"]["frame_provenance"], "synthetic_compatibility_id")
        self.assertEqual(out["bounds"], [121, 24, 122, 25])
        self.assertFalse(out["processing_unlocked"])

    def test_metadata_change_changes_fingerprint_but_id_remains_stable(self):
        led = self.product()
        before = coverage.read_product(led)
        raw = bytearray(led.read_bytes())
        self.ascii(raw, 720 + 710, 16, "104.7915957")
        led.write_bytes(raw)
        after = coverage.read_product(led)
        self.assertEqual(before["id"], after["id"])
        self.assertNotEqual(before["source_fingerprint"], after["source_fingerprint"])
        self.assertNotEqual(before["metadata"]["range_sampling_mhz"], after["metadata"]["range_sampling_mhz"])

    def test_source_change_during_read_is_rejected(self):
        led = self.product()
        original = coverage._Source.read
        changed = False
        def changing(source, offset, length):
            nonlocal changed
            raw = original(source, offset, length)
            if source.path == led.resolve() and not changed:
                changed = True
                with led.open("ab") as stream:
                    stream.write(b"changed")
            return raw
        with patch.object(coverage._Source, "read", changing), self.assertRaisesRegex(ValueError, "來源"):
            coverage.read_product(led)

    def test_symlink_and_invalid_record_lengths_are_rejected(self):
        led = self.product()
        link = self.root / led.name
        link.symlink_to(led)
        with self.assertRaises(ValueError):
            coverage.read_product(link)
        raw = bytearray(led.read_bytes())
        raw[8:12] = struct.pack(">I", 99999999)
        led.write_bytes(raw)
        out = coverage.read_product(led)
        self.assertIsNone(out["footprint"])
        self.assertTrue(out["errors"])

    def test_leader_scene_identity_mismatch_is_unknown(self):
        led = self.product()
        renamed = led.with_name(led.name.replace("0480", "0490"))
        led.rename(renamed)
        out = coverage.read_product(renamed)
        self.assertIsNone(out["footprint"])
        self.assertTrue(any("場景編號" in e for e in out["errors"]))


if __name__ == "__main__":
    unittest.main()
