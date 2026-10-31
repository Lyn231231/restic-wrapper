import json
import os
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resticctl  # noqa: E402


def fake_restic(returncode=0, stdout="", stderr=""):
    return mock.Mock(return_value=mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr))


class TestConfig(unittest.TestCase):
    def test_missing_paths_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"tags": ["x"]}, fh)
        self.addCleanup(os.unlink, fh.name)
        with self.assertRaises(resticctl.ConfigError):
            resticctl.load_config(fh.name)

    def test_defaults_filled(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"paths": ["/srv"]}, fh)
        self.addCleanup(os.unlink, fh.name)
        cfg = resticctl.load_config(fh.name)
        self.assertEqual(cfg["tags"], [])
        self.assertEqual(cfg["check_subset"], "5%")

    def test_bad_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{nope")
        self.addCleanup(os.unlink, fh.name)
        with self.assertRaises(resticctl.ConfigError):
            resticctl.load_config(fh.name)


class TestRecord(unittest.TestCase):
    def test_record_merges_verbs(self):
        with tempfile.TemporaryDirectory() as d:
            status = Path(d) / "nested" / "status.json"
            resticctl.record(status, "backup", True, "snap abc", 0.0)
            resticctl.record(status, "check", False, "boom", 0.0)
            data = json.loads(status.read_text())
            self.assertEqual(sorted(data), ["backup", "check"])
            self.assertTrue(data["backup"]["ok"])
            self.assertFalse(data["check"]["ok"])

    def test_corrupt_status_is_replaced_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            status = Path(d) / "status.json"
            status.write_text("garbage")
            resticctl.record(status, "backup", True, "fresh", 0.0)
            self.assertIn("backup", json.loads(status.read_text()))


class TestBackup(unittest.TestCase):
    def cfg(self):
        return {"paths": ["/srv"], "tags": ["homelab"], "retention": {},
                "verify_file": "etc/hostname", "check_subset": "5%"}

    def test_backup_success_records_summary(self):
        summary = json.dumps({"message_type": "summary", "files_new": 12,
                              "snapshot_id": "deadbeefcafe"})
        with tempfile.TemporaryDirectory() as d:
            status = Path(d) / "s.json"
            with mock.patch.object(resticctl, "restic", fake_restic(stdout=summary)):
                self.assertEqual(resticctl.cmd_backup(self.cfg(), status), 0)
            row = json.loads(status.read_text())["backup"]
            self.assertTrue(row["ok"])
            self.assertIn("deadbeef", row["detail"])

    def test_backup_failure_records_and_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            status = Path(d) / "s.json"
            with mock.patch.object(resticctl, "restic", fake_restic(1, stderr="repo locked")):
                self.assertEqual(resticctl.cmd_backup(self.cfg(), status), 1)
            self.assertFalse(json.loads(status.read_text())["backup"]["ok"])

    def test_empty_snapshot_warns(self):
        summary = json.dumps({"message_type": "summary", "files_new": 0, "snapshot_id": "x"})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(resticctl, "restic", fake_restic(stdout=summary)), \
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as err:
            rc = resticctl.cmd_backup(self.cfg(), Path(d) / "s.json")
        self.assertEqual(rc, 0)
        self.assertIn("added no new files", err.getvalue())


class TestVerify(unittest.TestCase):
    def _run_verify(self, live_text, restored_text):
        """Run cmd_verify with restic faked to 'restore' restored_text."""
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / "etc" / "hostname"  # stands in for /etc/hostname
            live.parent.mkdir(parents=True)
            live.write_text(live_text)
            status = Path(d) / "s.json"
            cfg = {"verify_file": "etc/hostname", "tags": [], "check_subset": "5%",
                   "retention": {}}

            def fake_restic(args):
                target = Path(args[args.index("--target") + 1])
                out = Path(posixpath.join(str(target), "etc", "hostname"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(restored_text)
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(resticctl, "restic", side_effect=fake_restic), \
                    mock.patch.object(resticctl, "Path",
                                      side_effect=lambda p=".": Path(d) / p.lstrip("/")
                                      if str(p).startswith("/") else Path(p)):
                rc = resticctl.cmd_verify(cfg, status)
            return rc, json.loads(status.read_text())["verify"]

    def test_roundtrip_match_exits_0(self):
        rc, row = self._run_verify("web-01\n", "web-01\n")
        self.assertEqual(rc, 0)
        self.assertTrue(row["ok"])

    def test_mismatch_exits_3(self):
        rc, row = self._run_verify("web-01\n", "web-02\n")
        self.assertEqual(rc, 3)
        self.assertFalse(row["ok"])
        self.assertIn("DIFFERS", row["detail"])

    def test_sha256_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a"
            p.write_bytes(b"hello")
            self.assertEqual(
                resticctl.sha256_file(p),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )


class TestCli(unittest.TestCase):
    def test_status_missing_file_is_2(self):
        with tempfile.TemporaryDirectory() as d:
            rc = resticctl.main(["status", "--status", str(Path(d) / "nope.json")])
        self.assertEqual(rc, 2)

    def test_bad_config_is_2(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "c.json"
            bad.write_text("{}")
            rc = resticctl.main(["backup", "--config", str(bad), "--status", str(Path(d) / "s.json")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
