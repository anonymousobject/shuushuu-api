"""Tests for docker/nginx/cert-reload.sh.

The script runs inside the nginx container and reloads nginx when certbot has
renewed a certificate. These tests run the real script against a temp
directory of "certificates" with a stand-in `nginx` executable on PATH that
records how it was called, so what is under test is the script's own decision
of when to reload — not nginx.
"""

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "nginx" / "cert-reload.sh"

# Short enough that a test sees several polls, long enough to edit files between them.
INTERVAL_SECONDS = 0.2


class Watcher:
    """The running script plus the files that steer and observe it."""

    def __init__(self, tmp_path: Path, live_dir: Path) -> None:
        self.live_dir = live_dir
        self.calls_file = tmp_path / "nginx-calls"
        self.config_test_fails = tmp_path / "nginx-config-test-fails"
        self.process: subprocess.Popen[str] | None = None

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_nginx = bin_dir / "nginx"
        # Records its arguments; `nginx -t` fails while the marker file exists.
        fake_nginx.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{self.calls_file}"\n'
            f'if [ "$1" = "-t" ] && [ -e "{self.config_test_fails}" ]; then exit 1; fi\n'
        )
        fake_nginx.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CERT_RELOAD_GLOB": str(live_dir / "*" / "fullchain.pem"),
            "CERT_RELOAD_INTERVAL": str(INTERVAL_SECONDS),
        }

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["sh", str(SCRIPT)],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Let it take its baseline fingerprint before a test changes anything.
        time.sleep(INTERVAL_SECONDS)

    def stop(self) -> tuple[str, str]:
        assert self.process is not None
        self.process.terminate()
        return self.process.communicate(timeout=5)

    def write_cert(self, name: str, content: str) -> None:
        cert_dir = self.live_dir / name
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "fullchain.pem").write_text(content)

    def nginx_calls(self) -> list[str]:
        if not self.calls_file.exists():
            return []
        return self.calls_file.read_text().splitlines()

    def wait_for_calls(self, expected: list[str]) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.nginx_calls() == expected:
                return
            time.sleep(INTERVAL_SECONDS / 4)
        assert self.nginx_calls() == expected

    def let_polls_pass(self, count: int = 4) -> None:
        time.sleep(INTERVAL_SECONDS * count)


@pytest.fixture
def watcher(tmp_path: Path) -> Iterator[Watcher]:
    watcher = Watcher(tmp_path, tmp_path / "live")
    yield watcher
    if watcher.process is not None and watcher.process.poll() is None:
        watcher.process.kill()
        watcher.process.communicate()


@pytest.mark.unit
def test_unchanged_certificates_never_reload(watcher: Watcher) -> None:
    watcher.write_cert("example.net", "cert v1")
    watcher.start()

    watcher.let_polls_pass()

    assert watcher.process is not None
    assert watcher.process.poll() is None
    assert watcher.nginx_calls() == []


@pytest.mark.unit
def test_renewed_certificate_reloads_once(watcher: Watcher) -> None:
    watcher.write_cert("example.net", "cert v1")
    watcher.write_cert("wiki.example.net", "wiki cert v1")
    watcher.start()

    watcher.write_cert("wiki.example.net", "wiki cert v2")

    watcher.wait_for_calls(["-t", "-s reload"])
    # The new certificate becomes the baseline: no reload on every later poll.
    watcher.let_polls_pass()
    assert watcher.nginx_calls() == ["-t", "-s reload"]
    stdout, _ = watcher.stop()
    assert "certificates changed, reloading nginx" in stdout


@pytest.mark.unit
def test_failed_config_test_skips_reload_and_retries(watcher: Watcher) -> None:
    """A renewal caught half-written, or a broken config, must not be forgotten."""
    watcher.write_cert("example.net", "cert v1")
    watcher.start()

    watcher.config_test_fails.touch()
    watcher.write_cert("example.net", "cert v2")
    watcher.wait_for_calls(["-t"])
    watcher.let_polls_pass()
    calls_while_failing = watcher.nginx_calls()
    assert len(calls_while_failing) > 1
    assert set(calls_while_failing) == {"-t"}

    watcher.config_test_fails.unlink()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "-s reload" not in watcher.nginx_calls():
        time.sleep(INTERVAL_SECONDS / 4)
    assert watcher.nginx_calls()[-2:] == ["-t", "-s reload"]
    _, stderr = watcher.stop()
    assert "reload failed, will retry" in stderr


@pytest.mark.unit
def test_no_certificates_is_a_quiet_no_op(watcher: Watcher) -> None:
    """Dev and test stacks share the entrypoint but mount no certificates."""
    watcher.start()

    watcher.let_polls_pass()

    assert watcher.process is not None
    assert watcher.process.poll() is None
    assert watcher.nginx_calls() == []
    stdout, stderr = watcher.stop()
    assert stdout == ""
    assert stderr == ""
