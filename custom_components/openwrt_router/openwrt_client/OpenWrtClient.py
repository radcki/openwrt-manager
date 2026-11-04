import contextlib
import json
import logging
from typing import Any

import asyncssh

PROTOCOL_SSH = "ssh"
_LOGGER = logging.getLogger(__name__)


class OpenWrtClientError(Exception):
    """Base exception for OpenWrt client errors."""


class CannotConnect(OpenWrtClientError):
    """Exception raised when connection to OpenWrt fails."""


class InvalidAuth(OpenWrtClientError):
    """Exception raised when authentication fails."""


class EndpointNotUbus(CannotConnect):
    """Exception raised when HTTP response returns HTML instead of ubus JSON-RPC."""


class OpenWrtClient:
    """Asynchronous client for interacting with OpenWrt via ssh."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str = "root",
        password: str = "",
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._ssh_conn: asyncssh.SSHClientConnection | None = None

    async def _get_ssh_conn(self) -> asyncssh.SSHClientConnection:
        """Get or establish an SSH connection."""
        if self._ssh_conn is None or self._ssh_conn.is_closed():
            _LOGGER.debug(
                "Establishing SSH connection to %s:%d as %s",
                self.host,
                self.port,
                self.username,
            )
            try:
                self._ssh_conn = await asyncssh.connect(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    known_hosts=None,
                    connect_timeout=10,
                )
            except asyncssh.PermissionDenied as err:
                raise InvalidAuth(
                    f"SSH authentication failed for user '{self.username}' on {self.host}:{self.port}"
                ) from err
            except (TimeoutError, OSError, asyncssh.Error) as err:
                raise CannotConnect(
                    f"Failed to connect to OpenWrt via SSH at {self.host}:{self.port}: {err}"
                ) from err
        return self._ssh_conn

    async def async_run_ssh_command(self, cmd: str) -> str:
        """Execute a shell command over SSH."""
        conn = await self._get_ssh_conn()
        try:
            result = await conn.run(cmd, check=False, timeout=15)
            out = result.stdout
            if isinstance(out, bytes):
                return out.decode("utf-8", errors="replace")
            return out or ""
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError):
            _LOGGER.debug("SSH connection dropped, reconnecting...")
            self._ssh_conn = None
            conn = await self._get_ssh_conn()
            result = await conn.run(cmd, check=False, timeout=15)
            out = result.stdout
            if isinstance(out, bytes):
                return out.decode("utf-8", errors="replace")
            return out or ""
        except Exception as err:
            raise CannotConnect(f"SSH execution error: {err}") from err

    async def close(self) -> None:
        """Close HTTP session and SSH connection."""

        if self._ssh_conn and not self._ssh_conn.is_closed():
            self._ssh_conn.close()
            with contextlib.suppress(Exception):
                await self._ssh_conn.wait_closed()
            self._ssh_conn = None

    async def async_login(self) -> str:
        """Authenticate with OpenWrt (ubus session for HTTP/S, connection verify for SSH)."""

        _LOGGER.debug("Verifying OpenWrt SSH credentials...")
        await self._get_ssh_conn()
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
        """Make a generic ubus RPC call with automatic session renewal or SSH execution."""

        if params:
            param_json = json.dumps(params).replace("'", "'\"'\"'")
            cmd = f"ubus call {target} {method} '{param_json}'"
        else:
            cmd = f"ubus call {target} {method}"

        try:
            stdout = await self.async_run_ssh_command(cmd)
            clean_out = stdout.strip()
            if not clean_out:
                return {}
            return json.loads(clean_out)
        except json.JSONDecodeError:
            _LOGGER.debug(
                "ubus call %s %s over SSH returned non-JSON output", target, method
            )
            return {}
        except Exception as err:
            _LOGGER.debug("ubus call %s %s failed over SSH: %s", target, method, err)
            return {}
