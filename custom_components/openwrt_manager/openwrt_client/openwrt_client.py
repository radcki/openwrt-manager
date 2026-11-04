"""Asynchronous SSH client for OpenWrt routers."""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
from typing import Any

import asyncssh

_LOGGER = logging.getLogger(__name__)

SSH_TIMEOUT = 15


class OpenWrtClientError(Exception):
    """Base exception for OpenWrt client errors."""


class CannotConnect(OpenWrtClientError):
    """Exception raised when connection to OpenWrt fails."""


class InvalidAuth(OpenWrtClientError):
    """Exception raised when authentication fails."""


class HostKeyMismatch(CannotConnect):
    """Exception raised when the router presents an unexpected SSH host key."""


class OpenWrtClient:
    """Asynchronous client for interacting with OpenWrt via SSH.

    If ``host_key`` (an OpenSSH public key line) is given, the router must
    present exactly that key. Otherwise the first key seen is trusted and
    exposed through ``host_key`` so the caller can persist it (TOFU).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str = "root",
        password: str = "",
        host_key: str | None = None,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.host_key = host_key
        self._ssh_conn: asyncssh.SSHClientConnection | None = None

    async def _get_ssh_conn(self) -> asyncssh.SSHClientConnection:
        """Get or establish an SSH connection."""
        if self._ssh_conn is not None and not self._ssh_conn.is_closed():
            return self._ssh_conn

        _LOGGER.debug(
            "Establishing SSH connection to %s:%d as %s",
            self.host,
            self.port,
            self.username,
        )
        known_hosts = (
            asyncssh.import_known_hosts(f"* {self.host_key}") if self.host_key else None
        )
        try:
            conn = await asyncssh.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                known_hosts=known_hosts,
                connect_timeout=10,
            )
        except asyncssh.PermissionDenied as err:
            raise InvalidAuth(
                f"SSH authentication failed for user '{self.username}' on {self.host}:{self.port}"
            ) from err
        except asyncssh.HostKeyNotVerifiable as err:
            raise HostKeyMismatch(
                f"SSH host key of {self.host}:{self.port} does not match the stored key"
            ) from err
        except (TimeoutError, OSError, asyncssh.Error) as err:
            raise CannotConnect(
                f"Failed to connect to OpenWrt via SSH at {self.host}:{self.port}: {err}"
            ) from err

        if self.host_key is None:
            server_key = conn.get_server_host_key()
            if server_key is not None:
                self.host_key = server_key.export_public_key("openssh").decode().strip()
                _LOGGER.debug("Trusting SSH host key of %s on first use", self.host)

        self._ssh_conn = conn
        return conn

    async def _run(self, cmd: str) -> str:
        conn = await self._get_ssh_conn()
        result = await conn.run(cmd, check=False, timeout=SSH_TIMEOUT)
        out = result.stdout
        if isinstance(out, bytes):
            return out.decode("utf-8", errors="replace")
        return out or ""

    async def async_run_ssh_command(self, cmd: str) -> str:
        """Execute a shell command over SSH, reconnecting once if the link dropped."""
        try:
            try:
                return await self._run(cmd)
            except (asyncssh.ConnectionLost, asyncssh.DisconnectError):
                _LOGGER.debug("SSH connection dropped, reconnecting...")
                self._ssh_conn = None
                return await self._run(cmd)
        except OpenWrtClientError:
            raise
        except (TimeoutError, OSError, asyncssh.Error) as err:
            raise CannotConnect(f"SSH execution error: {err}") from err

    async def close(self) -> None:
        """Close the SSH connection."""
        if self._ssh_conn and not self._ssh_conn.is_closed():
            self._ssh_conn.close()
            with contextlib.suppress(Exception):
                await self._ssh_conn.wait_closed()
        self._ssh_conn = None

    async def async_login(self) -> str:
        """Verify that the SSH connection and credentials work."""
        _LOGGER.debug("Verifying OpenWrt SSH credentials...")
        out = await self.async_run_ssh_command("echo ok")
        if "ok" not in out:
            raise CannotConnect("SSH connected but test command failed to execute")
        return "ssh_authenticated"

    async def async_ubus_call(
        self,
        target: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Run `ubus call` over SSH and return the parsed JSON (empty dict on no/invalid output).

        Connection problems are raised as OpenWrtClientError.
        """
        cmd = f"ubus call {shlex.quote(target)} {shlex.quote(method)}"
        if params:
            cmd += f" {shlex.quote(json.dumps(params))}"

        stdout = (await self.async_run_ssh_command(cmd)).strip()
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            _LOGGER.debug("ubus call %s %s returned non-JSON output", target, method)
            return {}
