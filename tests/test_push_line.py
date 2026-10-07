import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import helpers
import push_line as pl
import report_assets
from helpers import good_report, make_png

UTC = timezone.utc
SHA = "b" * 40
G_A, G_B, G_C = "Cfake_group_A", "Cfake_group_B", "Cfake_group_C"
NOW = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)
TODAY = date(2026, 10, 7)
CFG = {G_A: {"name": "群A"}, G_B: {"name": "群B"}, G_C: {"name": "群C", "enabled": False}}
URLS = report_assets.build_urls(SHA, "2026-10-07")


class FakeLine:
    """Scripted fake of the LINE push endpoint. No real HTTP anywhere."""

    def __init__(self, plan=None):
        self.plan = plan or {}   # group_id -> list of outcomes (consumed in order)
        self.calls = []          # (group_id, retry_key, accepted?)
        self.accepted_keys = {}

    def __call__(self, url, headers, body, timeout):
        gid, key = body["to"], headers.get("X-Line-Retry-Key")
        self.calls.append((gid, key))
        outcome = (self.plan.get(gid) or ["ok"]).pop(0) if self.plan.get(gid) else "ok"
        if outcome == "timeout":
            raise ConnectionError("network")
        if outcome == "timeout_after_accept":
            self.accepted_keys[key] = "req-1"
            raise ConnectionError("network")
        if outcome == "ok":
            if key in self.accepted_keys:
                return 409, {"x-line-accepted-request-id": self.accepted_keys[key]}, "{}"
            self.accepted_keys[key] = f"req-{len(self.calls)}"
            return 200, {"x-line-request-id": self.accepted_keys[key]}, "{}"
        if isinstance(outcome, int):
            return outcome, {}, json.dumps({"message": "x"})
        if isinstance(outcome, tuple):
            return outcome[0], outcome[1] if len(outcome) > 2 else {}, outcome[-1]
        raise AssertionError(outcome)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.day = Path(self.tmp.name) / "2026-10-07"
        self.day.mkdir()
        (self.day / "report.json").write_text(json.dumps(good_report(), ensure_ascii=False), encoding="utf-8")
        make_png(self.day / "card.png"); make_png(self.day / "card_preview.png")
        self.state = self.day / "push_state.json"
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, fake, groups=(G_A, G_B), now=NOW, **kw):
        args = dict(group_ids=list(groups), group_config=CFG, token="TOKEN-XYZ", report_path=self.day / "report.json",
                    image_url=URLS["image_url"], preview_url=URLS["preview_url"], text=None, state_path=self.state,
                    today=TODAY, now_fn=lambda: now, post=fake, sleep=lambda s: None,
                    log=lambda *a, **k: self.logs.append(" ".join(map(str, a))))
        args.update(kw)
        return pl.run(**args)

    def st(self):
        return json.loads(self.state.read_text(encoding="utf-8"))


class Retry(Base):
    def test_every_send_has_a_valid_uuid_retry_key(self):
        fake = FakeLine()
        self.assertEqual(self.go(fake), 0)
        keys = [k for _, k in fake.calls]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)  # distinct per target
        import uuid
        for k in keys:
            self.assertEqual(str(uuid.UUID(k)), k)

    def test_a_succeeds_b_fails_then_resume_only_sends_b_with_same_key(self):
        fake = FakeLine({G_B: [500, 500, 500]})
        self.assertEqual(self.go(fake), 1)
        self.assertEqual([g for g, _ in fake.calls], [G_A, G_B, G_B, G_B])
        first_b_key = fake.calls[1][1]
        s = self.st()["targets"]
        tidA, tidB = pl.target_id(G_A), pl.target_id(G_B)
        self.assertEqual(s[tidA]["status"], "accepted")
        self.assertEqual(s[tidB]["status"], "failed_retryable")
        fake.calls.clear()
        self.assertEqual(self.go(fake), 0)
        self.assertEqual([g for g, _ in fake.calls], [G_B])           # A is NOT re-sent
        self.assertEqual(fake.calls[0][1], first_b_key)               # same retry key
        self.assertEqual(self.st()["targets"][tidB]["status"], "accepted")

    def test_timeout_after_line_accepted_is_recognised_via_409(self):
        fake = FakeLine({G_A: ["timeout_after_accept"]})
        self.assertEqual(self.go(fake, groups=[G_A]), 0)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0][1], fake.calls[1][1])
        rec = self.st()["targets"][pl.target_id(G_A)]
        self.assertEqual(rec["status"], "accepted")
        self.assertEqual(rec["request_id"], "req-1")

    def test_409_without_accepted_id_is_manual_review_not_success(self):
        fake = FakeLine({G_A: [(409, {}, "{}")]})
        self.assertEqual(self.go(fake, groups=[G_A]), 1)
        self.assertEqual(self.st()["targets"][pl.target_id(G_A)]["status"], "manual_review")
        fake.calls.clear()
        self.assertEqual(self.go(fake, groups=[G_A]), 1)
        self.assertEqual(fake.calls, [])  # never auto re-sent

    def test_report_exists_but_push_incomplete_resumes_without_regenerating(self):
        fake = FakeLine({G_B: [500, 500, 500]})
        self.go(fake)
        report_bytes = (self.day / "report.json").read_bytes()
        png_bytes = (self.day / "card.png").read_bytes()
        self.go(FakeLine())
        self.assertEqual((self.day / "report.json").read_bytes(), report_bytes)
        self.assertEqual((self.day / "card.png").read_bytes(), png_bytes)

    def test_crash_after_send_before_state_write_is_recoverable(self):
        fake = FakeLine()
        self.go(fake, groups=[G_A])
        rec = self.st()
        tid = pl.target_id(G_A)
        rec["targets"][tid].update(status="pending")
        for k in ("accepted_at", "request_id"):
            rec["targets"][tid].pop(k, None)
        self.state.write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(self.go(fake, groups=[G_A]), 0)
        self.assertEqual(fake.calls[0][1], fake.calls[1][1])
        self.assertEqual(self.st()["targets"][tid]["status"], "accepted")

    def test_two_concurrent_runs_use_the_same_key(self):
        fake = FakeLine()
        s1, s2 = self.state, self.day / "other_state.json"
        self.go(fake, groups=[G_A], state_path=s1)
        self.go(fake, groups=[G_A], state_path=s2)  # a second run with no shared state at all
        self.assertEqual(fake.calls[0][1], fake.calls[1][1])
        self.assertEqual(sum(1 for _ in fake.accepted_keys), 1)  # LINE side saw exactly one logical message

    def test_pending_older_than_24h_is_not_resent(self):
        fake = FakeLine({G_A: [500, 500, 500]})
        self.go(fake, groups=[G_A])
        fake.calls.clear()
        later = NOW + timedelta(hours=25)
        self.assertEqual(self.go(fake, groups=[G_A], now=later), 1)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.st()["targets"][pl.target_id(G_A)]["status"], "manual_review")

    def test_accepted_stays_recorded_after_24h(self):
        fake = FakeLine()
        self.go(fake, groups=[G_A])
        fake.calls.clear()
        self.assertEqual(self.go(fake, groups=[G_A], now=NOW + timedelta(hours=48)), 0)
        self.assertEqual(fake.calls, [])

    def test_4xx_classification_no_blind_retry(self):
        for code, status in ((400, "rejected_permanent"), (401, "auth_error"), (403, "auth_error")):
            with self.subTest(code):
                self.state.unlink(missing_ok=True)
                fake = FakeLine({G_A: [code]})
                self.assertEqual(self.go(fake, groups=[G_A]), 1)
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(self.st()["targets"][pl.target_id(G_A)]["status"], status)

    def test_monthly_quota_429_is_permanent_but_plain_429_retries(self):
        self.state.unlink(missing_ok=True)
        fake = FakeLine({G_A: [(429, {}, '{"message":"You have reached your monthly limit."}')]})
        self.assertEqual(self.go(fake, groups=[G_A]), 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.st()["targets"][pl.target_id(G_A)]["status"], "quota_exceeded")
        self.state.unlink()
        fake = FakeLine({G_A: [429, "ok"]})
        self.assertEqual(self.go(fake, groups=[G_A]), 0)
        self.assertEqual(len(fake.calls), 2)

    def test_permanent_failure_not_retried_on_rerun_unless_asked(self):
        fake = FakeLine({G_A: [401]})
        self.go(fake, groups=[G_A])
        fake.calls.clear()
        self.assertEqual(self.go(fake, groups=[G_A]), 1)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.go(fake, groups=[G_A], retry_permanent=True), 0)

    def test_disabled_group_never_sent_and_duplicates_once(self):
        fake = FakeLine()
        self.assertEqual(self.go(fake, groups=[G_A, G_C, G_A, G_B, G_A]), 0)
        self.assertEqual([g for g, _ in fake.calls], [G_A, G_B])
        self.assertNotIn(pl.target_id(G_C), self.st()["targets"])

    def test_remote_state_unknown_sends_nothing(self):
        fake = FakeLine()
        self.assertEqual(self.go(fake, remote_state="unknown"), 3)
        self.assertEqual(fake.calls, [])
        self.assertFalse(self.state.exists())

    def test_payload_change_blocks_resume(self):
        self.go(FakeLine({G_B: [500, 500, 500]}))
        other = report_assets.build_urls("c" * 40, "2026-10-07")
        fake = FakeLine()
        code = self.go(fake, image_url=other["image_url"], preview_url=other["preview_url"])
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])

    def test_state_file_has_no_secrets_or_raw_group_ids(self):
        self.go(FakeLine())
        raw = self.state.read_text(encoding="utf-8")
        for secret in ("TOKEN-XYZ", G_A, G_B):
            self.assertNotIn(secret, raw)
        self.assertRegex(raw, r'"retry_key": "[0-9a-f-]{36}"')
        self.assertNotIn("TOKEN-XYZ", " ".join(self.logs))
        self.assertNotIn(G_A, " ".join(self.logs))

    def test_corrupt_state_is_fail_closed(self):
        self.state.write_text("{not json", encoding="utf-8")
        fake = FakeLine()
        with self.assertRaises(SystemExit):
            self.go(fake)
        self.assertEqual(fake.calls, [])

    def test_wording_is_accepted_not_delivered(self):
        self.go(FakeLine(), groups=[G_A])
        text = " ".join(self.logs)
        self.assertIn("accepted the request", text)
        self.assertNotIn("delivered", text.lower())


class Gate(Base):
    def test_gate_blocks_everything_when_report_invalid(self):
        r = good_report(); r["sections"]["fx"][0]["dir"] = "invalid"
        (self.day / "report.json").write_text(json.dumps(r), encoding="utf-8")
        fake = FakeLine()
        self.assertEqual(self.go(fake), 2)
        self.assertEqual(fake.calls, [])

    def test_gate_blocks_stale_report(self):
        fake = FakeLine()
        self.assertEqual(self.go(fake, today=date(2026, 10, 8)), 2)
        self.assertEqual(fake.calls, [])

    def test_gate_blocks_missing_or_corrupt_images(self):
        fake = FakeLine()
        (self.day / "card_preview.png").unlink()
        self.assertEqual(self.go(fake), 2)
        make_png(self.day / "card_preview.png")
        (self.day / "card.png").write_bytes(b"junk")
        self.assertEqual(self.go(fake), 2)
        self.assertEqual(fake.calls, [])

    def test_gate_blocks_branch_urls(self):
        base = "https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report/claude/x/reports/2026-10-07"
        fake = FakeLine()
        self.assertEqual(self.go(fake, image_url=base + "/card.png", preview_url=base + "/card_preview.png"), 2)
        self.assertEqual(fake.calls, [])

    def test_gate_blocks_url_for_other_date(self):
        other = report_assets.build_urls(SHA, "2026-10-06")
        fake = FakeLine()
        self.assertEqual(self.go(fake, image_url=other["image_url"], preview_url=other["preview_url"]), 2)

    def test_missing_text_file_is_an_error(self):
        self.assertEqual(pl.main(["--image-url", URLS["image_url"], "--preview-url", URLS["preview_url"],
                                  "--report", str(self.day / "report.json"), "--text-file", str(self.day / "nope.txt")]), 2)

    def test_default_flow_is_image_only(self):
        sent = []

        def post(url, headers, body, timeout):
            sent.append(body)
            return 200, {}, "{}"
        self.go(post, groups=[G_A])
        self.assertEqual([m["type"] for m in sent[0]["messages"]], ["image"])


class Helpers(unittest.TestCase):
    def test_retry_key_is_deterministic_and_input_sensitive(self):
        a = pl.retry_key("2026-10-07", "t1", "p1")
        self.assertEqual(a, pl.retry_key("2026-10-07", "t1", "p1"))
        for args in (("2026-10-08", "t1", "p1"), ("2026-10-07", "t2", "p1"), ("2026-10-07", "t1", "p2")):
            self.assertNotEqual(a, pl.retry_key(*args))

    def test_classify(self):
        self.assertEqual(pl.classify(200, {"X-Line-Request-Id": "r"}, "")[0], "accepted")
        self.assertEqual(pl.classify(409, {"x-line-accepted-request-id": "r"}, "")[0], "accepted")
        self.assertEqual(pl.classify(409, {}, "")[0], "unknown")
        self.assertEqual(pl.classify(503, {}, "")[0], "retry")
        self.assertEqual(pl.classify(422, {}, "")[0], "rejected_permanent")

    def test_bad_group_config_is_friendly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"; p.write_text("{bad", encoding="utf-8")
            with self.assertRaises(SystemExit):
                pl.load_group_config(p)
            self.assertEqual(pl.load_group_config(Path(d) / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
