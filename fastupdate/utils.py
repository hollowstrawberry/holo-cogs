import asyncio
import discord
import itertools
import contextlib
from typing import Mapping


def get_result_set(result: Mapping[str, list[str] | dict[str, str]], *keys: str) -> set[str]:
    return set(itertools.chain.from_iterable(list(result.get(result_name) or "") for result_name in keys))

@contextlib.asynccontextmanager
async def bot_is_typing(channel: discord.abc.Messageable):
    """Like channel.typing but doesn't stop execution if typing fails."""
    async def keep_typing():
        while True:
            await asyncio.wait_for(channel._state.http.send_typing(getattr(channel, "id")), timeout=2.0)
            await asyncio.sleep(5.0)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
