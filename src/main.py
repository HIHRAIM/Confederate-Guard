import asyncio
import discord
import db
from config import BOT_TOKEN, SERVICE_CHATS, BACKUP_CHATS
from discord_bot import bot
from utils import get_guild_lang, localized

async def send_service_event(event_key):
    for channel_id in SERVICE_CHATS.get("discord", set()):
        try:
            ch = bot.get_channel(channel_id)
            if not ch:
                try:
                    ch = await bot.fetch_channel(channel_id)
                except Exception:
                    ch = None
            if ch:
                guild_id = ch.guild.id if getattr(ch, "guild", None) else None
                lang = get_guild_lang(guild_id) if guild_id else "en"
                text = localized(event_key, lang)
                await ch.send(text)
        except Exception:
            pass

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

async def main():
    db.init()
    db.cleanup_expired_user_data()

    await asyncio.sleep(0)
    task = asyncio.create_task(bot.start(BOT_TOKEN))
    asyncio.get_event_loop().create_task(backup_loop())
    asyncio.get_event_loop().create_task(retention_loop())

    await asyncio.sleep(5)
    await send_service_event("bot_started")

    try:
        await task
    finally:
        await send_service_event("bot_stopped")

if __name__ == "__main__":
    asyncio.run(main())