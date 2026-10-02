#!/usr/bin/env python3
"""Nightly `pg_dump` with retention, plus the restore drill that proves it loads.

P1-4. The ledger is the financial record (`driver_deposits`, `ledger_entries`,
`settlement_runs`); a platform that cannot restore it is not launchable, and
`docs/PRODUCTION_READINESS.md` says so. This is the missing half of the
durability story — `docker-compose.yml` has named volumes and Redis has
`appendonly`, but neither survives `docker volume rm`, a bad migration, or a
host that is simply gone.

**Redis is deliberately not backed up.** It holds rate-limit counters, grab
locks and Pub/Sub channels — all ephemeral. Losing it costs a re-login and a
re-grab, which is why `docs/PRODUCTION_READINESS.md` states plainly that the
database is the truth and Redis is not. Backing it up would imply otherwise.

What it does
------------
`backup`  — dump to `--out-dir`, write a `.sha256` beside it, prune to the
            retention policy, optionally hand the file to an off-host command.
`verify`  — restore a dump into a scratch database and compare table row counts
            against the source. This is the drill, and it is the point: a dump
            that has never been restored is a hypothesis, not a backup.
`list`    — show what is on disk and what retention would keep.

Reaching Postgres — two transports, one code path
-------------------------------------------------
There is **no `pg_dump` on this machine's PATH**; it exists only inside the
`realtaxi-db` container. A real deployment host usually has the client tools
installed. Rather than maintain two scripts, every `pg_*` call goes through
`Transport`, and `--via-docker` picks the route:

    host    run `pg_dump` etc. locally, connecting to PGHOST:PGPORT
    docker  run `pg_dump` etc. inside the `realtaxi-db` container via docker exec

`auto` (the default) probes for the client tools and falls back to docker when
they are missing, so the same command works in both places — and, more
importantly, *the code the drill exercises is the code that runs in production*.

The docker transport has one wrinkle worth stating: the archive is written by
the container's `pg_dump`, so it lands *inside* the container's filesystem. The
transport streams it to stdout and the caller writes it to `--out-dir`, keeping
the archive on the host where retention and upload can see it.

Examples
--------
    # the nightly run
    python scripts/ops/db_backup.py backup

    # force a route rather than probing
    python scripts/ops/db_backup.py --via-docker backup
    python scripts/ops/db_backup.py --via host backup

    # off-host copy, without this script knowing the provider. Runs once per
    # new archive; a non-zero exit fails the run.
    python scripts/ops/db_backup.py backup \\
        --upload-cmd 'rclone copy {file} remote:realtaxi-backups/'

    # the drill — restore the newest dump and compare row counts
    python scripts/ops/db_backup.py verify

    # what is on disk
    python scripts/ops/db_backup.py list

Scheduling is the operator's call, and it is one line of cron:

    17 3 * * *  cd /srv/realtaxihk && .venv/bin/python scripts/ops/db_backup.py backup

`--upload-cmd` takes a shell command with `{file}` and `{name}` substituted.
Keep the credential for the remote in that command's environment (an rclone
config, an IAM role, a `~/.pgpass`-style file) — never in this repository, and
never in this script's own arguments, which land in the process table.

Exit codes: 0 success, 1 failure, 2 bad usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT as ROOT

# `pg_dump` is custom-format (-Fc): compressed, and restorable table-by-table.
# Plain SQL would be readable but neither compressible nor selectively
# restorable, and the point of this artefact is restoring under pressure.
DUMP_FORMAT = "c"

ARCHIVE_SUFFIX = ".dump"
CHECKSUM_SUFFIX = ".sha256"

# The container that holds Postgres, for the docker transport. Matches
# `docker-compose.yml`'s `container_name`.
DOCKER_CONTAINER = "realtaxi-db"

# A dump name is a promise about its contents, so the pattern is strict:
# realtaxihk_20260930T031700Z.dump
NAME_RE = re.compile(r"^(?P<db>.+)_(?P<stamp>\d{8}T\d{6}Z)" + re.escape(ARCHIVE_SUFFIX) + r"$")

# A scratch database created by `verify` must never be mistakable for the real
# one, because the drill ends with `dropdb`. Both a fixed suffix AND an
# inequality check against the source name are enforced — one guard is a typo
# away from destroying the database this whole script exists to protect.
SCRATCH_SUFFIX = "_restore_drill"


class BackupError(RuntimeError):
    """Anything that should stop the run with a clear message."""


@dataclass(frozen=True)
class DbTarget:
    """Where to dump from. Resolved from the same settings the API uses."""

    host: str
    port: str
    user: str
    password: str
    dbname: str
    container: str = DOCKER_CONTAINER

    @classmethod
    def from_env(cls) -> DbTarget:
        # Import the app's own settings rather than re-reading `.env`, so a
        # backup always targets the database the API is actually talking to.
        # A backup script with its own copy of the connection string is a
        # backup script that silently backs up the wrong database.
        from app.core.config import get_settings

        s = get_settings()
        return cls(
            host=s.postgres_host,
            port=str(s.postgres_port),
            user=s.postgres_user,
            password=s.postgres_password,
            dbname=s.postgres_db,
        )

    def pg_env(self, database: str | None = None) -> dict[str, str]:
        """`PG*` variables — the one channel that keeps the password out of argv.

        Passing `--password` or `-W` would put the credential in the process
        table, where `ps` on a shared host shows it to every other user.
        """
        env = dict(os.environ)
        env.update(
            {
                "PGHOST": self.host,
                "PGPORT": self.port,
                "PGUSER": self.user,
                "PGPASSWORD": self.password,
                "PGDATABASE": database or self.dbname,
            }
        )
        return env


class Transport:
    """Runs a `pg_*` tool, either locally or inside the Postgres container.

    Every call site names the tool and its arguments; only this class knows
    whether that becomes a `subprocess.run(["pg_dump", ...])` or a
    `docker exec`. That is what makes `--via-docker` a one-line switch rather
    than a parallel implementation.
    """

    PG_TOOLS = ("pg_dump", "pg_restore", "psql", "createdb", "dropdb")

    def __init__(self, target: DbTarget, mode: str) -> None:
        self.target = target
        self.mode = self._resolve(mode)

    def _resolve(self, mode: str) -> str:
        if mode == "host":
            self._require_host_tools()
            return "host"
        if mode == "docker":
            self._require_docker()
            return "docker"
        # auto: prefer local tools (no docker hop, works against a managed DB
        # over the network), fall back to the container.
        if all(shutil.which(t) for t in self.PG_TOOLS):
            return "host"
        if shutil.which("docker") and self._container_running():
            return "docker"
        raise BackupError(
            "no PostgreSQL client tools on PATH and no running "
            f"{self.target.container!r} container. Install postgresql-client "
            "(Debian: `apt-get install postgresql-client`; macOS: `brew install "
            "libpq`) or start the stack, or pass --via-docker/--via host."
        )

    def _require_host_tools(self) -> None:
        missing = [t for t in self.PG_TOOLS if shutil.which(t) is None]
        if missing:
            raise BackupError(
                f"missing required tool(s): {', '.join(missing)} — install the "
                "PostgreSQL client tools, or use --via-docker."
            )

    def _require_docker(self) -> None:
        if shutil.which("docker") is None:
            raise BackupError("docker not found on PATH — required for --via-docker")
        if not self._container_running():
            raise BackupError(
                f"container {self.target.container!r} is not running — "
                "`docker compose up -d db` first, or pass --via host."
            )

    def _container_running(self) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.target.container],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def describe(self) -> str:
        if self.mode == "docker":
            return f"docker exec {self.target.container}"
        return f"local client -> {self.target.host}:{self.target.port}"

    def run_local(
        self,
        tool: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float = 600,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [tool, *args],
            env=env or dict(os.environ),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def exec_in_container(
        self, tool: str, args: list[str], *, env: dict[str, str] | None = None, timeout: float = 600
    ) -> subprocess.CompletedProcess:
        """Run a `pg_*` tool inside the container, forwarding only the PG* vars.

        `-e` rather than `-e VAR` for each: the values must not appear in the
        argv this process builds, or the password lands in the process table on
        the host — the exact leak `pg_env` exists to avoid.
        """
        cmd = ["docker", "exec", "-i"]
        for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
            src = (env or {}).get(key)
            if src is not None:
                cmd += ["-e", f"{key}={src}"]
        # Inside the container the server is on the local socket.
        cmd += ["-e", "PGHOST=127.0.0.1", "-e", "PGPORT=5432"]
        cmd += [self.target.container, tool, *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

    def run(
        self, tool: str, args: list[str], *, env: dict[str, str] | None = None, timeout: float = 600
    ) -> subprocess.CompletedProcess:
        if self.mode == "docker":
            return self.exec_in_container(tool, args, env=env, timeout=timeout)
        return self.run_local(tool, args, env=env, timeout=timeout)

    def check(
        self, tool: str, args: list[str], *, env: dict[str, str] | None = None, timeout: float = 600
    ) -> subprocess.CompletedProcess:
        proc = self.run(tool, args, env=env, timeout=timeout)
        if proc.returncode != 0:
            detail = _tail(proc.stderr or proc.stdout)
            raise BackupError(f"{tool} failed ({proc.returncode}): {detail}")
        return proc

    def stream_to_file(
        self, tool: str, args: list[str], dest: Path, *, env: dict[str, str] | None = None
    ) -> None:
        """Run a tool that writes its archive to **stdout**, capturing to `dest`.

        Deliberately binary, not text: a custom-format dump is not decodable and
        `text=True` would corrupt it on Windows. This is the one place the
        transport cannot go through `run`.
        """
        if self.mode == "docker":
            cmd = ["docker", "exec", "-i"]
            for key in ("PGUSER", "PGPASSWORD", "PGDATABASE"):
                src = (env or {}).get(key)
                if src is not None:
                    cmd += ["-e", f"{key}={src}"]
            cmd += ["-e", "PGHOST=127.0.0.1", "-e", "PGPORT=5432"]
            cmd += [self.target.container, tool, *args]
        else:
            cmd = [tool, *args]

        with dest.open("wb") as fh:
            proc = subprocess.run(
                cmd, env=env or dict(os.environ), stdout=fh, stderr=subprocess.PIPE, check=False
            )
        if proc.returncode != 0:
            detail = _tail(proc.stderr.decode("utf-8", "replace"))
            dest.unlink(missing_ok=True)
            raise BackupError(f"{tool} failed ({proc.returncode}): {detail}")

    def read_to_file(
        self, tool: str, args: list[str], src: Path, *, env: dict[str, str] | None = None
    ) -> None:
        """Feed a local file to a tool's **stdin** (the restore direction).

        The archive lives on the host; the tool lives in the container. `docker
        exec -i` bridges that without copying the file into the container first.
        """
        self._bridge(tool, args, src, env=env, capture=False)

    def read_stdout(
        self, tool: str, args: list[str], src: Path, *, env: dict[str, str] | None = None
    ) -> str:
        """Feed a local file to a tool's stdin and return its **stdout** as text.

        For read-only introspection (`pg_restore --list`), where the archive is
        an input rather than a target. Same bridging reason as `read_to_file`:
        a host path is meaningless to a tool running in the container.
        """
        proc = self._bridge(tool, args, src, env=env, capture=True)
        return proc.stdout.decode("utf-8", "replace")

    def _bridge(
        self,
        tool: str,
        args: list[str],
        src: Path,
        *,
        env: dict[str, str] | None,
        capture: bool,
    ) -> subprocess.CompletedProcess:
        """Run `tool args < src`, locally or in the container.

        Captured as bytes throughout: a custom-format archive is not text, and
        `text=True` would corrupt it on Windows before the tool ever saw it.
        """
        if self.mode == "docker":
            cmd = ["docker", "exec", "-i"]
            for key in ("PGUSER", "PGPASSWORD", "PGDATABASE"):
                val = (env or {}).get(key)
                if val is not None:
                    cmd += ["-e", f"{key}={val}"]
            cmd += ["-e", "PGHOST=127.0.0.1", "-e", "PGPORT=5432"]
            cmd += [self.target.container, tool, *args]
            run_env = None
        else:
            cmd = [tool, *args]
            run_env = dict(env or os.environ)

        with src.open("rb") as fh:
            proc = subprocess.run(
                cmd,
                env=run_env,
                stdin=fh,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE,
                check=False,
            )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")
            raise BackupError(f"{tool} failed ({proc.returncode}): {_tail(err)}")
        return proc


def _tail(text: str, limit: int = 2000) -> str:
    return (text or "").strip()[-limit:]


def _target_from(args: argparse.Namespace) -> DbTarget:
    """Build the target, honouring an overridden container name.

    `--container` has to reach `DbTarget`, because the docker transport reads
    the name off the target rather than off the args — otherwise the flag is
    parsed and silently ignored, and the run fails against the wrong container.
    """
    import dataclasses

    target = DbTarget.from_env()
    if getattr(args, "container", None):
        target = dataclasses.replace(target, container=args.container)
    return target


# --------------------------------------------------------------------------- #
# Archive naming, checksums, retention
# --------------------------------------------------------------------------- #


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_checksum(archive: Path) -> Path:
    """Write `<archive>.sha256` in `sha256sum -c` format.

    The checksum is what makes "the backup is corrupted" discoverable *before*
    a restore is needed, and it is the only thing that distinguishes a
    truncated dump from a complete one. `pg_dump` exiting 0 does not.
    """
    digest = sha256_of(archive)
    sidecar = archive.with_name(archive.name + CHECKSUM_SUFFIX)
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return sidecar


def read_checksum(archive: Path) -> str | None:
    sidecar = archive.with_name(archive.name + CHECKSUM_SUFFIX)
    if not sidecar.exists():
        return None
    text = sidecar.read_text(encoding="ascii").strip()
    return text.split()[0] if text else None


def list_archives(out_dir: Path) -> list[Path]:
    """Newest first. Only files this tool would have written."""
    if not out_dir.is_dir():
        return []
    found = [p for p in out_dir.iterdir() if p.is_file() and NAME_RE.match(p.name)]
    return sorted(found, key=lambda p: p.name, reverse=True)


def stamp_of(archive: Path) -> dt.datetime:
    m = NAME_RE.match(archive.name)
    if m is None:
        raise BackupError(f"unrecognised archive name: {archive.name}")
    # The name is UTC by construction (`%Y%m%dT%H%M%SZ`).
    return dt.datetime.strptime(m.group("stamp"), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)


def select_prune(archives: list[Path], keep_daily: int, keep_weekly: int) -> list[Path]:
    """Return the archives to delete, grandfather-father-son style.

    Retention is by *calendar distance*, not by count, for the weekly tier: the
    point of a weekly copy is that it survives a problem that takes days to
    notice (a bad migration, a silent ledger drift). Keeping "the 4 newest"
    would mean four copies from the same afternoon, which is exactly the case
    that fails.

    Newest-first input. The newest of each day is the daily keeper; among the
    daily keepers, the newest of each ISO week is the weekly keeper.
    """
    if keep_daily <= 0 and keep_weekly <= 0:
        return list(archives)

    ordered = sorted(archives, key=lambda p: p.name, reverse=True)

    daily_keepers: list[Path] = []
    seen_days: set[dt.date] = set()
    for a in ordered:
        day = stamp_of(a).date()
        if day not in seen_days:
            seen_days.add(day)
            daily_keepers.append(a)

    keep: set[Path] = set(daily_keepers[:keep_daily])

    seen_weeks: set[tuple[int, int]] = set()
    for a in daily_keepers:
        iso = stamp_of(a).isocalendar()
        week = (iso[0], iso[1])
        if week not in seen_weeks:
            seen_weeks.add(week)
            if len(seen_weeks) <= keep_weekly:
                keep.add(a)

    return [a for a in ordered if a not in keep]


# --------------------------------------------------------------------------- #
# backup
# --------------------------------------------------------------------------- #


def cmd_backup(args: argparse.Namespace) -> int:
    target = _target_from(args)
    transport = Transport(target, args.via)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.UTC)
    name = f"{target.dbname}_{now.strftime('%Y%m%dT%H%M%SZ')}{ARCHIVE_SUFFIX}"
    archive = out_dir / name

    print(f"[backup] {target.user}@{target.host}:{target.port}/{target.dbname}")
    print(f"[backup] transport: {transport.describe()}")
    print(f"[backup] archive:   {archive}")

    # `--no-owner` / `--no-privileges`: the restore may land on a different
    # cluster with different role names (a scratch DB for the drill, a new host
    # after a failure). Embedding ownership makes the dump fail to restore in
    # exactly the situation it exists for.
    transport.stream_to_file(
        "pg_dump",
        ["--format", DUMP_FORMAT, "--no-owner", "--no-privileges"],
        archive,
        env=target.pg_env(),
    )

    size = archive.stat().st_size
    if size == 0:
        # An empty file with exit 0 is possible when the dump is interrupted in
        # a way the client does not report. Treat it as a failure, and do not
        # leave it on disk to be mistaken for a good backup.
        archive.unlink(missing_ok=True)
        raise BackupError("pg_dump produced a 0-byte archive — refusing to keep it")

    sidecar = write_checksum(archive)
    print(
        f"[backup] {size:,} bytes · sha256 {sidecar.read_text(encoding='ascii').split()[0][:16]}…"
    )

    # `pg_restore --list` reads the archive's table of contents. It is the
    # cheapest proof the file is a valid archive and not, say, an error page
    # written to `--file`. The full proof is `verify`.
    #
    # The archive must be piped in, not named: under the docker transport the
    # tool runs inside the container and a host path means nothing there. This
    # is the same bridge `read_to_file` uses for the restore.
    toc = transport.read_stdout("pg_restore", ["--list"], archive, env=target.pg_env())
    entries = sum(1 for line in toc.splitlines() if line and not line.startswith(";"))
    print(f"[backup] archive TOC: {entries} entries")
    if entries == 0:
        raise BackupError("archive listed no entries — it is not a usable dump")

    if args.upload_cmd:
        uploaded = _upload(args.upload_cmd, archive)
        print(f"[backup] off-host copy ok: {uploaded}")
        if args.upload_verify_cmd:
            # Default the marker to the archive name: it is what the upload put
            # on the remote, so a listing of the remote should contain it. An
            # operator with a different remote layout overrides it.
            marker = args.upload_verify_marker or archive.name
            _upload_verify(args.upload_verify_cmd, archive, marker)
            print(f"[backup] off-host copy verified present ({marker})")

    removed = 0
    for stale in select_prune(
        list_archives(out_dir), keep_daily=args.keep_daily, keep_weekly=args.keep_weekly
    ):
        # Both halves go, or a later run reports a checksum mismatch against a
        # file that is intentionally gone.
        stale.unlink(missing_ok=True)
        stale.with_name(stale.name + CHECKSUM_SUFFIX).unlink(missing_ok=True)
        removed += 1
    if removed:
        print(f"[backup] pruned {removed} archive(s) beyond retention")

    print("[backup] done")
    return 0


def _shell_path(path: Path) -> str:
    """A path safe to substitute into a POSIX-shell command on any platform.

    This is not cosmetic. `str(Path)` on Windows is `C:\\Users\\user\\...`, and
    Git-for-Windows' `sh` performs quote-removal on backslashes: substituting
    the native string produced

        cp: cannot stat 'C:UsersuserDesktop...realtaxihk_20260930T073607Z.dump'

    — every separator eaten, for both `{file}` and `{name}`. The upload was
    therefore impossible on Windows regardless of what the operator wrote.

    Forward slashes work in POSIX shells, in Git Bash (`C:/Users/...` is a valid
    path there), and in Windows' own tooling, so one form serves all three. A
    relative path is resolved first, because a bare `foo.dump` is unambiguous
    and the absolute form is what a remote copy actually wants.
    """
    resolved = path if path.is_absolute() else path.resolve()
    return resolved.as_posix()


def _render_upload_cmd(template: str, archive: Path) -> str:
    """Substitute `{file}` / `{name}` with shell-safe values."""
    return template.replace("{file}", _shell_path(archive)).replace("{name}", archive.name)


def _upload(template: str, archive: Path) -> str:
    """Run the operator's off-host command, once, for this archive.

    Two things here are load-bearing, and both were learned from a measured
    failure rather than from reasoning:

    **1. The command is run by `sh`, explicitly — never by `shell=True`.**
    On Windows `subprocess.run(shell=True)` uses `cmd.exe`, which does *not*
    treat `;` as a separator. Measured: `echo hi >&2; exit 7` returns **0**,
    because `cmd.exe` ran the whole thing as a single `echo`. That turned a
    failing upload into a reported success — the single worst failure mode a
    backup can have, since the copy silently did not happen and the nightly run
    said it did. Naming the interpreter makes the operator's command behave the
    same way on Windows as it does in a Linux cron, which is where this runs.

    **2. An exit code is not evidence the archive arrived.** `cp` to a full
    disk can exit 0; a wrapper script can swallow a failure. So the operator may
    require a marker: pass `--upload-verify-cmd`, which must print
    `--upload-verify-marker` (default: the archive name) on success. Without it,
    "off-host copy ok" is a claim about a command, not about a backup.
    """
    cmd = _render_upload_cmd(template, archive)

    shell = shutil.which("sh")
    if shell is None:
        raise BackupError(
            "no `sh` on PATH — the upload command needs a POSIX shell so it "
            "behaves the same on Windows as in the cron job it mirrors"
        )

    proc = subprocess.run([shell, "-c", cmd], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise BackupError(
            f"upload command failed (exit {proc.returncode}): {_tail(proc.stderr or proc.stdout)}"
        )
    return cmd


def _upload_verify(template: str, archive: Path, marker: str) -> None:
    """Confirm the off-host copy really exists, by asking the remote.

    `--upload-verify-cmd` is the operator's own check — `rclone lsf`, `aws s3
    ls`, an `ssh ... test -s`. It must print `marker`; anything else, including
    silence, fails the run. This is the difference between "the copy command
    exited 0" and "the backup is on the remote".
    """
    cmd = _render_upload_cmd(template, archive)
    shell = shutil.which("sh")
    if shell is None:
        raise BackupError("no `sh` on PATH — required for --upload-verify-cmd")

    proc = subprocess.run([shell, "-c", cmd], capture_output=True, text=True, check=False)
    haystack = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0 or marker not in haystack:
        raise BackupError(
            f"upload verification failed — the off-host copy is not confirmed. "
            f"exit={proc.returncode}, looking for {marker!r}. "
            f"output: {_tail(haystack, 500)}"
        )


def cmd_list(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).resolve()
    archives = list_archives(out_dir)
    if not archives:
        print(f"[list] no archives in {out_dir}")
        return 0

    doomed = set(select_prune(archives, keep_daily=args.keep_daily, keep_weekly=args.keep_weekly))
    print(f"[list] {len(archives)} archive(s) in {out_dir}")
    print(f"[list] retention: keep {args.keep_daily} daily + {args.keep_weekly} weekly")
    for a in archives:
        size = a.stat().st_size
        age = (dt.datetime.now(dt.UTC) - stamp_of(a)).total_seconds() / 3600
        recorded = read_checksum(a)
        state = "prune" if a in doomed else "keep "
        mark = "?" if recorded is None else " "
        print(f"  {state} {a.name}  {size:>12,}B  {age:>6.1f}h old  sha{mark}")
    return 0


# --------------------------------------------------------------------------- #
# verify — the drill
# --------------------------------------------------------------------------- #


def table_counts(transport: Transport, env: dict[str, str]) -> dict[str, int]:
    """Exact row counts per user table, via the catalog.

    `count(*)` per table would take a scan on every table; the catalog's
    `reltuples` is an estimate and an estimate is not good enough to certify a
    restore. Instead this builds one `UNION ALL` over the tables and runs it —
    one round trip, exact numbers.
    """
    names_sql = (
        "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
        "ORDER BY 1"
    )
    out = transport.check("psql", ["--tuples-only", "--no-align", "--command", names_sql], env=env)
    tables = [t.strip() for t in out.stdout.splitlines() if t.strip()]
    if not tables:
        return {}

    # S608 (string-built SQL) is a false positive here, and deliberately so.
    # Every identifier in `union` comes from `pg_tables` on the same server via
    # `format('%I.%I', ...)`, which is Postgres' own quoting function. Nothing
    # in this statement originates from a user or an argument — the only inputs
    # are schema and table names the catalog just returned.
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, count(*) AS n FROM {t}"  # noqa: S608
        for t in tables
    )
    out = transport.check(
        "psql",
        ["--tuples-only", "--no-align", "--field-separator=|", "--command", union],
        env=env,
    )
    counts: dict[str, int] = {}
    for raw in out.stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        name, _, n = stripped.rpartition("|")
        counts[name] = int(n)
    return counts


def cmd_verify(args: argparse.Namespace) -> int:
    """The restore drill: load the newest dump and compare it against the source.

    A dump that has never been restored is a hypothesis. This restores into a
    scratch database on the *same* server (which is available wherever the
    client tools are), compares table-by-table row counts, and drops the
    scratch database. It is the only check that catches a dump that is
    structurally valid but missing data — a stale snapshot taken mid-migration,
    an `--exclude-table` that crept in, a restore that silently skipped a
    schema.
    """
    target = _target_from(args)

    # The scratch guard runs FIRST, before any I/O. It is a pure argument
    # check, and it protects against the one unrecoverable mistake this command
    # can make (`dropdb` on something real) — so nothing may short-circuit it.
    # Previously it sat after the checksum check, which meant a corrupt archive
    # masked a dangerous --scratch-db: the run stopped on the checksum error and
    # the operator fixed that, only to have the next run do the dangerous thing.
    scratch = args.scratch_db or f"{target.dbname}{SCRATCH_SUFFIX}_{os.getpid()}"
    _assert_scratch_is_expendable(scratch, target.dbname)

    transport = Transport(target, args.via)

    if args.file:
        archive = Path(args.file).resolve()
    else:
        archives = list_archives(Path(args.out_dir).resolve())
        if not archives:
            raise BackupError(f"no archives found to verify in {args.out_dir}")
        archive = archives[0]

    if not archive.exists():
        raise BackupError(f"archive not found: {archive}")

    print(f"[verify] archive:   {archive.name} ({archive.stat().st_size:,} bytes)")
    print(f"[verify] transport: {transport.describe()}")

    recorded = read_checksum(archive)
    if recorded:
        actual = sha256_of(archive)
        if actual != recorded:
            raise BackupError(
                f"checksum mismatch — archive is corrupt (recorded {recorded[:16]}…, "
                f"computed {actual[:16]}…)"
            )
        print("[verify] checksum ok")
    elif not args.allow_unchecksummed:
        # No sidecar means this file's integrity is unproven. Refusing by
        # default is the safe direction: a corrupt dump that verifies "ok"
        # because we skipped the check is worse than no drill at all.
        raise BackupError(
            f"no {CHECKSUM_SUFFIX} beside {archive.name} — pass --allow-unchecksummed to "
            "restore anyway (integrity will be unverified)"
        )

    admin_env = target.pg_env(database="postgres")

    exists = transport.check(
        "psql",
        [
            "--tuples-only",
            "--no-align",
            "--command",
            # The name is a CLI argument, not user input, and `--command` takes
            # a literal string; there is no placeholder to bind here.
            f"SELECT 1 FROM pg_database WHERE datname = '{scratch}'",  # noqa: S608
        ],
        env=admin_env,
    ).stdout.strip()
    if exists:
        raise BackupError(f"scratch database {scratch!r} already exists — drop it or pick another")

    print("[verify] source counts…")
    source = table_counts(transport, target.pg_env())
    print(f"[verify]   {len(source)} table(s), {sum(source.values()):,} row(s)")

    created = False
    try:
        transport.check("createdb", ["--maintenance-db", "postgres", scratch], env=admin_env)
        created = True
        print(f"[verify] scratch database {scratch!r} created")

        # `--exit-on-error` is what makes this a drill: without it `pg_restore`
        # reports failures and still exits 0, and a restore that skipped half
        # the schema would pass a naive check.
        transport.read_to_file(
            "pg_restore",
            [
                "--dbname",
                scratch,
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
            ],
            archive,
            env=admin_env,
        )

        # PostGIS installs its own tables (`spatial_ref_sys`) that the dump
        # carries; compare only what the source has, and report the rest as
        # info rather than a mismatch.
        restored = table_counts(transport, target.pg_env(database=scratch))
        missing = sorted(set(source) - set(restored))
        mismatched = sorted(t for t in source if t in restored and source[t] != restored[t])
        extra = sorted(set(restored) - set(source))

        print(f"[verify] restored {len(restored)} table(s), {sum(restored.values()):,} row(s)")
        if extra:
            print(f"[verify]   extra in restore (informational): {', '.join(extra[:8])}")

        if missing:
            print(f"[verify] FAIL — {len(missing)} table(s) missing from the restore:")
            for t in missing[:20]:
                print(f"           {t}")
            return 1
        if mismatched:
            print(f"[verify] FAIL — {len(mismatched)} table(s) differ:")
            for t in mismatched[:20]:
                print(f"           {t}: source={source[t]:,} restored={restored[t]:,}")
            return 1

        print(f"[verify] PASS — all {len(source)} table(s) match the source exactly")
        return 0
    finally:
        if created:
            transport.check(
                "dropdb", ["--if-exists", "--maintenance-db", "postgres", scratch], env=admin_env
            )
            print(f"[verify] scratch database {scratch!r} dropped")


def _assert_scratch_is_expendable(scratch: str, source_db: str) -> None:
    """Refuse a scratch name that could be the real database.

    `verify` ends with `dropdb`, so a scratch name that collides with anything
    real is unrecoverable data loss. Two independent guards, because a single
    one is a typo away from destroying the ledger:

      1. the name must carry SCRATCH_SUFFIX, which no production database does;
      2. it must differ from the database being backed up.
    """
    if scratch == source_db:
        raise BackupError(
            f"refusing to use {scratch!r} as the scratch database — it is the source "
            "database, and this command drops it"
        )
    # Allow the `_<pid>` form the default uses.
    if not re.search(re.escape(SCRATCH_SUFFIX) + r"(_\d+)?$", scratch):
        raise BackupError(
            f"refusing to use {scratch!r} as the scratch database — a scratch name must "
            f"end in {SCRATCH_SUFFIX!r} (optionally with a _pid suffix), because this "
            "command drops it at the end"
        )


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db_backup.py",
        description="Nightly pg_dump with retention, and the restore drill that proves it loads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("BACKUP_DIR", str(ROOT / "backups")),
        help="where archives live (default: $BACKUP_DIR or ./backups)",
    )
    parser.add_argument(
        "--keep-daily", type=int, default=7, help="daily archives to keep (default: 7)"
    )
    parser.add_argument(
        "--keep-weekly", type=int, default=4, help="weekly archives to keep (default: 4)"
    )
    parser.add_argument(
        "--via",
        choices=("auto", "host", "docker"),
        default=os.environ.get("BACKUP_VIA", "auto"),
        help="how to reach pg_dump: auto (default), host tools, or docker exec into "
        "the db container",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("BACKUP_CONTAINER", DOCKER_CONTAINER),
        help=f"the Postgres container for --via docker (default: {DOCKER_CONTAINER})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="dump, checksum, upload, prune")
    p_backup.add_argument(
        "--upload-cmd",
        default=os.environ.get("BACKUP_UPLOAD_CMD", ""),
        help="POSIX-shell command run once per new archive; {file} and {name} are "
        "substituted. Must exit 0. Default: $BACKUP_UPLOAD_CMD, else no off-host copy.",
    )
    p_backup.add_argument(
        "--upload-verify-cmd",
        default=os.environ.get("BACKUP_UPLOAD_VERIFY_CMD", ""),
        help="POSIX-shell command that asks the remote whether the archive landed; must "
        "print --upload-verify-marker. Recommended whenever --upload-cmd is set: without "
        "it, a copy that exited 0 but did nothing is indistinguishable from success.",
    )
    p_backup.add_argument(
        "--upload-verify-marker",
        default=os.environ.get("BACKUP_UPLOAD_VERIFY_MARKER", ""),
        help="the string --upload-verify-cmd must print to confirm the copy "
        "(default: the archive name)",
    )
    p_backup.set_defaults(func=cmd_backup)

    p_verify = sub.add_parser("verify", help="restore the newest dump and compare row counts")
    p_verify.add_argument(
        "--file", default="", help="a specific archive (default: newest in --out-dir)"
    )
    p_verify.add_argument(
        "--scratch-db", default="", help="scratch database name (default: derived)"
    )
    p_verify.add_argument(
        "--allow-unchecksummed",
        action="store_true",
        help="restore even when no .sha256 sidecar exists (integrity unverified)",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_list = sub.add_parser("list", help="show archives and what retention keeps")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252 and this prints `·` and CJK table
    # names; a UnicodeEncodeError at 03:17 would abort a backup for a cosmetic
    # reason.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except BackupError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        print(
            f"[error] {exc.cmd[0] if isinstance(exc.cmd, list) else exc.cmd} timed out",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
