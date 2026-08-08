"""Guard's half of the shared appeal system.

The appeal itself belongs to bridge_bot: it runs /appeal, opens the thread,
bridges it to the appellant's DM, anonymizes the consuls and posts the verdict
buttons. Guard never reads that thread. What Guard owns is everything that
touches bans — who may stay on the appeal server, what is known about a user's
bans, and the lifting of them.

The seam between the two bots is four Discord channels named in both configs
and nothing else: no shared database, no API, no imports. Three of them are
served here (pardon, ban-info, and the invitation that sends people to the
server in the first place); the fourth pair is the verification sync in
verification.py.

The gate's one-day ban is not implemented here — it is a ban, and bans are
delivered and lifted in bans.py. This module decides who gets one.
"""
import time
from datetime import datetime, timezone

import discord

import db
from config import (
    BRIDGE_BOT_ID, CONSULS, PURGATORIUM_GUILD_ID, PURGATORIUM_INVITE_URL,
)
from discord_bot.bans import (
    GATE_BAN_MARKER, _execute_global_unban, _find_registered_guild_ban,
)
from discord_bot.client import _acknowledge_message, _get_channel, bot
from utils import DEFAULT_LANG, SUPPORTED_LANGS, get_guild_lang, is_admin, localized

def _purgatorium_invite_line(guild_id, lang):
    """Localized Purgatorium invitation appended to ban DMs.

    Returns None when the server runs its own appeal system (a /setappeal text
    is configured) or when the ban happened on Purgatorium itself — bans there
    are not appealable through the bots.
    """
    if int(guild_id) == PURGATORIUM_GUILD_ID:
        return None
    if db.get_appeal(guild_id):
        return None
    return localized("purgatorium_invite_line", lang, invite=PURGATORIUM_INVITE_URL)

async def handle_purgatorium_join(member: discord.Member):
    """Gatekeeper for the shared appeal server.

    Consuls (appointed via /setconsul) are let in and handed the CONSULS
    role(s). Users with an active ban anywhere in guard_bot's database — or, if
    the database knows nothing, on the live ban list of any server registered
    via /setup — get a DM (in the language of the server that banned them)
    telling them to send /appeal to bridge_bot, with whom they now share a
    server. Everyone else — including users whose bans were already lifted — is
    silently banned for one day. That ban is deliberately kept out of the
    database: the unban moment is encoded in the audit-log reason and lifted by
    the unban_loop scan, so the gate leaves no stored trace of the visitor.
    """
    if is_admin(member.id, PURGATORIUM_GUILD_ID):
        return

    if db.is_consul(member.id):
        roles = [member.guild.get_role(int(rid)) for rid in CONSULS]
        roles = [r for r in roles if r is not None]
        if roles:
            try:
                await member.add_roles(*roles, reason="Purgatorium consul")
            except Exception:
                pass
        return

    global_bans = db.get_user_active_global_bans(member.id)
    local_bans = [
        b for b in db.get_user_active_bans(member.id)
        if int(b["guild_id"]) != PURGATORIUM_GUILD_ID
    ]

    if global_bans or local_bans:
        row = global_bans[0] if global_bans else local_bans[0]
        lang = row["lang"] if row["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
    else:
        lang = await _find_registered_guild_ban(member.id)

    if lang is not None:
        try:
            await member.send(
                localized("purgatorium_appeal_hint", lang, bridge_bot=f"<@{BRIDGE_BOT_ID}>")
            )
        except Exception:
            pass
        return

    try:
        await member.guild.ban(
            discord.Object(id=member.id),
            reason=(
                "Purgatorium gate: no active ban to appeal "
                f"{GATE_BAN_MARKER}{int(time.time()) + 86400}]"
            ),
            delete_message_days=0,
        )
    except Exception:
        return

async def handle_appeal_pardon(message: discord.Message):
    """Consume a consul verdict from the appeal-pardon sync channel.

    bridge_bot posts a bare user ID there when the consuls decide to lift the
    user's bans. The pardon clears everything: global (network) bans plus every
    local ban on every server, so the pardoned user never re-enters Purgatorium
    looking 'still banned' and gets invited to appeal again. Only bridge_bot
    and bot admins are trusted; the ✅ reaction acknowledges that the verdict
    was processed.
    """
    if message.author.id != BRIDGE_BOT_ID and not is_admin(message.author.id):
        return
    content = (message.content or "").strip()
    if not content.isdigit():
        return
    uid = int(content)

    for gban in db.get_user_active_global_bans(uid):
        try:
            await _execute_global_unban(gban["network"], uid)
        except Exception:
            pass

    for row in db.get_user_active_bans(uid):
        gid = int(row["guild_id"])
        guild_obj = bot.get_guild(gid)
        if guild_obj:
            try:
                await guild_obj.unban(discord.Object(id=uid), reason="Appeal granted by consuls")
            except Exception:
                pass
        db.remove_active_ban(gid, uid)
        db.remove_gban_enforcement(gid, uid)
    db.remove_user_ban_history(uid)

    await _acknowledge_message(message)

def _format_ts_date(ts):
    """A unix timestamp as a bare UTC date, or None if it is unusable.

    Plain text rather than Discord timestamp markup: these dates go into the
    ban summary, which consuls also read as quoted text and copy elsewhere.
    """
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None

async def _collect_user_ban_info(uid):
    """Everything guard_bot knows about the user's bans, per guild.

    Merges the live ban lists of every server the bot is on (reason from the
    ban entry, moderator and date from the audit log when readable) with the
    database: active local bans (end date), ban history (start date) and
    global bans (network, reason, origin). Purgatorium's own bans are not
    appealable and are left out. Returns {guild_id: info-dict}.
    """
    entries = {}

    def entry(gid, name=None):
        """Get or create this guild's entry, filling in the name when known."""
        gid = int(gid)
        e = entries.setdefault(gid, {"name": None})
        if name:
            e["name"] = name
        return e

    for guild in list(bot.guilds):
        if guild.id == PURGATORIUM_GUILD_ID:
            continue
        try:
            ban_entry = await guild.fetch_ban(discord.Object(id=uid))
        except Exception:
            continue
        e = entry(guild.id, guild.name)
        if ban_entry.reason:
            e.setdefault("reason", ban_entry.reason)
        try:
            async for log in guild.audit_logs(action=discord.AuditLogAction.ban, limit=50):
                if getattr(log.target, "id", None) == uid:
                    if log.user:
                        e.setdefault("moderator", str(log.user))
                    if log.created_at:
                        e.setdefault("banned_at", int(log.created_at.timestamp()))
                    break
        except Exception:
            pass

    for row in db.get_user_active_bans(uid):
        gid = int(row["guild_id"])
        if gid == PURGATORIUM_GUILD_ID:
            continue
        e = entry(gid)
        if row["unban_at"] is None:
            e["permanent"] = True
        else:
            e.setdefault("unban_at", int(row["unban_at"]))

    for row in db.get_user_ban_history(uid):
        gid = int(row["guild_id"])
        if gid == PURGATORIUM_GUILD_ID:
            continue
        if row["banned_at"]:
            entry(gid).setdefault("banned_at", int(row["banned_at"]))

    for row in db.get_user_global_bans(uid):
        e = entry(row["origin_guild_id"]) if row["origin_guild_id"] else None
        if e is None:
            continue
        e["network"] = row["network"]
        if row["reason"]:
            e.setdefault("reason", row["reason"])
        if row["banned_at"]:
            e.setdefault("banned_at", int(row["banned_at"]))
        if row["unban_at"]:
            e.setdefault("unban_at", int(row["unban_at"]))

    for gid, e in entries.items():
        if not e["name"]:
            guild = bot.get_guild(gid)
            e["name"] = guild.name if guild else str(gid)
    return entries

def _format_ban_info_entry(e, lang):
    """One server's line of the ban summary: name, then whatever is known.

    Every fact is optional — the sources disagree about what they can supply,
    and a ban issued by hand may yield nothing but the fact that it exists.
    That is what the 'no details' fallback is for: an entry with an empty
    parenthesis would read as a formatting bug rather than as an answer.
    """
    details = []
    if "network" in e:
        details.append(localized("baninfo_network", lang, network=e["network"]))
    date = _format_ts_date(e.get("banned_at")) if e.get("banned_at") else None
    if date:
        details.append(localized("baninfo_when", lang, date=date))
    if e.get("reason"):
        details.append(localized("baninfo_reason", lang, reason=e["reason"]))
    if e.get("moderator"):
        details.append(localized("baninfo_by", lang, moderator=e["moderator"]))
    if e.get("permanent"):
        details.append(localized("baninfo_permanent", lang))
    else:
        until = _format_ts_date(e.get("unban_at")) if e.get("unban_at") else None
        if until:
            details.append(localized("baninfo_until", lang, date=until))
    if not details:
        details.append(localized("baninfo_no_details", lang))
    return f"**{e['name']}** ({'; '.join(details)})"

def _is_consul_or_admin(user) -> bool:
    """Who may act on the buttons the bot puts in appeal threads and in the
    tribunal channel: bot admins, consuls appointed with /setconsul, and anyone
    holding one of the CONSULS roles where the interaction happened."""
    uid = getattr(user, "id", None)
    if uid is None:
        return False
    if is_admin(uid) or db.is_consul(uid):
        return True
    return any(role.id in CONSULS for role in getattr(user, "roles", None) or [])

def _user_ban_networks(uid):
    """Networks where this user currently holds a global ban, ascending."""
    return sorted({row["network"] for row in db.get_user_active_global_bans(uid)})

class NetworkPardonButton(discord.ui.Button):
    """One 'unban in network N' button, or the 'unban everywhere' one."""

    def __init__(self, uid, network, label):
        """Build the button. `network` None means every network at once.

        The custom_id carries the user and the network because the view is
        rebuilt from the database after a restart and has no other way to know
        what it acts on. Messages already posted in Discord reference this
        exact string — changing its shape silently breaks every pardon button
        still hanging in an appeal thread.
        """
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            custom_id=f"gpardon:{uid}:{'all' if network is None else network}",
        )
        self.uid = int(uid)
        self.network = network

    async def callback(self, interaction: discord.Interaction):
        """Hand the press to the shared handler, which does the permission
        check — the button itself is visible to everyone in the thread."""
        await handle_network_pardon_click(interaction, self.uid, self.network)

class NetworkPardonView(discord.ui.View):
    """Per-network unban controls under a ban summary in an appeal thread.

    The consuls' verdict buttons (bridge_bot's, on the pinned message) decide the
    appeal as a whole; these decide one network at a time, which is the finer
    tool the same conversation often needs — a user may be banned in several
    networks and deserve to come back to only one of them. Pressing one changes
    nothing about the appeal itself: the thread stays open until the consuls
    close it.

    The set of buttons is built from the user's *current* global bans every time
    the view is constructed, so neither a restart nor a second press can leave
    behind a button for a network whose ban is already gone.
    """
    def __init__(self, uid, networks, lang):
        """One button per network, capped at Discord's limit, plus an
        'all networks' button when there is more than one to choose from."""
        super().__init__(timeout=None)
        for network in list(networks)[:20]:
            self.add_item(NetworkPardonButton(
                uid, network, localized("pardon_btn_network", lang, network=network)))
        if len(networks) > 1:
            self.add_item(NetworkPardonButton(uid, None, localized("pardon_btn_all", lang)))

async def _refresh_pardon_view(message, uid, lang):
    """Rebuild a summary's buttons from what is still banned, or drop them."""
    if message is None:
        return
    networks = _user_ban_networks(uid)
    try:
        await message.edit(view=NetworkPardonView(uid, networks, lang) if networks else None)
    except Exception:
        pass

async def handle_network_pardon_click(interaction: discord.Interaction, uid, network):
    """Lift the user's global ban in one network, or in every network at once."""
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not _is_consul_or_admin(interaction.user):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    if network is None:
        networks = _user_ban_networks(uid)
    else:
        networks = [network] if db.get_active_global_ban(network, uid) else []

    if not networks:
        await _refresh_pardon_view(interaction.message, uid, lang)
        await interaction.followup.send(localized("pardon_none", lang), ephemeral=True)
        return

    for net in networks:
        try:
            await _execute_global_unban(net, uid)
        except Exception:
            pass

    await _refresh_pardon_view(interaction.message, uid, lang)

    key = "pardon_done_all" if network is None else "pardon_done_network"
    try:
        await interaction.channel.send(
            localized(key, lang, id=uid, network=network, moderator=interaction.user.mention)
        )
    except Exception:
        pass
    await interaction.followup.send(localized("pardon_applied", lang), ephemeral=True)

async def handle_appeal_baninfo(message: discord.Message):
    """Answer bridge_bot's '<user_id> <thread_id>' post from the ban-info sync
    channel: publish the user's known bans across every server of the bot into
    the appeal thread (entries separated by an interpunct) and acknowledge the
    processed message with a ✅ reaction so bridge_bot can pin the summary.

    The last message of the summary carries the per-network unban buttons, so the
    consuls decide next to the evidence they are deciding on."""
    if message.author.id != BRIDGE_BOT_ID and not is_admin(message.author.id):
        return
    parts = (message.content or "").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return
    uid, thread_id = int(parts[0]), int(parts[1])

    thread = bot.get_channel(thread_id)
    if thread is None:
        try:
            thread = await bot.fetch_channel(thread_id)
        except Exception:
            return

    lang = get_guild_lang(PURGATORIUM_GUILD_ID)
    entries = await _collect_user_ban_info(uid)

    if not entries:
        texts = [localized("baninfo_none", lang, id=uid)]
    else:
        texts = [localized("baninfo_header", lang, id=uid)]
        line = ""
        for e in entries.values():
            part = _format_ban_info_entry(e, lang)
            if line and len(line) + len(part) + 3 > 1900:
                texts.append(line)
                line = part
            else:
                line = f"{line} · {part}" if line else part
        if line:
            texts.append(line)

    networks = _user_ban_networks(uid)
    view = NetworkPardonView(uid, networks, lang) if networks else None
    sent = None
    try:
        for index, text in enumerate(texts):
            with_view = view is not None and index == len(texts) - 1
            sent = await thread.send(text[:2000], **({"view": view} if with_view else {}))
    except Exception:
        return

    if sent is not None and view is not None:
        db.add_baninfo_post(sent.id, thread.id, uid)

    await _acknowledge_message(message)
