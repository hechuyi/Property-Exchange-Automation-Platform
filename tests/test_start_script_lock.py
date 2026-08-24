from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class StartScriptLockTests(unittest.TestCase):
    def _start_function_shell(self, body: str) -> str:
        script_path = shlex.quote(str(REPO_ROOT / "start.sh"))
        return (
            "set -euo pipefail\n"
            f"eval \"$(awk '/^trap cleanup EXIT INT TERM$/{{exit}}{{print}}' {script_path})\"\n"
            f"{body}\n"
        )

    def test_second_launcher_exits_before_touching_ports_or_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_home = Path(temp_dir) / "runtime"
            lock_dir = runtime_home / "run" / "launcher.lock"
            lock_dir.mkdir(parents=True)
            lock_dir.joinpath("pid").write_text(f"{os.getpid()}\n", encoding="ascii")
            lock_dir.joinpath("command").write_text("existing launcher\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PEAP_RUNTIME_HOME": str(runtime_home),
                    "PEAP_BACKEND_PORT": "0",
                    "PEAP_FRONTEND_PORT": "0",
                }
            )
            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "start.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already running", completed.stderr)
            self.assertTrue(lock_dir.exists())

    def test_future_mtime_empty_lock_does_not_wait_forever(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_home = Path(temp_dir) / "runtime"
            lock_dir = runtime_home / "run" / "launcher.lock"
            lock_dir.mkdir(parents=True)
            future = time.time() + 3600
            os.utime(lock_dir, (future, future))

            env = os.environ.copy()
            env.update(
                {
                    "PEAP_RUNTIME_HOME": str(runtime_home),
                    "PEAP_BACKEND_PORT": "0",
                    "PEAP_FRONTEND_PORT": "0",
                    "PEAP_PYTHON": "/usr/bin/false",
                }
            )
            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "start.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("already running", completed.stderr)
            self.assertFalse(lock_dir.exists())

    def test_truncated_dead_pending_is_recovered_across_launches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_home = Path(temp_dir) / "runtime"
            lock_root = runtime_home / "run"
            lock_root.mkdir(parents=True)
            pending = lock_root / ".launcher.lock.pending-99999999-corrupt"
            pending.write_text("99999999\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PEAP_RUNTIME_HOME": str(runtime_home),
                    "PEAP_BACKEND_PORT": "0",
                    "PEAP_FRONTEND_PORT": "0",
                    "PEAP_PYTHON": "/usr/bin/false",
                }
            )
            for _ in range(2):
                completed = subprocess.run(
                    ["bash", str(REPO_ROOT / "start.sh")],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertNotIn("initializing", completed.stderr)

            self.assertFalse(pending.exists())

    def test_truncated_live_pending_still_blocks_second_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_home = Path(temp_dir) / "runtime"
            lock_root = runtime_home / "run"
            lock_root.mkdir(parents=True)
            pending = lock_root / f".launcher.lock.pending-{os.getpid()}-corrupt"
            pending.write_text(f"{os.getpid()}\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PEAP_RUNTIME_HOME": str(runtime_home),
                    "PEAP_BACKEND_PORT": "0",
                    "PEAP_FRONTEND_PORT": "0",
                    "PEAP_PYTHON": "/usr/bin/false",
                }
            )
            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "start.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("initializing", completed.stderr)
            self.assertTrue(pending.exists())

    def test_process_signal_helpers_ignore_a_reused_pid_generation(self) -> None:
        shell = self._start_function_shell(
            "sleep 30 &\n"
            "pid=$!\n"
            "start_identity=\"$(launcher_process_start_identity \"$pid\")\"\n"
            "test -n \"$start_identity\"\n"
            "kill_tree \"$pid\" TERM \"different-$start_identity\"\n"
            "kill -0 \"$pid\"\n"
            "wait_for_process_exit \"$pid\" \"different-$start_identity\"\n"
            "kill -0 \"$pid\"\n"
            "kill_tree \"$pid\" TERM \"$start_identity\"\n"
            "wait_for_process_exit \"$pid\" \"$start_identity\"\n"
        )
        completed = subprocess.run(
            ["bash", "-c", shell],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_signal_escalation_waits_for_the_original_process_to_exit(self) -> None:
        shell = self._start_function_shell(
            "ready_dir=\"$(mktemp -d)\"\n"
            "python3 -c 'import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, lambda *_: None); pathlib.Path(sys.argv[1]).touch(); time.sleep(30)' \"$ready_dir/ready\" &\n"
            "pid=$!\n"
            "for _ in $(seq 1 100); do [ -f \"$ready_dir/ready\" ] && break; sleep 0.01; done\n"
            "test -f \"$ready_dir/ready\"\n"
            "rm -rf \"$ready_dir\"\n"
            "start_identity=\"$(launcher_process_start_identity \"$pid\")\"\n"
            "test -n \"$start_identity\"\n"
            "kill_tree \"$pid\" TERM \"$start_identity\"\n"
            "if wait_for_process_exit \"$pid\" \"$start_identity\"; then exit 1; fi\n"
            "kill_tree \"$pid\" KILL \"$start_identity\"\n"
            "wait_for_process_exit \"$pid\" \"$start_identity\"\n"
        )
        completed = subprocess.run(
            ["bash", "-c", shell],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_launcher_never_replaces_preexisting_port_processes(self) -> None:
        script = (REPO_ROOT / "start.sh").read_text(encoding="utf-8")

        self.assertNotIn("replace_existing_backend_if_owned", script)
        self.assertNotIn("replace_existing_frontend_if_owned", script)
        self.assertNotIn("stop_owned_port_processes", script)
        self.assertIn('require_free_port "$PEAP_BACKEND_PORT" "Backend"', script)
        self.assertIn('require_free_port "$PEAP_FRONTEND_PORT" "Frontend"', script)
        self.assertIn('wait_for_process_exit "$BACKEND_PID" "$BACKEND_START_IDENTITY"', script)
        self.assertIn('wait_for_process_exit "$FRONTEND_PID" "$FRONTEND_START_IDENTITY"', script)

    @unittest.skipUnless(shutil.which("lsof"), "requires lsof port ownership checks")
    def test_occupied_port_process_is_reported_but_not_stopped(self) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if server.poll() is not None:
                    self.fail("fixture server exited before the launcher check")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.01)
            else:
                self.fail("fixture server did not bind its port")

            with tempfile.TemporaryDirectory() as temp_dir:
                env = os.environ.copy()
                env.update(
                    {
                        "PEAP_RUNTIME_HOME": str(Path(temp_dir) / "runtime"),
                        "PEAP_BACKEND_PORT": str(port),
                        "PEAP_FRONTEND_PORT": str(port + 1),
                        "PEAP_PYTHON": "/usr/bin/false",
                    }
                )
                completed = subprocess.run(
                    ["bash", str(REPO_ROOT / "start.sh")],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already in use", completed.stderr)
            self.assertIsNone(server.poll(), "start.sh stopped a process it did not create")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
