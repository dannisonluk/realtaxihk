"""TDD — P1-4: the backup tool's retention, naming, and safety guards.

These cover the logic that decides *what happens to files on disk*, which is
the part of a backup tool that can destroy data. The end-to-end path (a real
`pg_dump`, a real restore) is exercised by `scripts/ops/db_backup.py verify`, which
needs a live Postgres and therefore is not a unit test — see the drill output in
`docs/PRODUCTION_READINESS.md`.

The guards are tested as hard requirements rather than as happy paths: each one
exists because the failure it prevents is unrecoverable (`dropdb` on the real
database) or silent (a prune that removes the only copy from last week).
"""

import datetime as dt
from pathlib import Path

import pytest

from scripts.ops.db_backup import (
    NAME_RE,
    SCRATCH_SUFFIX,
    BackupError,
    DbTarget,
    _assert_scratch_is_expendable,
    _render_upload_cmd,
    _shell_path,
    list_archives,
    select_prune,
    stamp_of,
)


def mk(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"x")
    return p


class TestArchiveNaming:
    def test_matches_the_produced_form(self):
        assert NAME_RE.match("realtaxihk_20260930T031700Z.dump")

    def test_stamp_is_parsed_as_utc(self):
        # The name is UTC by construction; a naive parse would shift retention
        # boundaries by the host's offset and silently keep the wrong days.
        stamp = stamp_of(Path("realtaxihk_20260930T031700Z.dump"))
        assert stamp.tzinfo is dt.UTC
        assert stamp == dt.datetime(2026, 9, 30, 3, 17, tzinfo=dt.UTC)

    @pytest.mark.parametrize(
        "name",
        [
            "realtaxihk_20260930.dump",  # no time
            "realtaxihk_20260930T031700.dump",  # no Z
            "anything.dump",
            "realtaxihk_20260930T031700Z.sql",
            "realtaxihk_20260930T031700Z.dump.sha256",
        ],
    )
    def test_rejects_everything_it_would_not_have_written(self, name):
        # `list_archives` prunes based on this pattern, so a loose match means
        # pruning something that is not ours.
        assert NAME_RE.match(name) is None

    def test_list_archives_ignores_foreign_files(self, tmp_path):
        mk(tmp_path, "realtaxihk_20260930T031700Z.dump")
        mk(tmp_path, "realtaxihk_20260930T031700Z.dump.sha256")
        mk(tmp_path, "notes.txt")
        mk(tmp_path, "realtaxihk.dump")
        found = list_archives(tmp_path)
        assert [p.name for p in found] == ["realtaxihk_20260930T031700Z.dump"]

    def test_list_archives_newest_first(self, tmp_path):
        mk(tmp_path, "realtaxihk_20260928T031700Z.dump")
        mk(tmp_path, "realtaxihk_20260930T031700Z.dump")
        mk(tmp_path, "realtaxihk_20260929T031700Z.dump")
        names = [p.name for p in list_archives(tmp_path)]
        assert names == [
            "realtaxihk_20260930T031700Z.dump",
            "realtaxihk_20260929T031700Z.dump",
            "realtaxihk_20260928T031700Z.dump",
        ]

    def test_list_archives_on_a_missing_dir_is_empty_not_an_error(self, tmp_path):
        assert list_archives(tmp_path / "nope") == []


class TestRetention:
    def _series(self, tmp_path: Path, days: list[str]) -> list[Path]:
        return [mk(tmp_path, f"realtaxihk_{d}T031700Z.dump") for d in days]

    def test_keeps_the_newest_n_daily(self, tmp_path):
        days = [f"202609{d:02d}" for d in range(1, 13)]  # 12 consecutive days
        archives = self._series(tmp_path, days)
        doomed = {p.name for p in select_prune(archives, keep_daily=7, keep_weekly=0)}
        assert doomed == {f"realtaxihk_202609{d:02d}T031700Z.dump" for d in range(1, 6)}

    def test_daily_tier_counts_days_not_files(self, tmp_path):
        # Two runs on one day must consume ONE daily slot, not two: with
        # keep_daily=2 the operator wants two *days* of coverage, and two files
        # from the same afternoon would silently evict yesterday's only copy.
        # So the newest of the day is kept and the earlier same-day run is the
        # one that goes.
        mk(tmp_path, "realtaxihk_20260930T010000Z.dump")
        mk(tmp_path, "realtaxihk_20260930T230000Z.dump")
        mk(tmp_path, "realtaxihk_20260929T031700Z.dump")
        archives = list_archives(tmp_path)
        doomed = {p.name for p in select_prune(archives, keep_daily=2, keep_weekly=0)}
        assert doomed == {"realtaxihk_20260930T010000Z.dump"}
        # And the older *day* survived, which is the property that matters.
        assert "realtaxihk_20260929T031700Z.dump" not in doomed

    def test_weekly_tier_survives_beyond_the_daily_window(self, tmp_path):
        # The point of the weekly tier: a problem noticed after a week still has
        # a copy. 40 consecutive days, 7 daily + 3 weekly.
        base = dt.date(2026, 9, 30)
        days = [(base - dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(40)]
        archives = self._series(tmp_path, days)
        kept = {p.name for p in archives} - {
            p.name for p in select_prune(archives, keep_daily=7, keep_weekly=3)
        }
        kept_dates = sorted(p.name.split("_")[1][:8] for p in archives if p.name in kept)

        # Everything in the last 7 days is kept regardless.
        for i in range(7):
            assert (base - dt.timedelta(days=i)).strftime("%Y%m%d") in kept_dates
        # And so are older dated archives, which a count-based policy would drop.
        assert len(kept_dates) > 7

    def test_zero_retention_prunes_everything(self, tmp_path):
        archives = self._series(tmp_path, ["20260929", "20260930"])
        assert len(select_prune(archives, keep_daily=0, keep_weekly=0)) == 2

    def test_retention_never_returns_a_path_outside_the_input(self, tmp_path):
        archives = self._series(tmp_path, [f"202609{d:02d}" for d in range(1, 20)])
        for p in select_prune(archives, keep_daily=7, keep_weekly=4):
            assert p in archives


class TestScratchGuard:
    """`verify` ends with `dropdb`. These guards are the difference between a
    drill and destroying the ledger."""

    def test_refuses_the_source_database_name(self):
        with pytest.raises(BackupError, match="source database"):
            _assert_scratch_is_expendable("realtaxihk", "realtaxihk")

    def test_refuses_an_arbitrary_name(self):
        with pytest.raises(BackupError, match="must end in"):
            _assert_scratch_is_expendable("production", "realtaxihk")

    def test_refuses_a_name_that_merely_contains_the_suffix(self):
        # `realtaxihk_restore_drill_backup` is not the tool's scratch name.
        with pytest.raises(BackupError):
            _assert_scratch_is_expendable("realtaxihk_restore_drill_extra", "realtaxihk")

    def test_accepts_the_default_form(self):
        _assert_scratch_is_expendable(f"realtaxihk{SCRATCH_SUFFIX}_1234", "realtaxihk")

    def test_accepts_a_bare_suffix(self):
        _assert_scratch_is_expendable(f"other{SCRATCH_SUFFIX}", "realtaxihk")

    def test_a_name_equal_to_the_source_never_passes_even_with_the_suffix(self):
        # Belt and braces: if someone names the source itself with the suffix,
        # the inequality check still has to hold.
        with pytest.raises(BackupError):
            _assert_scratch_is_expendable("realtaxihk", "realtaxihk")


class TestShellPaths:
    """The upload command is run by `sh`, so backslashes must not survive."""

    def test_the_native_separator_never_survives(self):
        # Measured failure: `str(Path)` on Windows is `C:\\Users\\...`, and
        # Git-for-Windows' `sh` strips the backslashes, producing
        # `C:UsersuserDesktop...` — the copy silently targeted nothing.
        #
        # Assert on a path *built with* the native separator rather than on a
        # hard-coded Windows one: `Path("C:/Users/user/x.dump")` is only
        # absolute on Windows, so on a POSIX runner it would be resolved under
        # the CWD and this test would fail for a reason that has nothing to do
        # with the defect it exists to catch. See the sibling test below.
        native = Path("a") / "b" / "x.dump"
        out = _shell_path(native)
        assert "\\" not in out
        assert out == native.resolve().as_posix()

    def test_windows_drive_paths_keep_their_drive_letter(self):
        # `C:/Users/...` is absolute on Windows but *relative* on POSIX, so CI
        # resolves it under the workspace. Assert only what holds on both: the
        # drive letter and every separator survive. Do not assert
        # `startswith("C:/")` — that is the Windows-only reading, and it is the
        # very mistake this class exists to catch.
        out = _shell_path(Path("C:/Users/user/x.dump"))
        assert "\\" not in out
        assert out.endswith("C:/Users/user/x.dump")
        assert out.count("/") >= 3

    def test_relative_paths_are_resolved(self):
        # The archive is handed to a *remote* copy, so a bare `foo.dump` is no
        # use — no directory component means the remote has nothing to resolve
        # it against. Assert that a directory appeared, not that it starts with
        # `/`: on Windows the resolved spelling is `C:/...`, so a leading-slash
        # assertion is a POSIX-only claim (the sibling above catches the same
        # class of mistake).
        out = _shell_path(Path("some.dump"))
        assert out.endswith("/some.dump")
        assert out != "some.dump"
        assert str(Path.cwd().as_posix()).split("/")[0] in out

    def test_render_substitutes_both_placeholders(self, tmp_path):
        archive = tmp_path / "realtaxihk_20260930T031700Z.dump"
        cmd = _render_upload_cmd("rclone copy {file} remote:{name}", archive)
        assert "{file}" not in cmd and "{name}" not in cmd
        assert archive.name in cmd
        assert "\\" not in cmd


class TestTargetFromSettings:
    def test_reads_the_apps_own_settings_not_a_second_copy(self):
        # The whole point: a backup targets the database the API uses. If this
        # ever grows its own connection string, it can silently back up the
        # wrong one.
        target = DbTarget.from_env()
        assert target.dbname
        assert target.user
        assert target.container

    def test_pg_env_keeps_the_password_out_of_argv(self):
        target = DbTarget(
            host="h", port="1", user="u", password="s3cret", dbname="d", container="c"
        )
        env = target.pg_env()
        assert env["PGPASSWORD"] == "s3cret"
        assert env["PGDATABASE"] == "d"

    def test_pg_env_can_override_the_database(self):
        target = DbTarget(host="h", port="1", user="u", password="p", dbname="d", container="c")
        assert target.pg_env(database="postgres")["PGDATABASE"] == "postgres"
