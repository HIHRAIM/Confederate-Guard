"""Verification: who has earned the role, and every path by which they get it.

Deciding is cheap and lives in one place — a member who posted genuine
messages on two different calendar dates is verified. Everything else here is
delivery, because the verified set is shared across servers while the role is
not: the same person may be owed the role on five servers, on none of them, or
on one they have not written in for a year.

Four paths lead to `_grant_verify` and they are deliberately all the same
function: writing (handle_verification), joining (events.py: on_member_join),
a state change arriving from bridge_bot (handle_verification_sync via
propagate_verified_roles), and the silent start-up sweep
(backfill_verified_roles). `verify_grants` is what keeps them from announcing
the same person twice.

This module does not own the /setverify command — that is a per-server switch
and lives with the other ones in commands/settings.py.
"""
import discord

import db
from config import UNVERIFIED
from discord_bot.client import _acknowledge_message, _get_channel, bot
from utils import DEFAULT_LANG, SUPPORTED_LANGS, localized

async def _grant_verify(member: discord.Member, verify_row, guild_row, lang, cross_server: bool, announce: bool = True):
    """Give the verify role on this server, mark the grant, and (optionally) announce it.

    Marking the grant is what stops the same verification from being announced
    twice, so it happens even for silent grants (announce=False), which the
    startup backfill uses to hand out roles without flooding the channels.
    """
    guild = member.guild
    role = guild.get_role(int(verify_row["role_id"]))
    if role is not None and role not in member.roles:
        try:
            await member.add_roles(role, reason="verified")
        except Exception:
            pass

    db.add_verify_grant(guild.id, member.id)

    if not announce:
        return

    channel_id = verify_row["channel_id"]
    if not channel_id and guild_row:
        channel_id = guild_row["log_channel_id"]
    channel = await _get_channel(channel_id)
    if channel:
        key = "verify_announce_cross" if cross_server else "verify_announce"
        try:
            await channel.send(
                localized(key, lang, name=member.display_name, username=str(member), id=member.id)
            )
        except Exception:
            pass

async def propagate_verified_roles(uid, announce: bool, origin_guild_id=None):
    """Grant the verify role to a verified user on every guild where they are a
    present member and verification is enabled, skipping guilds where the grant
    was already recorded.

    This is the single place cross-server verification fans out. It fixes two
    problems: verified users now get their role on *all* their servers (not only
    where they joined/chatted), and announcements land on the servers where the
    user actually is — never on whichever guild happens to host the bridge sync
    channel. Recording the grant also prevents a later message from the user from
    triggering a duplicate 'verified' announcement.

    origin_guild_id is the server where the user actually verified: there the
    announcement uses the plain 'verified' text, everywhere else the
    cross-server one. An unknown origin (a sync message without a server id, or
    a verification that happened on Telegram) means plain text everywhere: on
    the server where the user really verified 'due to verification on another
    server' is simply false, and nowhere else does the parenthesis matter enough
    to guess.
    """
    try:
        origin = int(origin_guild_id) if origin_guild_id is not None else None
    except (TypeError, ValueError):
        origin = None

    for guild in bot.guilds:
        verify_row = db.get_verify(guild.id)
        if not verify_row:
            continue
        try:
            member = guild.get_member(int(uid))
        except (TypeError, ValueError):
            return
        if member is None or member.bot:
            continue
        if db.has_verify_grant(guild.id, member.id):
            continue
        guild_row = db.get_guild(guild.id)
        lang = guild_row["lang"] if guild_row and guild_row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
        cross = origin is not None and guild.id != origin
        try:
            await _grant_verify(member, verify_row, guild_row, lang, cross_server=cross, announce=announce)
        except Exception:
            pass

async def handle_verification(message: discord.Message):
    """Track activity dates and verify users who have chatted on more than one date."""
    guild = message.guild
    verify_row = db.get_verify(guild.id)
    if not verify_row:
        return

    if message.is_system():
        return

    member = message.author
    if db.has_verify_grant(guild.id, member.id):
        return

    guild_row = db.get_guild(guild.id)
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if db.is_verified(member.id):
        origin = db.get_verified_origin(member.id)
        await _grant_verify(
            member, verify_row, guild_row, lang,
            cross_server=(origin is not None and origin != guild.id)
        )
        return

    today = message.created_at.date().isoformat()
    first = db.get_first_seen(guild.id, member.id)
    if first is None:
        db.set_first_seen(guild.id, member.id, today)
        return

    if first != today:
        db.add_verified(member.id, guild.id)
        await _grant_verify(member, verify_row, guild_row, lang, cross_server=False)

async def handle_verification_sync(message: discord.Message):
    """Mirror bridge_bot's verification state changes into our database.

    A user ID posted to the VERIFIED channel means the user consented to
    message forwarding; one posted to the UNVERIFIED channel means the user
    unverified themselves. We add or remove the user from our cross-server
    verified database accordingly and acknowledge the processed message with a
    ✅ reaction (anyone may post there, not only bridge_bot).

    A second number on the VERIFIED line is the id of the server where the
    consent was actually given; bridge_bot sends it for Discord-side
    verifications. It is what keeps that server's announcement from claiming
    the verification happened somewhere else. Older senders post the id alone,
    which stays valid — the origin is then simply unknown.

    An id already in the verified database is answered with ☑️ instead of ✅.
    Both mean "read"; the second says nothing was changed, so a re-post that
    someone made expecting a retry is visibly a no-op rather than looking
    like a fresh verification.
    """
    parts = (message.content or "").split()
    if not parts or not parts[0].isdigit():
        return
    uid = int(parts[0])

    if message.channel.id in UNVERIFIED:
        db.remove_verified(uid)
        await _acknowledge_message(message)
        return

    origin = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

    if db.is_verified(uid):
        await _acknowledge_message(message, "☑️")
        return

    db.add_verified(uid, origin)
    await propagate_verified_roles(uid, announce=True, origin_guild_id=origin)
    await _acknowledge_message(message)

async def backfill_verified_roles(client: discord.Client):
    """One-time sweep on startup: hand the verify role to every already-verified
    member on every verification-enabled server they are present on.

    Cross-server verification used to only take effect when a verified user
    joined or spoke on another server, so members verified while already present
    elsewhere never received the role there. This grants it silently (no
    announcement) and records the grant, so it is idempotent across restarts.
    """
    await client.wait_until_ready()
    try:
        for guild in list(client.guilds):
            verify_row = db.get_verify(guild.id)
            if not verify_row:
                continue
            if guild.get_role(int(verify_row["role_id"])) is None:
                continue
            guild_row = db.get_guild(guild.id)
            lang = guild_row["lang"] if guild_row and guild_row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
            for member in list(guild.members):
                if member.bot:
                    continue
                if db.has_verify_grant(guild.id, member.id):
                    continue
                if not db.is_verified(member.id):
                    continue
                try:
                    await _grant_verify(member, verify_row, guild_row, lang, cross_server=True, announce=False)
                except Exception:
                    pass
    except Exception:
        pass
