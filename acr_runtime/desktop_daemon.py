from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DaemonState:
    instance_id: str
    pid: int
    host: str
    port: int
    started_at: str
    database: str

    def __post_init__(self) -> None:
        uuid.UUID(self.instance_id)
        if self.pid <= 0:
            raise ValueError("daemon pid must be positive")
        ipaddress.ip_address(self.host)
        if not 1 <= self.port <= 65_535:
            raise ValueError("daemon port must be 1..65535")
        parsed = datetime.fromisoformat(self.started_at)
        if parsed.tzinfo is None:
            raise ValueError("daemon started_at requires a timezone")
        if not self.database:
            raise ValueError("daemon database must be non-empty")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "started_at": self.started_at,
            "database": self.database,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "DaemonState":
        fields = {
            "schema_version", "instance_id", "pid", "host", "port",
            "started_at", "database",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("daemon state has an invalid shape")
        if (
            not isinstance(payload["schema_version"], int)
            or isinstance(payload["schema_version"], bool)
            or payload["schema_version"] != SCHEMA_VERSION
        ):
            raise ValueError("unsupported daemon state schema")
        if not all(
            isinstance(payload[name], str)
            for name in ("instance_id", "host", "started_at", "database")
        ):
            raise ValueError("daemon state text fields are invalid")
        if (
            not isinstance(payload["pid"], int)
            or isinstance(payload["pid"], bool)
            or not isinstance(payload["port"], int)
            or isinstance(payload["port"], bool)
        ):
            raise ValueError("daemon pid and port must be integers")
        return cls(
            instance_id=payload["instance_id"],
            pid=payload["pid"],
            host=payload["host"],
            port=payload["port"],
            started_at=payload["started_at"],
            database=payload["database"],
        )


def _process_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class DesktopDaemon:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_path = settings.state_dir / "daemon.json"
        self.log_path = settings.state_dir / "daemon.log"

    def _read(self) -> DaemonState | None:
        if not self.state_path.exists():
            return None
        if self.state_path.is_symlink():
            raise ValueError("daemon state cannot be a symbolic link")
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return DaemonState.from_dict(payload)

    def _write(self, state: DaemonState) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        if temporary.is_symlink():
            raise ValueError("daemon temporary state cannot be a symbolic link")
        temporary.write_text(
            json.dumps(state.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)

    @staticmethod
    def _connect_host(host: str) -> str:
        address = ipaddress.ip_address(host)
        if address.is_unspecified:
            return "::1" if address.version == 6 else "127.0.0.1"
        return host

    def _probe(self, state: DaemonState) -> bool:
        host = self._connect_host(state.host)
        authority = f"[{host}]" if ":" in host else host
        request = urllib.request.Request(
            f"http://{authority}:{state.port}/health",
            headers={"Accept": "application/json"},
        )
        token = os.environ.get("ACR_API_TOKEN")
        if token:
            request.add_header("X-ACR-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=0.75) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError, TimeoutError, urllib.error.URLError,
            json.JSONDecodeError,
        ):
            return False
        return (
            payload.get("status") == "ok"
            and payload.get("daemon_instance_id") == state.instance_id
        )

    def status(self) -> dict[str, object]:
        state = self._read()
        if state is None:
            return {"status": "stopped", "state_file": str(self.state_path)}
        alive = _process_alive(state.pid)
        verified = alive and self._probe(state)
        return {
            "status": "running" if verified else "stale",
            **state.as_dict(),
            "process_alive": alive,
            "identity_verified": verified,
            "log": str(self.log_path),
        }

    def start(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        allow_network: bool = False,
        ready_timeout: float = 12.0,
    ) -> dict[str, object]:
        self.settings.ensure_local_directories()
        address = ipaddress.ip_address(host)
        if not 1 <= port <= 65_535:
            raise ValueError("daemon port must be 1..65535")
        if not address.is_loopback:
            if self.settings.profile_policy.zero_cloud:
                raise ValueError("zero-cloud daemon binding must remain loopback")
            if not allow_network:
                raise ValueError(
                    "non-loopback daemon binding requires --allow-network"
                )
            if not os.environ.get("ACR_API_TOKEN"):
                raise ValueError(
                    "non-loopback daemon binding requires ACR_API_TOKEN"
                )
        prior = self._read()
        if prior is not None and _process_alive(prior.pid):
            if self._probe(prior):
                raise RuntimeError("ACR daemon is already running")
            raise RuntimeError(
                "daemon state points to an unverified live process; "
                "refusing to replace it"
            )
        if prior is not None:
            self.state_path.unlink()

        instance_id = str(uuid.uuid4())
        command = [
            sys.executable,
            "-m",
            "acr_runtime.cli",
            "--db",
            str(self.settings.database),
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--daemon-instance-id",
            instance_id,
        ]
        popen_options: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            popen_options["startupinfo"] = startup
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
        else:
            popen_options["start_new_session"] = True
        if self.log_path.is_symlink():
            raise ValueError("daemon log cannot be a symbolic link")
        with self.log_path.open("ab") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                **popen_options,
            )
        state = DaemonState(
            instance_id=instance_id,
            pid=process.pid,
            host=host,
            port=port,
            started_at=datetime.now(timezone.utc).isoformat(),
            database=str(self.settings.database.resolve()),
        )
        self._write(state)
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if self._probe(state):
                return self.status()
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self.state_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ACR daemon did not become ready; inspect {self.log_path}"
        )

    def stop(self, *, timeout: float = 8.0) -> dict[str, object]:
        state = self._read()
        if state is None:
            return {"status": "stopped", "already_stopped": True}
        if not _process_alive(state.pid):
            self.state_path.unlink()
            return {"status": "stopped", "stale_state_removed": True}
        if not self._probe(state):
            raise RuntimeError(
                "daemon identity could not be verified; refusing to signal pid"
            )
        os.kill(state.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _process_alive(state.pid):
            time.sleep(0.1)
        if _process_alive(state.pid):
            raise RuntimeError("daemon did not stop before timeout")
        self.state_path.unlink(missing_ok=True)
        return {"status": "stopped", "pid": state.pid}
