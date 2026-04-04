import subprocess
import sys


def test_python_m_download_cli_executes_main():
    result = subprocess.run(
        [sys.executable, "-m", "peap.download_cli", "--list-tasks"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Registered downloader tasks:" in result.stdout
