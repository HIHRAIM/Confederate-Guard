"""Ban delivery: carrying out a ban decision, undoing it, and the sweep that
lifts what has expired.

The decision itself is made elsewhere — automatically in events.py, by hand in
commands/bans.py, by a consul in tribunal.py — and all three end up here,
which is why the DM, the message purge, the audit-log reason suffix and the
network fan-out live in one module rather than three.

Two things in here look like they belong to the appeal system but do not.
`GATE_BAN_MARKER` and `_lift_expired_gate_bans` implement the Purgatorium
gate's one-day ban, which is a ban and is lifted by a ban sweep;
purgatorium.py decides *who* gets one. `_find_registered_guild_ban` is the
fallback that lets the gate recognize a ban the bot never issued, and it is
here because it reads ban lists.

`_purgatorium_invite_line` is imported at the call site: purgatorium.py
imports this module at module level, so the reverse direction has to stay
inside the function.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import discord

import db
from config import PURGATORIUM_GUILD_ID
from discord_bot.client import _get_channel, bot
from utils import DEFAULT_LANG, SUPPORTED_LANGS, localized

GATE_BAN_MARKER = "[gate-unban:"

GATE_SCAN_INTERVAL = 900

async def _find_registered_guild_ban(uid):
    """Language of the first server registered via /setup where the user is
    really banned, or None.

    The database only knows the bans the bot issued itself: a moderator who
    bans by hand (or another bot that does) leaves no row in active_bans. The
    Purgatorium gate therefore falls back to the servers' live ban lists, so a
    genuinely banned user is invited to appeal instead of being locked out for
    a day. Nothing is written back — the gate keeps no trace of the visitor.
    """
    for guild in list(bot.guilds):
        if guild.id == PURGATORIUM_GUILD_ID:
            continue
        guild_row = db.get_guild(guild.id)
        if guild_row is None:
            continue
        try:
            await guild.fetch_ban(discord.Object(id=uid))
        except Exception:
            continue
        return guild_row["lang"] if guild_row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
    return None

async def _execute_global_unban(network, uid):
    """Lift a network's global ban: delete the DB record and unban the user on
    the origin guild plus every guild that enforced the ban. Returns how many
    guilds were processed."""
    gban = db.get_global_ban(network, uid)
    if not gban:
        return 0

    db.remove_global_ban(network, uid)
    origin_guild_id = str(gban["origin_guild_id"]) if gban["origin_guild_id"] else None

    count = 0
    for g in db.get_network_guilds(network):
        gid = int(g["guild_id"])
        enforced = str(uid) in db.get_gban_enforcements(gid)
        is_origin = origin_guild_id is not None and str(gid) == origin_guild_id
        if not (enforced or is_origin):
            continue
        guild_obj = bot.get_guild(gid)
        if guild_obj:
            try:
                await guild_obj.unban(discord.Object(id=uid))
            except Exception:
                pass
        db.remove_active_ban(gid, uid)
        db.remove_gban_enforcement(gid, uid)
        db.remove_ban_history(gid, uid)
        count += 1
    return count

async def _purge_recent_messages(guild, user_id, hours=24):
    """Delete the user's messages posted on `guild` within the last `hours`.

    The same sweep the spam guard runs after an automatic ban; used by /ban
    and /globalban so a banned user's recent messages disappear too.
    """
    cutoff = discord.utils.utcnow() - timedelta(hours=hours)
    for channel in guild.text_channels:
        try:
            to_delete = []
            async for msg in channel.history(after=cutoff, limit=None):
                if msg.author.id == user_id:
                    to_delete.append(msg)
            for i in range(0, len(to_delete), 100):
                await channel.delete_messages(to_delete[i:i+100])
        except Exception:
            pass

def _format_ban_reason_with_suffix(reason, lang, unban_at):
    """Append the localized '(ban issued by bot <name>; end date: <date>)' suffix.

    The suffix lands in the Discord audit-log reason (plain text), so we use a
    human-readable UTC date rather than Discord timestamp markup.
    """
    bot_name = bot.user.display_name if bot.user else "bot"
    end_date = datetime.fromtimestamp(int(unban_at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    suffix = localized("gban_enforce_reason_suffix", lang, bot=bot_name, date=end_date)
    return f"{reason} {suffix}".strip()

async def enforce_global_ban(guild, member_or_id, gban, guild_row, lang):
    """Ban a network-banned user on `guild` for the time left on the network ban.

    DMs the user, applies the ban (reason carries a localized suffix), schedules
    the matching local unban, records the enforcement so /setgbans disable can
    revert exactly these bans, and logs the action. Returns True if applied.
    """
    from discord_bot.purgatorium import _purgatorium_invite_line

    if isinstance(member_or_id, (discord.Member, discord.User)):
        uid = member_or_id.id
        member_obj = member_or_id
    else:
        uid = int(member_or_id)
        member_obj = guild.get_member(uid)

    unban_at = int(gban["unban_at"])
    if unban_at <= int(time.time()):
        return False
    reason = gban["reason"] or ""

    if member_obj is not None:
        try:
            dm_text = localized(
                "gban_enforce_dm", lang,
                server=guild.name, reason=reason, remaining=f"<t:{unban_at}:R>",
            )
            invite_line = _purgatorium_invite_line(guild.id, lang)
            if invite_line:
                dm_text = f"{dm_text}\n{invite_line}"
            await member_obj.send(dm_text)
        except Exception:
            pass

    try:
        await guild.ban(
            discord.Object(id=uid),
            reason=_format_ban_reason_with_suffix(reason, lang, unban_at),
            delete_message_days=0,
        )
    except Exception:
        return False

    db.add_active_ban(guild.id, uid, unban_at)
    db.add_gban_enforcement(guild.id, uid)
    db.record_ban(guild.id, uid)

    if guild_row and guild_row["log_channel_id"]:
        channel = await _get_channel(guild_row["log_channel_id"])
        if channel:
            origin_guild_id = gban["origin_guild_id"]
            origin_guild = bot.get_guild(int(origin_guild_id)) if origin_guild_id else None
            origin_name = origin_guild.name if origin_guild else (str(origin_guild_id) if origin_guild_id else "—")
            mention = member_obj.mention if member_obj else f"<@{uid}>"
            username = str(member_obj) if member_obj else str(uid)
            banned_at = gban["banned_at"]
            try:
                await channel.send(
                    localized(
                        "gban_enforce_log", lang,
                        mention=mention, username=username, id=uid,
                        origin_server=origin_name,
                        issued=(f"<t:{int(banned_at)}:F>" if banned_at else "—"),
                        remaining=f"<t:{unban_at}:R>",
                    )
                )
            except Exception:
                pass
    return True

async def notify_prior_network_ban(member: discord.Member, guild_row, lang):
    """Alert admins if `member` was ever banned on any server in this network."""
    network = guild_row["network"]
    if network is None:
        return
    rows = db.get_network_ban_history(network, member.id)
    if not rows:
        return

    origin_guild_id = rows[0]["guild_id"]
    origin_guild = bot.get_guild(int(origin_guild_id)) if origin_guild_id else None
    origin_name = origin_guild.name if origin_guild else str(origin_guild_id)

    channel = await _get_channel(guild_row["log_channel_id"])
    if not channel:
        return
    try:
        await channel.send(
            localized(
                "network_ban_notice", lang,
                mention=member.mention, username=str(member), id=member.id,
                server=origin_name,
            )
        )
    except Exception:
        pass

async def _lift_expired_gate_bans(client: discord.Client):
    """Unban Purgatorium gate bans whose time is up.

    Gate bans are not stored in the database: the unban moment lives in the
    audit-log reason as '[gate-unban:<unix ts>]'. Scanning the guild's ban list
    for expired markers survives restarts without keeping any record of who
    visited the server.
    """
    purg = client.get_guild(PURGATORIUM_GUILD_ID)
    if purg is None:
        return
    now = int(time.time())
    async for entry in purg.bans(limit=None):
        reason = entry.reason or ""
        start = reason.find(GATE_BAN_MARKER)
        if start == -1:
            continue
        start += len(GATE_BAN_MARKER)
        end = reason.find("]", start)
        if end == -1:
            continue
        try:
            unban_at = int(reason[start:end])
        except ValueError:
            continue
        if unban_at <= now:
            try:
                await purg.unban(entry.user, reason="Purgatorium gate: 1-day ban expired")
            except Exception:
                pass

async def unban_loop(client: discord.Client):
    """The bot's one recurring moderation task: lift what has expired.

    Two cadences in one loop. Every 60 seconds it walks the local bans whose
    unban_at has passed and drops expired network ban records — cheap, and
    late by at most a minute. Every GATE_SCAN_INTERVAL it additionally does
    the three jobs that cost API calls or scan tables: expired Purgatorium
    gate bans, tribunal cases nobody decided, and stale ban-summary
    bookkeeping. `_retire_stale_tribunal_cases` is imported at the call site
    because tribunal.py imports this module at module level.

    The whole body is wrapped in one try/except by design: this loop must
    outlive any single failure, since it is the only thing that ever unbans
    anybody.
    """
    from discord_bot.tribunal import _retire_stale_tribunal_cases

    await client.wait_until_ready()
    next_gate_scan = 0
    while not client.is_closed():
        try:
            rows = db.get_expired_bans()
            for row in rows:
                guild = client.get_guild(int(row["guild_id"]))
                if guild:
                    try:
                        await guild.unban(discord.Object(id=int(row["user_id"])))
                    except Exception:
                        pass
                db.remove_active_ban(row["guild_id"], row["user_id"])
                db.remove_gban_enforcement(row["guild_id"], row["user_id"])
            db.cleanup_expired_global_bans()
            if time.time() >= next_gate_scan:
                next_gate_scan = time.time() + GATE_SCAN_INTERVAL
                await _lift_expired_gate_bans(client)
                await _retire_stale_tribunal_cases(client)
                db.cleanup_old_baninfo_posts()
        except Exception:
            pass
        await asyncio.sleep(60)
