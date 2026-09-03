"""Entry point: bring the database up to date, start the client, run the
loops that are not tied to Discord events.

Three loops live here rather than in discord_bot/client.py because none of
them is about Discord — they are about the database and about the deployment:
encrypted backups, the retention sweep, and the seven-day setup deadline. The
loops that *are* about Discord (unbans, presence, the verification backfill)
start in GuardBot.setup_hook instead.

The client is started by start_client() rather than by bot.start(), because
the first connection to the gateway is the one moment discord.py cannot
recover from on its own — see that function.

Run with cwd = src/. The database and .env are both opened by relative path,
and the control panel launches the bot exactly this way.
"""
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("guard.main")

import aiohttp
import discord

import db
from config import BOT_TOKEN, BACKUP_CHATS
from discord_bot import bot
from utils import send_service_event

GATEWAY_RETRY_ATTEMPTS = 8
GATEWAY_RETRY_START = 5
GATEWAY_RETRY_MAX = 300

TRANSIENT_ERRORS = (
    OSError,
    asyncio.TimeoutError,
    aiohttp.ClientError,
    discord.HTTPException,
    discord.GatewayNotFound,
)

async def _login_with_backoff():
    """Log in over HTTP, retrying while the failure is the network.

    Only the login is retried, and only for the errors that mean "not right
    now": a bad token raises LoginFailure, which is not an HTTPException and
    so is not caught here at all.

    discord.py calls GuardBot.setup_hook at the end of login(), so a login
    that reached that far has already synced the command tree and started the
    background loops. setup_hook refuses to start them twice, which is what
    makes retrying this call safe at all.
    """
    delay = GATEWAY_RETRY_START
    for attempt in range(1, GATEWAY_RETRY_ATTEMPTS + 1):
        try:
            await bot.login(BOT_TOKEN)
            return
        except TRANSIENT_ERRORS as e:
            if attempt == GATEWAY_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "login failed (%s: %s); attempt %d/%d, retrying in %ds",
                type(e).__name__, e, attempt, GATEWAY_RETRY_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, GATEWAY_RETRY_MAX)

async def _connect_with_backoff():
    """Reach the gateway, retrying the *first* handshake with a backoff.

    discord.py recovers from a dropped connection by resuming from
    bot.ws.sequence, but until the first handshake succeeds bot.ws is still
    None — so any 5xx from the gateway at start-up becomes an AttributeError
    on NoneType inside Client.connect and takes the process down. Observed
    live: a restart met a 503 on the first handshake, systemd restarted the
    unit ten seconds later, and the bot was blind until it did.

    That single window is all this loop covers. It retries only while bot.ws
    is None, which is exactly "no connection has ever been made", so a retry
    can neither duplicate a session nor lose events. Once a connection has
    succeeded discord.py's own reconnect logic is sound, and anything raised
    after that point is passed on unchanged — including
    PrivilegedIntentsRequired, which can only be raised once a handshake has
    happened.
    """
    delay = GATEWAY_RETRY_START
    for attempt in range(1, GATEWAY_RETRY_ATTEMPTS + 1):
        try:
            await bot.connect(reconnect=True)
            return
        except Exception as e:
            if bot.ws is not None or bot.is_closed():
                raise
            if attempt == GATEWAY_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "gateway handshake failed (%s: %s); attempt %d/%d, retrying in %ds",
                type(e).__name__, e, attempt, GATEWAY_RETRY_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, GATEWAY_RETRY_MAX)

async def start_client():
    """What bot.start() would do, with the start-up retry around it.

    The two halves of bot.start() are separated because they need different
    treatment: the login must happen exactly once, since it is what runs
    setup_hook, while the connection is the half that fails at start-up.

    A client abandoned mid-start keeps its aiohttp connector open — the
    "Unclosed connector" that followed the crash — so the client is closed
    before the failure is passed on to the process.
    """
    try:
        await _login_with_backoff()
        await _connect_with_backoff()
    except Exception:
        try:
            await bot.close()
        except Exception:
            logger.warning("closing the client after a failed start failed", exc_info=True)
        raise

async def announce_started():
    """Tell the service chats the bot is up, once it actually is.

    Waits for the client rather than for a fixed few seconds: with the
    start-up retry in _connect_with_backoff the first connection can take
    minutes, and send_service_event needs a live client to resolve a channel
    at all — announced any earlier it would simply be lost.
    """
    await bot.wait_until_ready()
    await send_service_event("bot_started")

async def send_db_backup():
    """Build an encrypted snapshot of guard.db and post it to BACKUP_CHATS.

    Every failure is logged and swallowed, per channel: a backup channel that
    has been deleted must not take the other channels — or the loop — down
    with it. The file is encrypted before it leaves the process, because its
    destination is a Discord channel that stores it indefinitely.
    """
    import io
    from backup_crypto import build_encrypted_backup, encrypted_filename
    try:
        data = build_encrypted_backup("guard.db")
    except Exception as e:
        logger.error("periodic backup failed to build: %s", e)
        return
    fname = encrypted_filename("guard.db")

    for channel_id in BACKUP_CHATS.get("discord", set()):
        try:
            ch = bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(channel_id)
                except Exception:
                    logger.error("periodic backup: cannot fetch channel %s", channel_id)
                    continue
            if ch:
                await ch.send(file=discord.File(io.BytesIO(data), filename=fname))
        except Exception as e:
            logger.error("periodic backup: failed to send to channel %s: %s", channel_id, e)

async def backup_loop():
    """Every 12 hours, send the encrypted database snapshot to the backup
    chats. Sleeps first: a crash-looping bot must not spam backups on every
    restart."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(12 * 3600)
        await send_db_backup()

async def retention_loop():
    """Expire stored user ids and old localization dialogs, daily.

    Runs its first pass immediately on start-up — the retention promise is a
    published one (PRIVACY.md), and a deployment restarted daily would
    otherwise never reach the sweep at all.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            db.cleanup_expired_user_data()
            db.cleanup_old_loc_suggestions()
        except Exception:
            pass
        await asyncio.sleep(24 * 3600)

async def setup_deadline_loop():
    """Daily wrapper around setup_deadline.setup_deadline_pass: leaves the
    servers whose seven days ran out without a `/setup`, and reports each
    departure to the service chats.

    Waits for the client first — the sweep reads `Guild.me.joined_at` off the
    guild list, and an empty one would simply find nothing to do."""
    from setup_deadline import setup_deadline_pass

    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for event_key, fields in await setup_deadline_pass(bot):
                await send_service_event(event_key, **fields)
        except Exception as e:
            logger.error("setup_deadline_loop error: %s", e)
        await asyncio.sleep(24 * 3600)

async def main():
    """Bring the schema up to date, start the client and the loops, run.

    The order matters at both ends. db.init() and the first retention pass
    happen before anything connects, so the client never sees a half-built
    schema; db.rule_since() is called here so that the setup deadline's
    grandfathering line is planted on the first start of this version rather
    than whenever the first sweep happens to run.

    The service chats are told once the client is ready, and told again on
    the way out through the finally — which is the only announcement a crash
    produces.
    """
    db.init()
    db.cleanup_expired_user_data()
    db.rule_since()

    await asyncio.sleep(0)
    task = asyncio.create_task(start_client())
    asyncio.get_event_loop().create_task(backup_loop())
    asyncio.get_event_loop().create_task(retention_loop())
    asyncio.get_event_loop().create_task(setup_deadline_loop())
    asyncio.get_event_loop().create_task(announce_started())

    try:
        await task
    finally:
        await send_service_event("bot_stopped")

if __name__ == "__main__":
    asyncio.run(main())
