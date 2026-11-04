import asyncio

from custom_components.openwrt_router.openwrt_client.OpenWrtManager import (
    OpenWrtManager,
)


async def main():
    router = OpenWrtManager("192.168.1.1", password="eden2108", protocol="ssh")
    clients = await router.async_get_clients()
    await router.async_block_client("C0:09:25:EC:D5:92")
    await router.async_unblock_client("C0:09:25:EC:D5:92")
    print(clients)


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
