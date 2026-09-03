"""All four @bot.event handlers, and nothing else.

This is the dispatcher. Every inbound Discord event lands in one of these four
functions, which decide *which* domain module gets to act and in what order;
none of them contains policy of its own beyond that ordering.

The ordering is the interesting part, particularly in on_message. The sync
channels are checked before anything else and each returns immediately: those
channels carry machine traffic between the two bots, and running the spam
guard or the verification tracker over a bare user id would be at best
pointless and at worst a ban. Only after they are out of the way does the
message become an ordinary message.

Adding a handler here means adding an event; adding behaviour to an existing
one belongs in the domain module it calls.
"""
import logging
import time

import discord

import db
import utils
from config import (
    APPEAL_BANINFO_CHANNELS, APPEAL_PARDON_CHANNELS, PURGATORIUM_GUILD_ID,
    UNVERIFIED, VERIFIED,
)
from discord_bot.bans import (
    enforce_global_ban, notify_prior_network_ban, _purge_recent_messages,
)
from discord_bot.client import bot
from discord_bot.purgatorium import (
    handle_appeal_baninfo, handle_appeal_pardon, handle_purgatorium_join,
    _purgatorium_invite_line,
)
from discord_bot.tribunal import post_tribunal_case
from discord_bot.verification import (
    handle_verification, handle_verification_sync, _grant_verify,
)
from utils import DEFAULT_LANG, is_admin, localized, message_has_banned_link, message_has_spam

logger = logging.getLogger("guard.discord")

@bot.event
async def on_guild_join(guild: discord.Guild):
    """The bot was added to a server: record the join and tell the service
    chats, since a Bot Admin now has seven days to run `/setup` there
    (setup_deadline.py).

    The row is a record, not the clock — the sweep reads Discord's own
    `Guild.me.joined_at`, so a missed event costs nothing but this notice."""
    db.record_join(guild.id)
    await utils.send_service_event(
        "guild_joined",
        guild=guild.name or str(guild.id),
        guild_id=guild.id,
    )

@bot.event
async def on_guild_remove(guild: discord.Guild):
    """The bot was kicked from (or left) a server: drop its setup-deadline
    row, so that a later re-invitation is a fresh seven days rather than a
    settlement inherited from the last time. The server's moderation data is
    deliberately left alone — a kick-and-reinvite must not cost a server its
    log channel, its bans or its network membership."""
    db.forget_deadline(guild.id)

@bot.event
async def on_member_join(member: discord.Member):
    """Someone joined a server: the gate, then the ban, then the roles.

    Purgatorium is handled entirely separately and returns — arriving there is
    a request to appeal, not a membership.

    Everywhere else the order is deliberate. Network-ban enforcement runs
    first and returns on success: a user who is banned back out has no use for
    a prior-ban alert about themselves, a verify role or an autorole. The
    prior-ban alert follows, since it is about someone who was allowed to
    stay. Verification comes before the autorole because only one of them
    depends on state that may have changed while the user was away.
    """
    if member.bot:
        return

    if member.guild.id == PURGATORIUM_GUILD_ID:
        try:
            await handle_purgatorium_join(member)
        except Exception:
            pass
        return

    guild_row = db.get_guild(member.guild.id)
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if guild_row and guild_row["network"] is not None and db.is_gbans_enabled(member.guild.id):
        gban = db.get_active_global_ban(guild_row["network"], member.id)
        if gban:
            try:
                if await enforce_global_ban(member.guild, member, gban, guild_row, lang):
                    return
            except Exception:
                pass

    if guild_row and guild_row["network"] is not None:
        try:
            await notify_prior_network_ban(member, guild_row, lang)
        except Exception:
            pass

    verify_row = db.get_verify(member.guild.id)
    if verify_row and db.is_verified(member.id) and not db.has_verify_grant(member.guild.id, member.id):
        origin = db.get_verified_origin(member.id)
        try:
            await _grant_verify(
                member, verify_row, guild_row, lang,
                cross_server=(origin is not None and origin != member.guild.id)
            )
        except Exception:
            pass

    role_id = db.get_autorole(member.guild.id)
    if not role_id:
        return
    role = member.guild.get_role(int(role_id))
    if role:
        try:
            await member.add_roles(role, reason="autorole")
        except Exception:
            pass

@bot.event
async def on_message(message: discord.Message):
    """Every message the bot can see, sorted into what it means.

    Four early exits handle the sync channels the two bots talk through; each
    ignores the bot's own messages, because Guard posts into some of these
    channels itself and must not read its own traffic back.

    Then the ordinary path. Verification is tracked before moderation, so that
    a message which turns out to be spam still counts as the activity it was
    up to that point. In an unguarded channel the only remaining rule is the
    banned-link list, which deletes and never bans, and never touches an
    admin's message. In a guarded channel the message is deleted whatever it
    is, and only then is it examined for spam — the deletion is the rule, the
    ban is the consequence of spam.

    The ban sequence is DM first, then ban, then purge, then the tribunal
    case, then the log. The DM has to precede the ban because a banned user
    can no longer be messaged through the server, and it is best-effort: a
    closed DM must not stop the ban.
    """
    if message.channel.id in VERIFIED or message.channel.id in UNVERIFIED:
        if message.author.id != bot.user.id:
            await handle_verification_sync(message)
        return

    if message.channel.id in APPEAL_PARDON_CHANNELS.get("discord", set()):
        if message.author.id != bot.user.id:
            await handle_appeal_pardon(message)
        return

    if message.channel.id in APPEAL_BANINFO_CHANNELS.get("discord", set()):
        if message.author.id != bot.user.id:
            try:
                await handle_appeal_baninfo(message)
            except Exception:
                pass
        return

    if message.author.bot or message.webhook_id:
        return
    if not message.guild:
        return

    try:
        await handle_verification(message)
    except Exception:
        pass

    guard = db.get_guard(message.channel.id)
    if not guard:
        if (
            message_has_banned_link(message.content or "")
            and not is_admin(message.author.id, message.guild.id)
        ):
            try:
                await message.delete()
            except Exception:
                pass
        return

    try:
        await message.delete()
    except Exception:
        pass

    if not message_has_spam(message):
        return

    guild_row = db.get_guild(message.guild.id)
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG
    member = message.author
    reason = guard["reason"]
    duration_seconds = guard["duration_seconds"]

    custom_dm = db.get_custom_dm(message.guild.id) if guild_row else None
    if custom_dm:
        dm_text = custom_dm.replace("{server}", message.guild.name).replace("{reason}", reason)
    else:
        dm_text = localized("ban_dm", lang, server=message.guild.name, reason=reason)
    appeal = db.get_appeal(message.guild.id) if guild_row else None
    if appeal:
        dm_text = f"{dm_text}\n{appeal}"
    else:
        invite_line = _purgatorium_invite_line(message.guild.id, lang)
        if invite_line:
            dm_text = f"{dm_text}\n{invite_line}"
    try:
        await member.send(dm_text)
    except Exception:
        pass

    try:
        await message.guild.ban(member, reason=reason, delete_message_days=0)
    except Exception:
        return

    db.record_ban(message.guild.id, member.id)

    await _purge_recent_messages(message.guild, member.id)

    unban_at = None if duration_seconds is None else int(time.time()) + duration_seconds
    db.add_active_ban(message.guild.id, member.id, unban_at)

    try:
        await post_tribunal_case(message.guild, member, guild_row, reason, unban_at)
    except Exception as e:
        logger.error("tribunal post failed for %s: %s", member.id, e)

    if guild_row:
        log_channel_id = int(guild_row["log_channel_id"])
        log_channel = bot.get_channel(log_channel_id)
        if not log_channel:
            try:
                log_channel = await bot.fetch_channel(log_channel_id)
            except Exception:
                log_channel = None
        if log_channel:
            log_text = localized(
                "ban_log", lang,
                mention=member.mention,
                username=str(member),
                id=member.id
            )
            try:
                await log_channel.send(log_text)
            except Exception:
                pass
