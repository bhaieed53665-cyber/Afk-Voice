"""
Discord AFK Voice Bot (Self-Bot) - DAVE (E2EE) compatible

Joins a voice channel, self-mutes, stays connected, and auto-reconnects.

Commands (only work from your own account):
    !join [channel_id]  - Join a voice channel (default if no ID given)
    !leave              - Disconnect from voice
    !status             - Show connection status and uptime
"""

import asyncio
import logging
import os
import time

import discord
from discord.ext import commands

# Discord now requires DAVE (E2EE) on voice. The stock voice client of
# discord.py-self can't do it, so we use the native voice extension.
try:
    from discord.ext.native_voice import VoiceClient as VoiceImpl

    NATIVE_VOICE = True
except ImportError:  # falls back to the old client (will fail with 4017)
    VoiceImpl = discord.VoiceClient
    NATIVE_VOICE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_VC_ID = int(os.getenv("DEFAULT_VC_ID") or 0)
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
AUTO_JOIN = os.getenv("AUTO_JOIN", "false").lower() == "true"
SELF_MUTE = os.getenv("SELF_MUTE", "true").lower() == "true"
SELF_DEAF = os.getenv("SELF_DEAF", "false").lower() == "true"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # seconds

if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN is not set.")
if not DEFAULT_VC_ID:
    raise SystemExit("ERROR: DEFAULT_VC_ID is not set.")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("afk-bot")

if not NATIVE_VOICE:
    log.warning(
        "discord-native-voice is NOT installed: voice will fail with code 4017 (DAVE)."
    )

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

bot = commands.Bot(command_prefix=COMMAND_PREFIX, self_bot=True)

bot.state = {
    "connected_since": None,   # timestamp when voice was joined
    "target_channel_id": None,  # channel we WANT to be in (None = stay out)
    "keepalive_task": None,
}

join_lock = asyncio.Lock()


@bot.check
async def only_me(ctx):
    """Only react to commands typed by the account itself."""
    return ctx.author.id == bot.user.id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_voice_client(guild):
    for vc in bot.voice_clients:
        if getattr(vc, "guild", None) and vc.guild.id == guild.id:
            return vc
    return None


async def join_voice(channel) -> bool:
    """Join a voice channel (muted per config). Returns True on success."""
    async with join_lock:
        try:
            old = get_voice_client(channel.guild)
            if old:
                try:
                    await old.disconnect(force=True)
                except Exception:
                    pass

            log.info("Connecting to #%s (ID: %s) ...", channel.name, channel.id)
            await channel.connect(
                cls=VoiceImpl,
                timeout=30.0,
                reconnect=True,
                self_mute=SELF_MUTE,
                self_deaf=SELF_DEAF,
            )

            bot.state["connected_since"] = time.time()
            bot.state["target_channel_id"] = channel.id
            log.info("Joined #%s (mute=%s, deaf=%s).", channel.name, SELF_MUTE, SELF_DEAF)
            return True

        except Exception as exc:
            log.error("Failed to join #%s: %r", channel.name, exc)
            return False


async def keepalive_loop():
    """Every CHECK_INTERVAL seconds, make sure we're still in the voice channel."""
    await bot.wait_until_ready()
    failures = 0
    while not bot.is_closed():
        await asyncio.sleep(min(CHECK_INTERVAL * (2 ** failures), 300))

        target_id = bot.state["target_channel_id"]
        if not target_id:
            failures = 0
            continue

        channel = bot.get_channel(target_id)
        if channel is None:
            log.warning("Target channel %s not found.", target_id)
            continue

        vc = get_voice_client(channel.guild)
        if vc is not None and vc.is_connected():
            failures = 0
            continue

        log.warning("Not in voice anymore, reconnecting ...")
        ok = await join_voice(channel)
        failures = 0 if ok else min(failures + 1, 4)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user.name, bot.user.id)

    if bot.state["keepalive_task"] is None:
        bot.state["keepalive_task"] = asyncio.create_task(keepalive_loop())

    if AUTO_JOIN:
        channel = bot.get_channel(DEFAULT_VC_ID)
        if channel:
            bot.state["target_channel_id"] = channel.id
            await join_voice(channel)
        else:
            log.error("Default voice channel ID %s not found.", DEFAULT_VC_ID)
    else:
        log.info("Auto-join disabled. Use %sjoin to connect.", COMMAND_PREFIX)


# ---------------------------------------------------------------------------
# Text Commands
# ---------------------------------------------------------------------------


@bot.command(name="join")
async def cmd_join(ctx, channel_id: int = None):
    """Join a voice channel. Uses default if no ID is given."""
    target_id = channel_id or DEFAULT_VC_ID
    channel = bot.get_channel(target_id)
    if channel is None:
        log.warning("Channel ID %s not found.", target_id)
        return
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        log.warning("Channel %s is not a voice channel.", target_id)
        return

    bot.state["target_channel_id"] = channel.id
    await join_voice(channel)


@bot.command(name="leave")
async def cmd_leave(ctx):
    """Disconnect from voice and stop auto-reconnecting."""
    bot.state["target_channel_id"] = None
    bot.state["connected_since"] = None

    if not bot.voice_clients:
        log.info("Not currently in a voice channel.")
        return

    for vc in list(bot.voice_clients):
        try:
            await vc.disconnect(force=True)
        except Exception as exc:
            log.warning("Error while disconnecting: %r", exc)
    log.info("Disconnected from voice by command.")


@bot.command(name="status")
async def cmd_status(ctx):
    """Reply with the current connection status."""
    channel = bot.get_channel(bot.state["target_channel_id"] or 0)
    vc = get_voice_client(channel.guild) if channel else None
    connected = vc is not None and vc.is_connected()

    if connected and bot.state["connected_since"]:
        uptime = int(time.time() - bot.state["connected_since"])
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
        msg = (
            "```ansi\n"
            f"\x1b[1;32m[Connected]\x1b[0m to "
            f"\x1b[1;34m#{channel.name}\x1b[0m for "
            f"\x1b[1;35m{uptime_str}\x1b[0m\n"
            "```"
        )
        log.info("Status: Connected to #%s for %s", channel.name, uptime_str)
    else:
        msg = (
            "```ansi\n"
            "\x1b[1;31m[Disconnected]\x1b[0m \u2014 not in any voice channel\n"
            "```"
        )
        log.info("Status: Disconnected")

    await ctx.send(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting AFK Voice Bot ...")
    bot.run(TOKEN)
