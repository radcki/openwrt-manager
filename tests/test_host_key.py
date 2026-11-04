"""End-to-end tests of SSH host key pinning against a local asyncssh server."""

from __future__ import annotations

import asyncssh
import pytest

from custom_components.openwrt_manager.openwrt_client.openwrt_client import (
    HostKeyMismatch,
    OpenWrtClient,
)


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return password == "secret"


async def _start_server() -> tuple[asyncssh.SSHAcceptor, int]:
    async def handle(process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write("ok\n")
        process.exit(0)

    server = await asyncssh.create_server(
        _Server,
        "127.0.0.1",
        0,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=handle,
    )
    return server, server.sockets[0].getsockname()[1]


async def test_host_key_is_learned_then_enforced():
    server, port = await _start_server()
    other, other_port = await _start_server()
    try:
        first = OpenWrtClient("127.0.0.1", port, "root", "secret")
        await first.async_login()
        await first.close()
        assert first.host_key and first.host_key.startswith("ssh-ed25519 ")

        pinned = OpenWrtClient("127.0.0.1", port, "root", "secret", first.host_key)
        await pinned.async_login()
        await pinned.close()

        impostor = OpenWrtClient(
            "127.0.0.1", other_port, "root", "secret", first.host_key
        )
        with pytest.raises(HostKeyMismatch):
            await impostor.async_login()
    finally:
        server.close()
        other.close()
