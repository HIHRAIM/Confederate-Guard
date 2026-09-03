"""The tribunal: a human review of the bans a regular expression decided.

Every automatic spam-guard ban on a networked server is posted here as a case
file with two buttons. **Global ban** promotes it to a network ban;
**Ignore** retires the buttons. Bans on a server with no network are not
posted at all — the only button that does anything would have no network to
act on.

This module decides; bans.py delivers. The verdict writes the global_bans row
and then hands every actual ban to `enforce_global_ban`, which is the same
path /globalban takes, so a ban issued by a consul is indistinguishable from
one issued by a Bot Admin.

The buttons outlive the process. Their custom_ids are re-registered at
start-up by client.py from the open rows in tribunal_cases, and the case is
claimed in the database before anything is banned — that claim is what makes
two consuls pressing at the same instant produce one ban and one refusal.
"""
import logging
import time

import discord

import db
from config import TRIBUNAL_CHANNELS
from discord_bot.bans import enforce_global_ban
from discord_bot.client import _get_channel, bot
from discord_bot.purgatorium import _is_consul_or_admin
from utils import (
    DEFAULT_LANG, GLOBAL_BAN_MAX_SECONDS, SUPPORTED_LANGS, get_guild_lang, localized,
)

logger = logging.getLogger("guard.discord")

TRIBUNAL_BUTTON_TTL = 7 * 86400

def _tribunal_lang(channel):
    """The tribunal channel speaks the language of the server it sits on.

    Purgatorium is deliberately not registered with /setup, so a tribunal channel
    there falls back to English — which is what the consuls reading it share."""
    guild = getattr(channel, "guild", None)
    return get_guild_lang(guild.id) if guild is not None else DEFAULT_LANG

def _md(value):
    """Escape a name so its own punctuation cannot format the case text."""
    return discord.utils.escape_markdown(str(value or ""))

def _tribunal_text(guild, member, network, reason, banned_at, unban_at, lang):
    """The case as the consuls read it: one fact per line, timestamps rendered by
    Discord so everyone sees them in their own timezone."""
    created = int(member.created_at.timestamp()) if member.created_at else None
    until = (f"<t:{int(unban_at)}:F>" if unban_at
             else localized("tribunal_permanent", lang))
    return "\n".join([
        localized("tribunal_title", lang),
        localized("tribunal_user", lang, name=_md(member.display_name),
                  id=member.id, username=_md(str(member))),
        localized("tribunal_registered", lang,
                  date=(f"<t:{created}:F>" if created else "—")),
        localized("tribunal_banned_at", lang, date=f"<t:{int(banned_at)}:F>"),
        localized("tribunal_until", lang, date=until),
        localized("tribunal_server", lang, server=_md(guild.name),
                  id=guild.id, network=network),
        localized("tribunal_reason", lang, reason=_md(reason) or "—"),
    ])

class TribunalButton(discord.ui.Button):
    """One verdict button under a case."""

    def __init__(self, action, uid, lang):
        """Build the button for `action` ('globalban' or 'ignore').

        Only the destructive one is styled danger. The custom_id carries the
        action and the user because the view is rebuilt from the database
        after a restart; messages already in Discord reference this exact
        string, so its shape cannot change.
        """
        super().__init__(
            label=localized(f"tribunal_btn_{action}", lang),
            style=(discord.ButtonStyle.danger if action == "globalban"
                   else discord.ButtonStyle.secondary),
            custom_id=f"tribunal:{action}:{uid}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        """Hand the press to the shared handler, which owns the permission
        check and the race between two consuls."""
        await handle_tribunal_click(interaction, self.action)

class TribunalView(discord.ui.View):
    """'Global ban' / 'Ignore' under one automatic ban put up for review."""
    def __init__(self, uid, lang):
        """Both buttons, with no timeout — the case expires through the
        database sweep rather than through Discord, so that a restart does not
        reset the seven days."""
        super().__init__(timeout=None)
        self.add_item(TribunalButton("globalban", uid, lang))
        self.add_item(TribunalButton("ignore", uid, lang))

async def post_tribunal_case(guild, member, guild_row, reason, unban_at):
    """Put an automatic spam-guard ban before the tribunal channels.

    Only bans issued on a server that belongs to a network are posted: the
    'Global ban' button acts on that network, and without one there is nothing
    for it to do.
    """
    network = guild_row["network"] if guild_row else None
    if network is None:
        return
    banned_at = int(time.time())
    for channel_id in TRIBUNAL_CHANNELS.get("discord", set()):
        channel = await _get_channel(channel_id)
        if channel is None:
            continue
        lang = _tribunal_lang(channel)
        try:
            sent = await channel.send(
                _tribunal_text(guild, member, network, reason, banned_at, unban_at, lang),
                view=TribunalView(member.id, lang),
            )
        except Exception:
            continue
        db.add_tribunal_case(sent.id, channel.id, guild.id, network, member.id, reason, lang)

async def _tribunal_broadcast(uid, network, reason, unban_at, origin_guild_id, applied_guild_ids):
    """Announce the network ban in the log channels that need to know.

    Three groups: the server that issued the local ban, every server where the
    user is banned locally — including ones outside this network, because a local
    unban there while network bans are off would otherwise let the user walk back
    in unnoticed — and every server the ban has just reached.
    """
    targets = {int(origin_guild_id)}
    targets |= {int(row["guild_id"]) for row in db.get_user_active_bans(uid)}
    targets |= {int(gid) for gid in applied_guild_ids}

    for gid in sorted(targets):
        guild_row = db.get_guild(gid)
        if not guild_row or not guild_row["log_channel_id"]:
            continue
        channel = await _get_channel(guild_row["log_channel_id"])
        if channel is None:
            continue
        lang = guild_row["lang"] if guild_row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
        try:
            await channel.send(localized(
                "tribunal_network_log", lang,
                mention=f"<@{uid}>", id=uid, network=network,
                reason=reason or "—", until=f"<t:{int(unban_at)}:F>",
            ))
        except Exception:
            pass

async def _tribunal_apply_global_ban(case):
    """Turn a reviewed local ban into a network ban.

    The reason is the one the guarded channel banned for; the term is the 10
    years `/ban infinity` means, since a consul pressing this is deciding the
    user has no place in the network rather than setting a sentence. The origin
    server's own ban is stretched to match, so its local timer cannot quietly
    unban someone the network still bans.
    """
    uid = int(case["user_id"])
    network = case["network"]
    origin_guild_id = int(case["guild_id"])
    reason = case["reason"] or ""
    now = int(time.time())
    unban_at = now + GLOBAL_BAN_MAX_SECONDS

    db.add_global_ban(network, uid, reason, origin_guild_id, now, unban_at)
    db.add_active_ban(origin_guild_id, uid, unban_at)
    gban = db.get_global_ban(network, uid)

    applied = []
    for row in db.get_network_guilds(network):
        gid = int(row["guild_id"])
        if gid == origin_guild_id or not db.is_gbans_enabled(gid):
            continue
        other = bot.get_guild(gid)
        if other is None:
            continue
        member = other.get_member(uid)
        if member is None:
            continue
        other_lang = row["lang"] if row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
        try:
            if await enforce_global_ban(other, member, gban, row, other_lang):
                applied.append(gid)
        except Exception:
            pass

    await _tribunal_broadcast(uid, network, reason, unban_at, origin_guild_id, applied)

async def _close_tribunal_message(message, note):
    """Take the buttons off a decided case and record the outcome under it."""
    if message is None:
        return
    content = f"{message.content}\n\n{note}" if message.content else note
    try:
        await message.edit(content=content[:2000], view=None)
    except Exception:
        pass

async def handle_tribunal_click(interaction: discord.Interaction, action):
    """Decide a case: check it exists, check the presser, claim it, act.

    The order matters. The claim (`db.resolve_tribunal_case`) happens before
    any banning and before the message is edited, so a second consul pressing
    in the same instant is told the case is already decided instead of
    applying the verdict twice. The case's own stored language wins over the
    channel's, so a case keeps reading as it was written.

    A failure inside the global ban is logged rather than raised: the case is
    already decided at that point, and letting it bubble up would leave a
    closed case whose buttons the user is told never worked.
    """
    lang = _tribunal_lang(interaction.channel)
    case = db.get_tribunal_case(interaction.message.id)

    if case is None:
        await interaction.response.send_message(
            localized("tribunal_unknown_case", lang), ephemeral=True)
        return
    if not _is_consul_or_admin(interaction.user):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return
    if case["lang"] in SUPPORTED_LANGS:
        lang = case["lang"]
    if not db.resolve_tribunal_case(interaction.message.id, action):
        await interaction.response.send_message(
            localized("tribunal_already_resolved", lang), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    note_key = "tribunal_done_globalban" if action == "globalban" else "tribunal_done_ignore"
    await _close_tribunal_message(
        interaction.message, localized(note_key, lang, moderator=interaction.user.mention))

    if action == "globalban":
        try:
            await _tribunal_apply_global_ban(case)
        except Exception as e:
            logger.error("tribunal global ban failed for %s: %s", case["user_id"], e)

    try:
        await interaction.followup.send(localized("tribunal_ack", lang), ephemeral=True)
    except Exception:
        pass

async def _retire_stale_tribunal_cases(client: discord.Client):
    """Take the buttons off cases nobody acted on within the window.

    A case left open forever is a button that bans someone months after anyone
    remembers why, so the offer expires even though Discord itself would keep the
    components clickable indefinitely."""
    for case in db.get_stale_tribunal_cases(TRIBUNAL_BUTTON_TTL):
        db.resolve_tribunal_case(case["message_id"], "expired")
        channel = await _get_channel(case["channel_id"])
        if channel is None:
            continue
        try:
            message = await channel.fetch_message(int(case["message_id"]))
        except Exception:
            continue
        lang = case["lang"] if case["lang"] in SUPPORTED_LANGS else _tribunal_lang(channel)
        await _close_tribunal_message(message, localized("tribunal_expired", lang))
