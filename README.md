<div align="center">

# restic-wrapper

**A thin, safe wrapper around restic: named profiles, retention policies, and a dry-run that actually tells you what would be deleted.**

[![tests](https://img.shields.io/badge/tests-passing-brightgreen?style=flat-square)](#quick-start)
[![python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python&logoColor=white)](#quick-start)
[![deps](https://img.shields.io/badge/dependencies-none-lightgrey?style=flat-square)](#quick-start)
[![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

## About

restic's own flags are fine; what is not fine is retyping eight of them at
02:00 and hoping the `--keep-*` set is the one you meant. This wrapper stores a
profile per repository, renders the exact command line it is about to run
(`--dry-run` prints it verbatim and stops), and refuses to run `forget --prune`
without an explicit `--yes`.

The password file is read by path only — it is never put on the command line,
so it cannot leak through `ps`.

## Quick start

```bash
git clone https://github.com/Lyn231231/restic-wrapper
cd restic-wrapper
python3 resticctl.py --config example/config.json --dry-run backup
python3 -m unittest -v
```

## Configuration

| Key | Description |
| --- | --- |
| `repo` | restic repository URL, e.g. `sftp:backup@host:/srv/restic` |
| `password_file` | path to the restic password file, never inline |
| `keep_daily` | daily snapshots to retain |
| `keep_weekly / keep_monthly` | weekly and monthly retention counts |
| `exclude` | list of glob patterns passed as `--exclude` |
## Roadmap

- [ ] Wrapper around `restic check --read-data-subset`
- [ ] systemd timer generator
- [ ] Prometheus textfile output for backup age

## Contributing

Issues and pull requests are welcome. Please keep changes small and focused,
and add a test for any behaviour change — every repository here ships with a
runnable test suite and no third-party dependencies, so CI is just
`python3 -m unittest` / `bash test_*.sh`.

## License

[MIT](LICENSE)
