"""
Discord AFK Voice Bot (official bot account, discord.py 2.7+ with DAVE)

Stays in a voice channel and auto-reconnects.
Commands (owner only): !join [channel_id]  !leave  !status
"""

import asyncio
import logging
import os
import time

import discord
from discord.ext import commands, tasks

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_VC_ID = int(os.getenv("DEFAULT_VC_ID") or 0)
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
AUTO_JOIN = os.getenv("AUTO_JOIN", "true").lower() == "true"
SELF_MUTE = os.getenv("SELF_MUTE", "true").lower() == "true"
SELF_DEAF = os.getenv("SELF_DEAF", "true").lower() == "true"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN is not set.")
if not DEFAULT_VC_ID:
    raise SystemExit("ERROR: DEFAULT_VC_ID is not set.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("afk-bot")

intents = discord.Intents.default()
intents.message_content = True  # needed for prefix commands
intents.voice_states = True

join_lock = asyncio.Lock()
state = {"target_channel_id": None, "connected_since": None}


class AfkBot(commands.Bot):
    async def setup_hook(self):
        keepalive.start()


bot = AfkBot(command_prefix=COMMAND_PREFIX, intents=intents)


@bot.check
async def owner_only(ctx):
    return await bot.is_owner(ctx.author)


async def join_voice(channel) -> bool:
    async with join_lock:
        try:
            vc = channel.guild.voice_client
            if vc is not None:
                if vc.is_connected() and vc.channel.id == channel.id:
                    return True
                await vc.disconnect(force=True)

            log.info("Connecting to #%s ...", channel.name)
            await channel.connect(
                timeout=30.0,
                reconnect=True,
                self_mute=SELF_MUTE,
                self_deaf=SELF_DEAF,
            )
            state["target_channel_id"] = channel.id
            state["connected_since"] = time.time()
            log.info("Joined #%s.", channel.name)
            return True
        except Exception as exc:
            log.error("Failed to join #%s: %r", channel.name, exc)
            return False


@tasks.loop(seconds=CHECK_INTERVAL)
async def keepalive():
    target_id = state["target_channel_id"]
    if not target_id:
        return
    channel = bot.get_channel(target_id)
    if channel is None:
        return
    vc = channel.guild.voice_client
    if vc is None or not vc.is_connected():
        log.warning("Not in voice anymore, reconnecting ...")
        await join_voice(channel)


@keepalive.before_loop
async def before_keepalive():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    if AUTO_JOIN:
        channel = bot.get_channel(DEFAULT_VC_ID)
        if channel:
            state["target_channel_id"] = channel.id
            await join_voice(channel)
        else:
            log.error("Channel %s not found (is the bot in that server?).", DEFAULT_VC_ID)


@bot.command(name="join")
async def cmd_join(ctx, channel_id: int = None):
    channel = bot.get_channel(channel_id or DEFAULT_VC_ID)
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        await ctx.send("Voice channel not found.")
        return
    state["target_channel_id"] = channel.id
    ok = await join_voice(channel)
    await ctx.send("Joined." if ok else "Failed, check the logs.")


@bot.command(name="leave")
async def cmd_leave(ctx):
    state["target_channel_id"] = None
    state["connected_since"] = None
    if ctx.guild and ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect(force=True)
        await ctx.send("Left.")
    else:
        await ctx.send("Not in a voice channel.")


@bot.command(name="status")
async def cmd_status(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    if vc and vc.is_connected() and state["connected_since"]:
        up = int(time.time() - state["connected_since"])
        h, r = divmod(up, 3600)
        m, s = divmod(r, 60)
        await ctx.send(f"Connected to #{vc.channel.name} for {h:02d}:{m:02d}:{s:02d}")
    else:
        await ctx.send("Disconnected.")


if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
