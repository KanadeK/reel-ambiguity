import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reel_ambiguity.cli import main
from reel_ambiguity.core import Clock, InputError, audit, parse_edl, parse_inventory, read_text

EDL = """TITLE: TEST
FCM: NON-DROP FRAME
001 REEL0001 V C 01:00:02:00 01:00:04:00 10:00:00:00 10:00:02:00
"""
HEADER = "clip_id,reel,start,end\n"
ONE = HEADER + "clip-a,REEL0001,01:00:00:00,01:00:10:00\n"


class ClockTests(unittest.TestCase):
    def test_integer_and_ntsc_non_drop_frame_counts(self):
        for rate, nominal in (
            ("24", 24),
            ("25", 25),
            ("30", 30),
            ("50", 50),
            ("60", 60),
            ("24000/1001", 24),
            ("30000/1001", 30),
        ):
            self.assertEqual(Clock(rate, False).frames("01:00:00:00"), nominal * 3600)

    def test_drop_frame_landmarks(self):
        clock = Clock("30000/1001", True)
        self.assertEqual(clock.frames("00:01:00;02"), 1800)
        self.assertEqual(clock.frames("00:10:00;00"), 17982)
        self.assertEqual(clock.frames("01:00:00;00"), 107892)
        self.assertEqual(clock.frames("00:01:00:02"), 1800)
        clock60 = Clock("60000/1001", True)
        self.assertEqual(clock60.frames("00:01:00;04"), 3600)
        self.assertEqual(clock60.frames("00:10:00;00"), 35964)

    def test_drop_labels_skip_without_a_time_gap(self):
        clock = Clock("30000/1001", True)
        self.assertEqual(clock.frames("00:01:00;02") - clock.frames("00:00:59;29"), 1)
        self.assertEqual(clock.frames("00:10:00;00") - clock.frames("00:09:59;29"), 1)
        for label in ("00:01:00;00", "00:01:00;01", "01:59:00;01"):
            with self.subTest(label=label), self.assertRaises(InputError):
                clock.frames(label)

    def test_invalid_rates_and_timecodes(self):
        for rate, drop in (("29.97", False), ("23", False), ("30", True), ("24", True)):
            with self.assertRaises(InputError):
                Clock(rate, drop)
        for label in (
            "1:00:00:00",
            "24:00:00:00",
            "00:60:00:00",
            "00:00:60:00",
            "00:00:00:24",
            "00:00:00;01",
            "-1:00:00:00",
        ):
            with self.subTest(label=label), self.assertRaises(InputError):
                Clock("24", False).frames(label)


class ConformTests(unittest.TestCase):
    def test_unique_match_reports_source_handles(self):
        report = audit(EDL, ONE, "24", handles=48)
        self.assertTrue(report["ok"])
        self.assertEqual(
            report["events"][0]["candidates"][0],
            {
                "clip_id": "clip-a",
                "reel": "REEL0001",
                "row": 2,
                "head_frames": 48,
                "tail_frames": 144,
            },
        )

    def test_handles_are_required_on_both_sides(self):
        report = audit(EDL, ONE, "24", handles=49)
        self.assertEqual(report["events"][0]["status"], "insufficient_handles")
        self.assertFalse(report["ok"])
        short = ONE.replace("01:00:10:00", "01:00:04:01")
        self.assertEqual(
            audit(EDL, short, "24", handles=2)["events"][0]["status"], "insufficient_handles"
        )

    def test_exclusive_end_exact_fit_and_one_frame_short(self):
        exact = ONE.replace("01:00:00:00", "01:00:02:00").replace("01:00:10:00", "01:00:04:00")
        self.assertTrue(audit(EDL, exact, "24")["ok"])
        short = exact.replace("01:00:04:00", "01:00:03:23")
        self.assertEqual(audit(EDL, short, "24")["events"][0]["status"], "missing_source")

    def test_equal_reels_with_overlapping_timecodes_are_ambiguous(self):
        inventory = ONE + "clip-b,REEL0001,01:00:01:00,01:00:05:00\n"
        report = audit(EDL, inventory, "24")
        self.assertFalse(report["ok"])
        self.assertEqual(report["events"][0]["status"], "ambiguous")
        self.assertEqual(
            [c["clip_id"] for c in report["events"][0]["candidates"]], ["clip-a", "clip-b"]
        )

    def test_handles_do_not_resolve_an_ambiguous_identity(self):
        inventory = ONE + "clip-b,REEL0001,01:00:02:00,01:00:04:00\n"
        self.assertEqual(
            audit(EDL, inventory, "24", handles=20)["events"][0]["status"], "ambiguous"
        )

    def test_truncation_is_explicit_and_collision_is_visible(self):
        inventory = (
            ONE.replace("REEL0001", "REEL0001_A") + "clip-b,REEL0001_B,01:00:00:00,01:00:10:00\n"
        )
        self.assertEqual(audit(EDL, inventory, "24")["events"][0]["status"], "missing_source")
        report = audit(EDL, inventory, "24", reel_width=8)
        self.assertEqual(report["events"][0]["status"], "ambiguous")
        self.assertEqual(
            report["reel_collisions"][0]["original_reels"], ["REEL0001_A", "REEL0001_B"]
        )

    def test_non_overlapping_collisions_are_informational(self):
        inventory = (
            ONE.replace("REEL0001", "REEL0001_A") + "clip-b,REEL0001_B,02:00:00:00,02:00:10:00\n"
        )
        report = audit(EDL, inventory, "24", reel_width=8)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["reel_collisions"]), 1)

    def test_case_policy_is_explicit(self):
        inventory = ONE.replace("REEL0001", "reel0001")
        self.assertFalse(audit(EDL, inventory, "24")["ok"])
        self.assertTrue(audit(EDL, inventory, "24", case_fold=True)["ok"])

    def test_missing_reel_is_a_finding(self):
        self.assertEqual(
            audit(EDL.replace("REEL0001", "MISSING"), ONE, "24")["events"][0]["status"],
            "missing_source",
        )

    def test_generated_black_does_not_require_inventory_match(self):
        result = audit(EDL.replace("REEL0001", "BL"), ONE, "24", handles=20)
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"][0]["status"], "generated_black")
        with self.assertRaises(InputError):
            audit(EDL, ONE.replace("REEL0001", "BL"), "24")

    def test_drop_frame_event_across_minute_boundary(self):
        edl = "FCM: DROP FRAME\n001 A V C 00:00:59:29 00:01:00:03 01:00:00:00 01:00:00:02\n"
        inventory = HEADER + "a,A,00:00:58:00,00:01:01:00\n"
        result = audit(edl, inventory, "30000/1001")
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["events"][0]["source_end_frame"] - result["events"][0]["source_start_frame"], 2
        )

    def test_informational_comments_do_not_override_matching(self):
        edl = (
            EDL
            + "* FROM CLIP NAME: wrong-name.mov\n* SOURCE FILE: /never/open/this.mov\n* COMMENT: operator note\n"
        )
        result = audit(edl, ONE, "24")
        self.assertTrue(result["ok"])
        self.assertEqual(result["informational_comments"], 3)
        self.assertNotIn("/never/open", json.dumps(result))

    def test_unknown_lines_and_effects_fail_closed(self):
        for directive in (
            "M2 REEL0001 048.0 01:00:00:00",
            "* FREEZE FRAME",
            "SPLIT AUDIO DELAY 2",
            "garbage",
        ):
            with self.subTest(directive=directive), self.assertRaises(InputError):
                audit(EDL + directive, ONE, "24")
        for text in (
            EDL.replace(" V C ", " V D 012 "),
            EDL.replace(" V C ", " A C "),
            EDL.replace("10:00:02:00", "10:00:03:00"),
        ):
            with self.assertRaises(InputError):
                audit(text, ONE, "24")

    def test_header_event_and_order_errors(self):
        cases = [
            "",
            "TITLE: EMPTY\nFCM: NON-DROP FRAME",
            EDL.replace("FCM: NON-DROP FRAME\n", ""),
            EDL + "FCM: NON-DROP FRAME",
            EDL.replace("NON-DROP FRAME", "UNKNOWN"),
            EDL + "TITLE: AGAIN",
            EDL + EDL.splitlines()[-1],
            EDL + EDL.splitlines()[-1].replace("001", "002"),
            EDL.replace("01:00:04:00", "00:00:04:00"),
        ]
        for edl in cases:
            with self.subTest(edl=edl), self.assertRaises(InputError):
                audit(edl, ONE, "24")

    def test_inventory_validation(self):
        cases = [
            "",
            HEADER,
            "clip_id,reel,start,start\na,A,00:00:00:00,00:00:01:00",
            ONE + ONE.splitlines()[-1],
            ONE.replace("clip-a,", " clip-a,"),
            ONE.replace("clip-a,", ","),
            ONE.replace("clip-a,", '"unclosed,'),
            ONE.replace("clip-a,", '"line\nbreak",'),
            ONE.replace("01:00:10:00", "01:00:00:00"),
            ONE.replace("clip-a,", "a,b,"),
            ONE.replace("REEL0001", "SPACE REEL"),
        ]
        for inventory in cases:
            with self.subTest(inventory=inventory), self.assertRaises(InputError):
                audit(EDL, inventory, "24")

    def test_reordered_csv_columns_and_quoted_ids(self):
        text = 'end,start,reel,clip_id\n01:00:10:00,01:00:00:00,REEL0001,"take, one"\n\n'
        self.assertEqual(parse_inventory(text, Clock("24", False))[0].clip_id, "take, one")

    def test_input_and_work_limits(self):
        for kwargs in ({"handles": -1}, {"handles": True}, {"reel_width": 0}, {"reel_width": 129}):
            with self.assertRaises(InputError):
                audit(EDL, ONE, "24", **kwargs)
        for name, value in (
            ("MAX_EVENTS", 0),
            ("MAX_SOURCES", 0),
            ("MAX_CHECKS", 0),
            ("MAX_MATCHES", 0),
        ):
            with patch("reel_ambiguity.core." + name, value), self.assertRaises(InputError):
                audit(EDL, ONE, "24")
        with self.assertRaises(InputError):
            parse_edl(EDL + "* COMMENT:" + "x" * 8192, "24")

    def test_cli_reading_bom_errors_and_outcomes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            edl, source = root / "cut.edl", root / "source.csv"
            edl.write_text(EDL, encoding="utf-8-sig")
            source.write_text(ONE, encoding="utf-8-sig")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main([str(edl), str(source), "--rate", "24"]), 0)
                self.assertEqual(
                    main([str(edl), str(source), "--rate", "24", "--handles", "49"]), 1
                )
                self.assertEqual(
                    main([str(edl), str(source), "--rate", "24", "--handles", "-1"]), 2
                )
                self.assertEqual(main([str(root / "none"), str(source), "--rate", "24"]), 2)
            for data in (b"\xff", b"\0"):
                edl.write_bytes(data)
                with self.assertRaises(InputError):
                    read_text(edl)
            with patch("reel_ambiguity.core.MAX_BYTES", 0), self.assertRaises(InputError):
                read_text(source)

    def test_repository_examples(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        for filename, expected in (("sources.csv", True), ("ambiguous.csv", False)):
            self.assertEqual(
                audit(
                    read_text(examples / "cuts.edl"),
                    read_text(examples / filename),
                    "24",
                    reel_width=8,
                )["ok"],
                expected,
            )


if __name__ == "__main__":
    unittest.main()
