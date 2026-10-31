# restic-wrapper

`resticctl`, a thin wrapper around [restic](https://restic.net/) that answers the
question the backup timer cannot: *would this data actually come back?*

## The three failures this exists for

**"Backup succeeded" and the data was not there.** restic exits 0 when the
snapshot was written. If the source path was empty because a container had not
started yet, you get a perfectly valid snapshot of nothing. `resticctl backup`
prints the summary and warns when a snapshot added zero new files.

**Nobody runs `check`.** `restic check --read-data-subset` needs to run on a
schedule that is independent of the backup, and someone has to look at the exit
code. `resticctl check` does one rotating subset per run and records the result.

**Nobody runs a restore.** A structurally valid repository can still hand you the
wrong bytes. `resticctl verify` restores one file from the latest snapshot into a
temp directory, hashes it, and compares it to the live file. This is the only
check that touches the actual recovery path.

Every verb writes into a single status JSON, so a health check, a panic button,
or a person at a terminal all read the same file:

```bash
$ resticctl status
ok   backup  2026-10-30T03:12:44Z     44.1s  snapshot 9f2c1a7d files_new=1834
ok   check   2026-10-30T04:00:02Z    118.6s  no errors were found
ok   verify  2026-10-30T04:02:11Z      2.3s  /etc/hostname matches live copy
ok   prune   2026-10-30T03:12:44Z      9.7s  --keep-daily 7 --keep-weekly 4 --keep-monthly 6
```

## Install

```bash
curl -o /usr/local/bin/resticctl https://raw.githubusercontent.com/Lyn231231/restic-wrapper/main/resticctl.py
chmod +x /usr/local/bin/resticctl
```

`resticctl` uses the standard library only — no `pip install` step.

Needs `restic` on `PATH` and the usual `RESTIC_REPOSITORY` / `RESTIC_PASSWORD_FILE`
environment. `resticctl` never reads or prints your password; it passes the
environment through to restic unchanged.

## Configure

```json
{
  "paths": ["/srv", "/etc"],
  "exclude_file": "/etc/resticctl/excludes.txt",
  "tags": ["homelab", "nas"],
  "retention": {"daily": 7, "weekly": 4, "monthly": 6},
  "prune_after_backup": true,
  "verify_file": "etc/hostname",
  "check_subset": "5%",
  "allow_empty_snapshot": false
}
```

`verify_file` is a path relative to `/` — pick something small, stable and
important (a config file, not a database dump). `allow_empty_snapshot` silences
the zero-new-files warning for archives that legitimately never change.

## Schedule

```
# /etc/systemd/system/restic-backup.service
[Service]
EnvironmentFile=/etc/resticctl/restic.env
ExecStart=/usr/local/bin/resticctl backup
```

```
# /etc/systemd/system/restic-backup.timer
[Timer]
OnCalendar=daily
RandomizedDelaySec=30m
```

Run `resticctl check` weekly and `resticctl verify` weekly, on a different day
from the check. Three timers, three independent failure modes.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | the command succeeded |
| 1 | restic failed (repository locked, network, credentials) |
| 2 | configuration problem |
| 3 | the restored file did not match the live one |

## Tests

```bash
python3 -m unittest -v test_resticctl
```

`restic` itself is mocked. The tests cover snapshot-summary parsing, empty
snapshots, the status file surviving corruption, and the verify path both
matching and mismatching.

## License

MIT. See [LICENSE](LICENSE).
