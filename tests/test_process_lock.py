from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from desktop_backend.process_lock import ProcessLock, ProcessLockError, database_lock_path


class ProcessLockTests(unittest.TestCase):
    def test_database_lock_path_uses_realpath(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "data" / "streaming.sqlite3"
            target.parent.mkdir()
            alias = root / "alias.sqlite3"
            alias.symlink_to(target)

            self.assertEqual(
                database_lock_path(alias),
                Path(f"{target.resolve()}.lock"),
            )

    def test_second_live_owner_is_rejected_and_release_removes_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            owner = ProcessLock(lock_path, label="test backend").acquire()
            self.addCleanup(owner.release)

            with self.assertRaisesRegex(ProcessLockError, "already running"):
                ProcessLock(lock_path, label="test backend").acquire()

            owner_files = list(lock_path.glob("owner-*"))
            self.assertEqual(len(owner_files), 1)
            self.assertEqual(
                owner_files[0].read_text(encoding="utf-8").splitlines()[0],
                str(os.getpid()),
            )
            owner.release()
            self.assertFalse(lock_path.exists())

    def test_dead_pid_lock_is_recovered_without_recursive_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.mkdir(parents=True)
            lock_path.joinpath("pid").write_text("99999999\n", encoding="ascii")
            lock_path.joinpath("command").write_text("stale owner\n", encoding="utf-8")

            owner = ProcessLock(lock_path, label="test backend").acquire()
            try:
                owner_files = list(lock_path.glob("owner-*"))
                self.assertEqual(len(owner_files), 1)
                self.assertEqual(
                    owner_files[0].read_text(encoding="utf-8").splitlines()[0],
                    str(os.getpid()),
                )
            finally:
                owner.release()

    def test_stale_cleaner_cannot_remove_replacement_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.mkdir(parents=True)
            stale_owner = lock_path / "owner-99999999-stale"
            stale_owner.write_text("99999999\nstale owner\n", encoding="utf-8")
            delayed_cleaner = ProcessLock(lock_path, label="delayed cleaner")
            observed_owner, _pid, _command, legacy = delayed_cleaner._read_owner()

            replacement = ProcessLock(lock_path, label="replacement").acquire()
            try:
                with self.assertRaisesRegex(ProcessLockError, "not removable"):
                    delayed_cleaner._remove_stale_lock(
                        owner_file=observed_owner,
                        legacy=legacy,
                    )
                self.assertTrue(replacement._owner_file.is_file())
            finally:
                replacement.release()

    def test_future_mtime_empty_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.mkdir(parents=True)
            future = time.time() + 3600
            os.utime(lock_path, (future, future))

            owner = ProcessLock(lock_path, label="test backend").acquire()
            owner.release()

            self.assertFalse(lock_path.exists())

    def test_stale_lock_with_unknown_file_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.mkdir(parents=True)
            lock_path.joinpath("pid").write_text("99999999\n", encoding="ascii")
            marker = lock_path / "unexpected"
            marker.write_text("preserve\n", encoding="utf-8")

            with self.assertRaisesRegex(ProcessLockError, "not removable"):
                ProcessLock(lock_path, label="test backend").acquire()

            self.assertTrue(marker.exists())

    def test_active_pending_owner_cannot_be_stolen_before_owner_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.parent.mkdir(parents=True)
            token = "pending-token"
            start_identity = ProcessLock._process_start_identity(os.getpid())
            pending = lock_path.parent / f".{lock_path.name}.pending-{os.getpid()}-{token}"
            pending.write_text(
                f"{os.getpid()}\n{start_identity}\n{token}\nslow initializer\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ProcessLockError, "initializing"):
                ProcessLock(lock_path, label="test backend").acquire()

            self.assertTrue(pending.exists())

    def test_truncated_pending_with_dead_owner_is_recovered_across_acquisitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.parent.mkdir(parents=True)
            pending = lock_path.parent / f".{lock_path.name}.pending-99999999-corrupt"
            pending.write_text("99999999\n", encoding="utf-8")

            first_owner = ProcessLock(lock_path, label="test backend").acquire()
            first_owner.release()
            self.assertFalse(pending.exists())

            second_owner = ProcessLock(lock_path, label="test backend").acquire()
            second_owner.release()
            self.assertFalse(lock_path.exists())

    def test_truncated_pending_with_live_owner_remains_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.parent.mkdir(parents=True)
            pending = lock_path.parent / f".{lock_path.name}.pending-{os.getpid()}-corrupt"
            pending.write_text(f"{os.getpid()}\n", encoding="utf-8")

            with self.assertRaisesRegex(ProcessLockError, "initializing"):
                ProcessLock(lock_path, label="test backend").acquire()

            self.assertTrue(pending.exists())

    def test_pid_reuse_with_different_start_identity_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run" / "backend.lock"
            lock_path.mkdir(parents=True)
            owner_file = lock_path / "owner-{}-old".format(os.getpid())
            owner_file.write_text(
                f"{os.getpid()}\nold-process-start\nold-token\nold owner\n",
                encoding="utf-8",
            )

            owner = ProcessLock(lock_path, label="test backend").acquire()
            try:
                self.assertNotEqual(owner._owner_token, "old-token")
                self.assertTrue(owner._owner_file.is_file())
            finally:
                owner.release()


if __name__ == "__main__":
    unittest.main()
