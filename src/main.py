import asyncio
import discord
import db
from config import BOT_TOKEN, BACKUP_CHATS
from discord_bot import bot
from utils import send_service_event

async def send_db_backup():
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
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(12 * 3600)
        await send_db_backup()

async def retention_loop():
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