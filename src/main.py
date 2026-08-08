"""Entry point: bring the database up to date, start the client, run the
loops that are not tied to Discord events.

Three loops live here rather than in discord_bot/client.py because none of
them is about Discord — they are about the database and about the deployment:
encrypted backups, the retention sweep, and the seven-day setup deadline. The
loops that *are* about Discord (unbans, presence, the verification backfill)
start in GuardBot.setup_hook instead.

Run with cwd = src/. The database and .env are both opened by relative path,
and the control panel launches the bot exactly this way.
"""
import asyncio
import discord
import db
from config import BOT_TOKEN, BACKUP_CHATS
from discord_bot import bot
from utils import send_service_event

async def send_db_backup():
    """Build an encrypted snapshot of guard.db and post it to BACKUP_CHATS.

    Every failure is printed and swallowed, per channel: a backup channel that
    has been deleted must not take the other channels — or the loop — down
    with it. The file is encrypted before it leaves the process, because its
    destination is a Discord channel that stores it indefinitely.
    """
    import io
    from backup_crypto import build_encrypted_backup, encrypted_filename
    try:
        data = build_encrypted_backup("guard.db")
    except Exception as e:
        print(f"Periodic backup failed to build: {e}", flush=True)
        return
    fname = encrypted_filename("guard.db")

    for channel_id in BACKUP_CHATS.get("discord", set()):
        try:
            ch = bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(channel_id)
                except Exception:
                    print(f"Periodic backup: cannot fetch channel {channel_id}", flush=True)
                    continue
            if ch:
                await ch.send(file=discord.File(io.BytesIO(data), filename=fname))
        except Exception as e:
            print(f"Periodic backup: failed to send to channel {channel_id}: {e}", flush=True)

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
            print(f"setup_deadline_loop error: {e}", flush=True)
        await asyncio.sleep(24 * 3600)

async def main():
    """Bring the schema up to date, start the client and the loops, run.

    The order matters at both ends. db.init() and the first retention pass
    happen before anything connects, so the client never sees a half-built
    schema; db.rule_since() is called here so that the setup deadline's
    grandfathering line is planted on the first start of this version rather
    than whenever the first sweep happens to run.

    The service chats are told five seconds in, by which time the client is
    normally ready, and told again on the way out through the finally — which
    is the only announcement a crash produces.
    """
    db.init()
    db.cleanup_expired_user_data()
    db.rule_since()

    await asyncio.sleep(0)
    task = asyncio.create_task(bot.start(BOT_TOKEN))
    asyncio.get_event_loop().create_task(backup_loop())
    asyncio.get_event_loop().create_task(retention_loop())
    asyncio.get_event_loop().create_task(setup_deadline_loop())

    await asyncio.sleep(5)
    await send_service_event("bot_started")

    try:
        await task
    finally:
        await send_service_event("bot_stopped")

if __name__ == "__main__":
    asyncio.run(main())