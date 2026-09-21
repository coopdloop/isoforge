"""Local process supervision.

The CLI owns the lifecycle of two child processes: the Go daemon (gateway + store)
and the Python service (agent + render). Both bind to localhost on ports we choose,
and both must be torn down cleanly even when the user hits Ctrl-C mid-turn.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import httpx


class StartupError(RuntimeError):
    """A managed service failed to become healthy."""


def free_port() -> int:
    """Ask the OS for an unused port.

    There is a benign race between closing this socket and the child binding it, but
    it is far better than hardcoding ports and colliding with another CLI instance.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def find_binary(name: str) -> Path | None:
    """Locate a bundled or developer-built Go binary.

    Wheels ship the binary inside the package; a development checkout has it in the
    repo root after `make build`. PATH is the last resort.
    """
    packaged = Path(__file__).parent / "bin" / name
    if packaged.is_file():
        return packaged

    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (repo_root / name, repo_root / "bin" / name):
        if candidate.is_file():
            return candidate

    if found := shutil.which(name):
        return Path(found)
    return None


@dataclass
class ManagedProcess:
    """One supervised child process."""

    name: str
    command: list[str]
    port: int
    health_url: str
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    process: subprocess.Popen | None = None
    log_file: IO[bytes] | None = None
    log_path: Path | None = None

    def start(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"{self.name}.log"
        self.log_file = self.log_path.open("wb")

        environment = {**os.environ, **self.env}
        self.process = subprocess.Popen(
            self.command,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=str(self.cwd) if self.cwd else None,
            # Own process group so Ctrl-C in the terminal reaches the CLI only; the
            # CLI then shuts children down in a controlled order.
            start_new_session=True,
        )

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def tail(self, lines: int = 25) -> str:
        """Recent output, used to explain a failed startup."""
        if not self.log_path or not self.log_path.is_file():
            return ""
        try:
            content = self.log_path.read_text(errors="replace").strip().splitlines()
        except OSError:
            return ""
        return "\n".join(content[-lines:])

    def stop(self, timeout: float = 5.0) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=2)
        if self.log_file:
            with contextlib.suppress(OSError):
                self.log_file.close()


class Supervisor:
    """Starts, health-gates and tears down the local service stack."""

    def __init__(self, data_dir: Path, *, log_dir: Path | None = None, quiet: bool = True):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.log_dir = Path(log_dir) if log_dir else self.data_dir / "logs"
        self.quiet = quiet
        self.processes: list[ManagedProcess] = []
        self.gateway_port: int | None = None
        self.python_port: int | None = None
        self._stopped = False

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway_port}"

    def start(self, *, gateway_port: int | None = None, serve_web: bool = True) -> str:
        """Launch both services and wait until they are healthy."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.python_port = free_port()
        if gateway_port is None:
            self.gateway_port = free_port()
        else:
            if not port_is_free(gateway_port):
                raise StartupError(
                    f"port {gateway_port} is already in use; another isoforge session "
                    "may be running. Pass --port to choose a different one."
                )
            self.gateway_port = gateway_port

        atexit.register(self.stop)

        self._start_python()
        self._start_gateway(serve_web=serve_web)

        # The Python service must be up first: the gateway health-checks it, and a
        # failure here should name the Python service rather than the gateway.
        self._await_health(self.processes[0], timeout=45)
        self._await_health(self.processes[1], timeout=20)
        return self.gateway_url

    def _start_python(self) -> None:
        env = {
            "AGENT_ORCHESTRATOR_PORT": str(self.python_port),
            "EXPORT_OUTPUT_DIR": str(self.data_dir / "exports"),
            "LOG_LEVEL": "warning" if self.quiet else "info",
        }
        # Prefer the installed console script; fall back to the module for a source
        # checkout where the package is on PYTHONPATH but not installed.
        if entry := shutil.which("isoforge-py"):
            command = [entry]
        else:
            command = [sys.executable, "-m", "isoforge_py.api.app"]

        services_dir = Path(__file__).resolve().parents[2] / "services"
        if services_dir.is_dir():
            env["PYTHONPATH"] = str(services_dir) + os.pathsep + env.get("PYTHONPATH", "")

        self.processes.append(
            ManagedProcess(
                name="isoforge-py",
                command=command,
                port=self.python_port,
                health_url=f"http://127.0.0.1:{self.python_port}/health",
                env=env,
            )
        )
        self.processes[-1].start(self.log_dir)

    def _start_gateway(self, *, serve_web: bool) -> None:
        binary = find_binary("isoforged")
        if binary is None:
            raise StartupError(
                "could not find the 'isoforged' binary.\n"
                "In a source checkout run:  make build\n"
                "Otherwise reinstall isoforge so the bundled binary is restored."
            )

        python_base = f"http://127.0.0.1:{self.python_port}"
        command = [
            str(binary),
            "-port", str(self.gateway_port),
            "-agent-url", python_base,
            "-render-url", python_base,
            "-db", str(self.data_dir / "isoforge.db"),
            "-scenes", str(self.data_dir / "scenes"),
            "-log-level", "warn" if self.quiet else "info",
        ]
        if not serve_web:
            command.append("-no-web")

        self.processes.append(
            ManagedProcess(
                name="isoforged",
                command=command,
                port=self.gateway_port,
                health_url=f"http://127.0.0.1:{self.gateway_port}/health",
            )
        )
        self.processes[-1].start(self.log_dir)

    def _await_health(self, proc: ManagedProcess, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""

        while time.monotonic() < deadline:
            if not proc.running:
                raise StartupError(
                    f"{proc.name} exited during startup (code {proc.process.returncode}).\n"
                    f"Recent output:\n{proc.tail()}"
                )
            try:
                response = httpx.get(proc.health_url, timeout=1.5)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.RequestError as exc:
                last_error = str(exc)
            time.sleep(0.15)

        raise StartupError(
            f"{proc.name} did not become healthy within {timeout:.0f}s ({last_error}).\n"
            f"Recent output:\n{proc.tail()}"
        )

    def stop(self) -> None:
        """Shut everything down, gateway first so in-flight requests fail fast."""
        if self._stopped:
            return
        self._stopped = True
        for proc in reversed(self.processes):
            proc.stop()

    def __enter__(self) -> "Supervisor":
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


def probe_running_gateway(port: int) -> str | None:
    """Return the base URL if a healthy gateway is already listening.

    Lets `isoforge history` and friends attach to a running `isoforge chat` session
    instead of starting a second, conflicting stack.
    """
    url = f"http://127.0.0.1:{port}"
    try:
        response = httpx.get(f"{url}/health", timeout=1.0)
        if response.status_code == 200 and response.json().get("isodsl_version"):
            return url
    except (httpx.RequestError, json.JSONDecodeError, ValueError):
        return None
    return None
