"""Filesystem-backed process locks shared by the desktop entrypoints.

Acquisition uses an atomic directory creation together with a pending owner
record.  The pending record closes the small window between ``mkdir`` and the
owner metadata rename, so a slow initializer cannot be mistaken for a stale
empty lock.  Legacy ``pid``/``command`` locks remain readable for migration.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path


class ProcessLockError(RuntimeError):
    """Raised when another live process owns a process lock."""


def database_lock_path(database_path: str | os.PathLike[str]) -> Path:
    """Return a stable lock path for a SQLite database, including symlinks."""

    raw_path = os.path.expandvars(os.path.expanduser(str(database_path or "")).strip())
    if not raw_path:
        raise ValueError("database path is required for process locking")
    resolved_path = Path(os.path.realpath(raw_path))
    return Path(f"{resolved_path}.lock")


class ProcessLock:
    """An atomic, filesystem-backed process lock."""

    _PID_FILE = "pid"
    _COMMAND_FILE = "command"
    _OWNER_PREFIX = "owner-"
    _PENDING_MARKER = "pending-"

    def __init__(self, path: str | os.PathLike[str], *, label: str) -> None:
        raw_path = os.path.expandvars(os.path.expanduser(str(path or "")).strip())
        if not raw_path:
            raise ValueError("lock path is required")
        self.path = Path(raw_path).absolute()
        self.label = str(label or "process").strip() or "process"
        self._owned = False
        self._owner_file: Path | None = None
        self._owner_token = ""
        self._owner_start_identity = ""

    @property
    def pid_file(self) -> Path:
        return self.path / self._PID_FILE

    @property
    def command_file(self) -> Path:
        return self.path / self._COMMAND_FILE

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _process_start_identity(pid: int) -> str:
        """Return a PID-generation token (``ps lstart`` on macOS/Linux)."""

        if pid <= 0:
            return ""
        try:
            completed = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            identity = (completed.stdout or "").strip()
            if completed.returncode == 0 and identity:
                return identity
        except (OSError, subprocess.SubprocessError):
            pass

        # ``ps`` is part of macOS, but this fallback keeps the protocol useful
        # in minimal Unix test environments.  Linux starttime is monotonic for
        # a PID and changes whenever the PID is reused.
        proc_stat = Path(f"/proc/{pid}/stat")
        try:
            fields = proc_stat.read_text(encoding="ascii").split()
            if len(fields) > 21:
                return f"proc-start:{fields[21]}"
        except (OSError, ValueError):
            pass
        return ""

    @staticmethod
    def _read_owner_file(owner_file: Path) -> tuple[int | None, str]:
        """Read the compatibility ``(pid, command)`` view of an owner file."""

        try:
            lines = owner_file.read_text(encoding="utf-8").splitlines()
            pid = int(lines[0].strip())
        except (OSError, ValueError, IndexError):
            return None, ""
        if len(lines) >= 4:
            command = lines[3].strip()
        else:
            command = lines[1].strip() if len(lines) > 1 else ""
        return (pid if pid > 0 else None), command

    @staticmethod
    def _read_owner_metadata(
        owner_file: Path,
    ) -> tuple[int | None, str, str, str, bool]:
        """Return pid, start identity, token, command, and modern-format flag."""

        try:
            lines = owner_file.read_text(encoding="utf-8").splitlines()
            pid = int(lines[0].strip())
        except (OSError, ValueError, IndexError):
            return None, "", "", "", False
        if pid <= 0:
            return None, "", "", "", False
        if len(lines) >= 4:
            return pid, lines[1].strip(), lines[2].strip(), lines[3].strip(), True
        command = lines[1].strip() if len(lines) > 1 else ""
        return pid, "", "", command, False

    def _read_owner_details(
        self,
    ) -> tuple[Path | None, int | None, str, str, str, bool]:
        owner_files = sorted(self.path.glob(f"{self._OWNER_PREFIX}*"))
        if len(owner_files) == 1:
            owner_file = owner_files[0]
            pid, start_identity, token, command, modern = self._read_owner_metadata(owner_file)
            return owner_file, pid, start_identity, token, command, modern
        if owner_files:
            return None, None, "", "", "", False

        try:
            pid = int(self.pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
        try:
            command = self.command_file.read_text(encoding="utf-8").strip()
        except OSError:
            command = ""
        return None, (pid if pid > 0 else None), "", "", command, False

    def _read_owner(self) -> tuple[Path | None, int | None, str, bool]:
        owner_file, pid, _start_identity, _token, command, modern = self._read_owner_details()
        return owner_file, pid, command, not modern and owner_file is None

    def _pending_glob(self) -> str:
        return f".{self.path.name}.{self._PENDING_MARKER}*"

    def _pending_files(self) -> list[Path]:
        return sorted(self.path.parent.glob(self._pending_glob()))

    def _pending_file_pid(self, pending_file: Path) -> int | None:
        prefix = f".{self.path.name}.{self._PENDING_MARKER}"
        name = pending_file.name
        if not name.startswith(prefix):
            return None
        raw_pid = name[len(prefix) :].split("-", 1)[0]
        try:
            pid = int(raw_pid)
        except ValueError:
            return None
        return pid if pid > 0 else None

    @staticmethod
    def _read_pending_metadata(pending_file: Path) -> tuple[int | None, str, str]:
        try:
            lines = pending_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None, "", ""
        try:
            pid = int(lines[0].strip())
        except (ValueError, IndexError):
            pid = None
        if pid is not None and pid <= 0:
            pid = None
        start_identity = lines[1].strip() if len(lines) >= 2 else ""
        token = lines[2].strip() if len(lines) >= 3 else ""
        return pid, start_identity, token

    def _pending_status(self) -> tuple[list[Path], list[Path]]:
        """Return active and stale pending records, without deleting anything."""

        active: list[Path] = []
        stale: list[Path] = []
        for pending_file in self._pending_files():
            metadata_pid, start_identity, _token = self._read_pending_metadata(pending_file)
            filename_pid = self._pending_file_pid(pending_file)
            if metadata_pid is None and filename_pid is None:
                active.append(pending_file)
                continue
            metadata_live = metadata_pid is not None and self._owner_is_live(metadata_pid, start_identity)
            filename_live = filename_pid is not None and self._owner_is_live(
                filename_pid,
                start_identity if filename_pid == metadata_pid else "",
            )
            if metadata_live or filename_live:
                active.append(pending_file)
            else:
                stale.append(pending_file)
        return active, stale

    def _owner_is_live(self, pid: int | None, start_identity: str) -> bool:
        if pid is None or not self._pid_is_alive(pid):
            return False
        if not start_identity:
            # Legacy owners have no generation token; retain PID-only
            # compatibility rather than deleting a potentially live lock.
            return True
        observed = self._process_start_identity(pid)
        if not observed:
            return True
        return observed == start_identity

    def _lock_is_initializing(self) -> bool:
        """Compatibility helper retained for callers; no grace timeout is used."""

        active, _stale = self._pending_status()
        return bool(active)

    @staticmethod
    def _unlink_if_same(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _remove_stale_lock(self, *, owner_file: Path | None, legacy: bool) -> None:
        """Remove only an unchanged, known-format stale lock directory."""

        try:
            children = list(self.path.iterdir())
        except FileNotFoundError:
            return
        allowed_names: set[str]
        if owner_file is not None:
            allowed_names = {owner_file.name}
        elif legacy:
            allowed_names = {self._PID_FILE, self._COMMAND_FILE}
        else:
            allowed_names = set()
        if any(child.name not in allowed_names for child in children):
            raise ProcessLockError(
                f"{self.label} lock exists but is not removable: {self.path}"
            )
        if owner_file is not None:
            self._unlink_if_same(owner_file)
        elif legacy:
            for child in (self.pid_file, self.command_file):
                self._unlink_if_same(child)
        try:
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProcessLockError(
                f"{self.label} lock exists but is not removable: {self.path}"
            ) from exc

    def _write_exclusive(self, path: Path, content: str) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _create_pending(self, *, pid: int, start_identity: str, token: str, command: str) -> Path:
        pending_file = self.path.parent / f".{self.path.name}.{self._PENDING_MARKER}{pid}-{token}"
        temp_file = self.path.parent / f".{self.path.name}.write-pending-{pid}-{token}"
        try:
            self._write_exclusive(
                temp_file,
                f"{pid}\n{start_identity}\n{token}\n{command}\n",
            )
            os.replace(temp_file, pending_file)
        except Exception:
            self._unlink_if_same(temp_file)
            raise
        return pending_file

    def _inspect_existing(self) -> None:
        active_pending, stale_pending = self._pending_status()
        if active_pending:
            raise ProcessLockError(
                f"{self.label} is initializing; another process owns the acquisition"
            ) from None
        for pending_file in stale_pending:
            self._unlink_if_same(pending_file)

        owner_file, pid, start_identity, _token, command, modern = self._read_owner_details()
        if pid is not None and self._owner_is_live(pid, start_identity):
            detail = f" (PID {pid}{': ' + command if command else ''})"
            raise ProcessLockError(f"{self.label} is already running{detail}") from None
        legacy = owner_file is None and not modern
        self._remove_stale_lock(owner_file=owner_file, legacy=legacy)

    def acquire(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        start_identity = self._process_start_identity(pid)
        token = secrets.token_hex(16)
        command = " ".join(sys.argv).replace("\n", " ").strip()

        while True:
            active_pending, stale_pending = self._pending_status()
            if active_pending:
                raise ProcessLockError(
                    f"{self.label} is initializing; another process owns the acquisition"
                ) from None
            for pending_file in stale_pending:
                self._unlink_if_same(pending_file)
            pending_file: Path | None = None
            try:
                pending_file = self._create_pending(
                    pid=pid,
                    start_identity=start_identity,
                    token=token,
                    command=command,
                )
                self.path.mkdir(mode=0o700)
            except FileExistsError:
                if pending_file is not None:
                    self._unlink_if_same(pending_file)
                self._inspect_existing()
                continue
            except OSError as exc:
                if pending_file is not None:
                    self._unlink_if_same(pending_file)
                raise ProcessLockError(f"cannot create {self.label} lock: {self.path}") from exc

            owner_file = self.path / f"{self._OWNER_PREFIX}{pid}-{token}"
            try:
                os.replace(pending_file, owner_file)
            except OSError as exc:
                self._remove_stale_lock(owner_file=None, legacy=False)
                raise ProcessLockError(f"cannot initialize {self.label} lock: {self.path}") from exc
            self._owner_file = owner_file
            self._owner_token = token
            self._owner_start_identity = start_identity
            self._owned = True
            return self

    def release(self) -> None:
        if not self._owned:
            return
        self._owned = False
        owner_file = self._owner_file
        self._owner_file = None
        if owner_file is None:
            return
        try:
            pid, start_identity, token, _command, modern = self._read_owner_metadata(owner_file)
            if (
                not modern
                or pid != os.getpid()
                or token != self._owner_token
                or (
                    self._owner_start_identity
                    and start_identity != self._owner_start_identity
                )
            ):
                return
            self._unlink_if_same(owner_file)
            self.path.rmdir()
        except OSError:
            # Leaving a stale lock is safe; the next acquisition validates its
            # owner generation before attempting compare-and-remove cleanup.
            return

    def __enter__(self) -> "ProcessLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()
