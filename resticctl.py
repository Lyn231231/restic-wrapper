#!/usr/bin/env python3
"""resticctl — a thin, opinionated wrapper around restic for self-hosted boxes.

The wrapper exists because every homelab ends up with the same three problems:

  1. The backup timer ran, restic exited 0, and the repository was not written to
     because a postgres dump was skipped. "Backup succeeded" means nothing.
  2. Nobody ever runs ``restic check`` until the day they need the data.
  3. Retention and verification live in two different crontab lines that drift apart.

resticctl gives you four verbs and one machine-readable status file:

  backup   snapshot with the configured paths/tags, then record the result
  check    ``restic check --read-data-subset`` on a rotating subset
  verify   actually restore a file out of the newest snapshot and compare hashes
  status   print the last result of each verb as JSON (or a table)

Configuration is a small JSON file. Environment for the repo (RESTIC_REPOSITORY,
RESTIC_PASSWORD_FILE, ...) is inherited from the process, so secrets stay in the
service manager, not in the repo.

Exit codes are meaningful and stable, because cron and systemd look at them:
  0 ok, 1 command failed, 2 configuration problem, 3 verification mismatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_STATUS = Path(os.environ.get("RESTICCTL_STATUS", "/var/lib/resticctl/status.json"))


class ConfigError(Exception):
    pass


def load_config(path: str) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}")
    if not cfg.get("paths"):
        raise ConfigError("config needs a non-empty 'paths' list")
    cfg.setdefault("tags", [])
    cfg.setdefault("verify_file", "etc/hostname")
    cfg.setdefault("check_subset", "5%")
    cfg.setdefault("retention", {})
    return cfg


def restic(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run restic, inheriting the repository environment. Never logs the env."""
    cmd = ["restic", *args]
    return subprocess.run(cmd, capture_output=capture, text=True)


def record(status_path: Path, verb: str, ok: bool, detail: str,
           started: float) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data[verb] = {
        "ok": ok,
        "detail": detail[:2000],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(time.time() - started, 1),
    }
    tmp = status_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, status_path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_backup(cfg: dict, status: Path) -> int:
    started = time.time()
    args = ["backup", "--json", *cfg["paths"]]
    for tag in cfg["tags"]:
        args += ["--tag", tag]
    if cfg.get("exclude_file"):
        args += ["--exclude-file", cfg["exclude_file"]]
    r = restic(args)
    if r.returncode != 0:
        record(status, "backup", False, r.stderr, started)
        print(f"backup failed: {r.stderr.strip()}", file=sys.stderr)
        return 1

    summary: dict = {}
    for line in r.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("message_type") == "summary":
            summary = obj
    files_new = summary.get("files_new", 0)
    snapshot = summary.get("snapshot_id", "?")
    detail = f"snapshot {snapshot[:8]} files_new={files_new}"
    record(status, "backup", True, detail, started)

    # a snapshot that added nothing on a machine that should be changing is
    # usually a broken source path, not a quiet day. Surface it.
    if files_new == 0 and not cfg.get("allow_empty_snapshot", False):
        print(f"warning: snapshot {snapshot[:8]} added no new files", file=sys.stderr)

    ret = cfg["retention"]
    if ret and cfg.get("prune_after_backup", False):
        keep = " ".join(f"--keep-{k} {v}" for k, v in ret.items())
        pr = restic(["forget", "--prune", *keep.split()])
        if pr.returncode != 0:
            record(status, "prune", False, pr.stderr, started)
            return 1
        record(status, "prune", True, keep, started)
    return 0


def cmd_check(cfg: dict, status: Path) -> int:
    started = time.time()
    r = restic(["check", f"--read-data-subset={cfg['check_subset']}"])
    ok = r.returncode == 0
    record(status, "check", ok, (r.stdout + r.stderr).strip(), started)
    if not ok:
        print(r.stderr.strip(), file=sys.stderr)
    return 0 if ok else 1


def cmd_verify(cfg: dict, status: Path) -> int:
    """Restore one file from the newest snapshot and compare it to the live copy.

    This is the check that matters. ``check`` proves the repository is
    structurally intact; ``verify`` proves a real file comes back byte-identical.
    """
    started = time.time()
    target = cfg["verify_file"]
    tmpdir = Path(tempfile.mkdtemp(prefix="resticctl-verify-"))
    try:
        r = restic(["restore", "latest", "--target", str(tmpdir), "--include", f"/{target}"])
        if r.returncode != 0:
            record(status, "verify", False, f"restore failed: {r.stderr}", started)
            print(r.stderr.strip(), file=sys.stderr)
            return 1
        restored = tmpdir / target.lstrip("/")
        if not restored.exists():
            record(status, "verify", False, f"{target} not present in latest snapshot", started)
            return 3
        live = Path("/" + target.lstrip("/"))
        if not live.exists():
            # nothing to compare against; the restore itself is still evidence
            record(status, "verify", True, f"restored {target} (no live copy to compare)", started)
            return 0
        same = sha256_file(restored) == sha256_file(live)
        record(status, "verify", same,
               f"{target} {'matches' if same else 'DIFFERS FROM'} live copy", started)
        return 0 if same else 3
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cmd_status(cfg: dict | None, status: Path) -> int:
    if not status.exists():
        print(f"no status file at {status}", file=sys.stderr)
        return 2
    data = json.loads(status.read_text(encoding="utf-8"))
    for verb in ("backup", "check", "verify", "prune"):
        row = data.get(verb)
        if not row:
            continue
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"{mark} {verb:<7} {row['finished_at']}  {row['duration_s']:>7}s  {row['detail'][:80]}")
    return 0 if all(v["ok"] for v in data.values()) else 1


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="/etc/resticctl/config.json")
    common.add_argument("--status", default=str(DEFAULT_STATUS))

    ap = argparse.ArgumentParser(description="thin restic wrapper with real verification")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup", parents=[common])
    sub.add_parser("check", parents=[common])
    sub.add_parser("verify", parents=[common])
    sub.add_parser("status", parents=[common])
    sub.add_parser("init", parents=[common], help="initialise an empty repository")
    args = ap.parse_args(argv)

    status = Path(args.status)
    try:
        cfg = None if args.cmd == "status" else load_config(args.config)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "backup":
        return cmd_backup(cfg, status)
    if args.cmd == "check":
        return cmd_check(cfg, status)
    if args.cmd == "verify":
        return cmd_verify(cfg, status)
    if args.cmd == "status":
        return cmd_status(cfg, status)
    if args.cmd == "init":
        r = restic(["init"])
        print((r.stdout + r.stderr).strip())
        return 0 if r.returncode == 0 else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
