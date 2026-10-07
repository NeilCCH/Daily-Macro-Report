import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import report_assets as ra

SHA = "a" * 40


class Assets(unittest.TestCase):
    def test_urls_pinned_and_dated(self):
        u = ra.build_urls(SHA, "2026-10-07")
        self.assertTrue(u["image_url"].endswith(f"/{SHA}/reports/2026-10-07/card.png"))
        self.assertTrue(u["preview_url"].endswith(f"/{SHA}/reports/2026-10-07/card_preview.png"))
        self.assertIsNone(ra.check_pinned_url(u["image_url"], "2026-10-07", "card"))
        self.assertIsNone(ra.check_pinned_url(u["preview_url"], "2026-10-07", "card_preview"))

    def test_branch_names_and_short_shas_rejected(self):
        for bad in ("claude/focused-dirac-lxg3xx", "main", "abc1234", "A" * 40, "", None, "g" * 40):
            with self.assertRaises(ValueError):
                ra.build_urls(bad, "2026-10-07")
        with self.assertRaises(ValueError):
            ra.build_urls(SHA, "2026/10/07")

    def test_branch_url_fails_check(self):
        url = "https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report/claude/x/reports/2026-10-07/card.png"
        self.assertIsNotNone(ra.check_pinned_url(url, "2026-10-07", "card"))

    def test_date_and_name_mismatch(self):
        u = ra.build_urls(SHA, "2026-10-06")["image_url"]
        self.assertIn("date", ra.check_pinned_url(u, "2026-10-07", "card"))
        self.assertIn("points at", ra.check_pinned_url(u, "2026-10-06", "card_preview"))

    def test_url_never_contains_branch(self):
        u = ra.build_urls(SHA, "2026-10-07")
        self.assertNotIn("claude/", u["image_url"] + u["preview_url"])

    def test_git_object_check_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            (Path(d) / "reports/2026-10-07").mkdir(parents=True)
            (Path(d) / "reports/2026-10-07/card.png").write_bytes(b"x")
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]}
            subprocess.run(["git", "-C", d, "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", d, "commit", "-qm", "x"], check=True, env=env)
            sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            self.assertTrue(ra.git_object_exists(sha, "2026-10-07", "card.png", d))
            self.assertFalse(ra.git_object_exists(sha, "2026-10-07", "card_preview.png", d))
            self.assertFalse(ra.git_object_exists("f" * 40, "2026-10-07", "card.png", d))


if __name__ == "__main__":
    unittest.main()
