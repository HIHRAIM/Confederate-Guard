import discord
from discord import app_commands
import asyncio
import io
import os
import secrets
import time
import traceback
from datetime import timedelta, datetime, timezone
import db
import utils
from config import (
    VERIFIED, UNVERIFIED, SUPPORT_CHATS,
    PURGATORIUM_GUILD_ID, PURGATORIUM_INVITE_URL, BRIDGE_BOT_ID, APPEAL_PARDON_CHANNELS,
    APPEAL_BANINFO_CHANNELS, CONSULS, TRIBUNAL_CHANNELS,
)
from utils import (
    is_admin, get_guild_lang, localized,
    parse_duration, parse_global_duration, format_duration, message_has_spam,
    classify_banned_link, message_has_banned_link,
    language_name, available_locales, locale_stats, locale_bar, compare_reply,
    LANG_ORDER, LOCALE_STATUS_EMOJI, GLOBAL_BAN_MAX_SECONDS,
    SUPPORTED_LANGS, DEFAULT_LANG
)

class GuardBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        asyncio.create_task(unban_loop(self))
        asyncio.create_task(status_loop(self))
        asyncio.create_task(backfill_verified_roles(self))
        self._restore_persistent_views()

    def _restore_persistent_views(self):
        """Re-arm the buttons the bot left on messages before it was restarted.

        Both sets are bound to their message, since their custom_ids carry the
        user (and network) they act on rather than being one fixed shape. The
        pardon buttons are rebuilt from what is banned *now*, so a ban lifted
        while the bot was down does not come back as a live button."""
        try:
            for case in db.get_open_tribunal_cases():
                lang = case["lang"] if case["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
                self.add_view(TribunalView(int(case["user_id"]), lang),
                              message_id=int(case["message_id"]))
        except Exception as e:
            print(f"[ERROR] restoring tribunal views: {e}", flush=True)
        try:
            lang = get_guild_lang(PURGATORIUM_GUILD_ID)
            for row in db.get_baninfo_posts(max_age_seconds=90 * 86400):
                uid = int(row["user_id"])
                networks = _user_ban_networks(uid)
                if networks:
                    self.add_view(NetworkPardonView(uid, networks, lang),
                                  message_id=int(row["message_id"]))
        except Exception as e:
            print(f"[ERROR] restoring pardon views: {e}", flush=True)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

bot = GuardBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    tb = traceback.format_exc()
    print(f"[ERROR] Command '{interaction.command.name if interaction.command else '?'}': {error}\n{tb}")
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    msg = localized("internal_error", lang, error=error)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass

@bot.tree.command(name="setup", description="register the server: language, log channel, network")
@app_commands.describe(
    lang="Language code (ru, uk, pl, en, es, pt)",
    channel_id="ID of the channel for ban logs",
    network="Network ID (number) to group servers together"
)
async def setup_cmd(interaction: discord.Interaction, lang: str, channel_id: str, network: int = None):
    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", "en"), ephemeral=True
        )
        return

    lang = lang.lower().strip()
    if lang not in SUPPORTED_LANGS:
        await interaction.response.send_message(
            localized("setup_unknown_lang", "en", lang=lang, supported=", ".join(sorted(SUPPORTED_LANGS))),
            ephemeral=True
        )
        return

    try:
        cid = int(channel_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("setup_invalid_channel", "en"), ephemeral=True
        )
        return

    db.setup_guild(interaction.guild.id, lang, cid, network)
    if network is not None:
        await interaction.response.send_message(
            localized("setup_success_network", lang, lang=lang, channel_id=cid, network=network)
        )
    else:
        await interaction.response.send_message(
            localized("setup_success", lang, lang=lang, channel_id=cid)
        )

@bot.tree.command(name="guard", description="enable spam guard on this channel")
@app_commands.describe(
    duration="Ban duration (e.g. 30m, 2h, 1d, infinity)",
    reason="Reason shown in the ban"
)
async def guard_cmd(interaction: discord.Interaction, duration: str, reason: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if not guild_row:
        await interaction.response.send_message(
            localized("guard_no_setup", DEFAULT_LANG), ephemeral=True
        )
        return

    try:
        seconds = parse_duration(duration)
    except ValueError:
        await interaction.response.send_message(
            localized("duration_invalid", lang), ephemeral=True
        )
        return

    if db.get_guard(interaction.channel.id):
        await interaction.response.send_message(
            localized("guard_already", lang), ephemeral=True
        )
        return

    db.set_guard(interaction.channel.id, interaction.guild.id, seconds, reason)
    await interaction.response.send_message(
        localized("guard_enabled", lang,
                  duration=format_duration(seconds, lang),
                  reason=reason)
    )

@bot.tree.command(name="dm", description="set the DM text sent before a ban ({{server}} — server name)")
@app_commands.describe(text="Message text (use {server} for server name)")
async def dm_cmd(interaction: discord.Interaction, text: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if not guild_row:
        await interaction.response.send_message(
            localized("dm_no_setup", DEFAULT_LANG), ephemeral=True
        )
        return

    db.set_custom_dm(interaction.guild.id, text)
    await interaction.response.send_message(localized("dm_set", lang))

@bot.tree.command(name="autorole", description="automatically assign a role to all members")
@app_commands.describe(role_id="ID of the role to assign automatically")
async def autorole_cmd(interaction: discord.Interaction, role_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if not guild_row:
        await interaction.response.send_message(
            localized("autorole_no_setup", DEFAULT_LANG), ephemeral=True
        )
        return

    try:
        rid = int(role_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("autorole_invalid_role", lang, role_id=role_id), ephemeral=True
        )
        return

    role = interaction.guild.get_role(rid)
    if role is None:
        await interaction.response.send_message(
            localized("autorole_invalid_role", lang, role_id=rid), ephemeral=True
        )
        return

    db.set_autorole(interaction.guild.id, rid)
    await interaction.response.send_message(
        localized("autorole_set", lang, role_id=rid)
    )

    count = 0
    for member in interaction.guild.members:
        if member.bot:
            continue
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="autorole")
                count += 1
            except Exception:
                pass

    await interaction.followup.send(localized("autorole_done", lang, count=count))

async def _get_channel(channel_id):
    if channel_id is None:
        return None
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            ch = None
    return ch

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
                localized(key, lang, mention=member.mention, username=str(member), id=member.id)
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

    if not db.is_verified(uid):
        db.add_verified(uid, origin)
        await propagate_verified_roles(uid, announce=True, origin_guild_id=origin)
    await _acknowledge_message(message)

async def _acknowledge_message(message: discord.Message):
    """React with ✅ to mark a sync-channel message as processed."""
    try:
        await message.add_reaction("✅")
    except Exception:
        pass

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

GATE_BAN_MARKER = "[gate-unban:"

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
    def __init__(self, uid, network, label):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            custom_id=f"gpardon:{uid}:{'all' if network is None else network}",
        )
        self.uid = int(uid)
        self.network = network

    async def callback(self, interaction: discord.Interaction):
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
    def __init__(self, action, uid, lang):
        super().__init__(
            label=localized(f"tribunal_btn_{action}", lang),
            style=(discord.ButtonStyle.danger if action == "globalban"
                   else discord.ButtonStyle.secondary),
            custom_id=f"tribunal:{action}:{uid}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await handle_tribunal_click(interaction, self.action)

class TribunalView(discord.ui.View):
    """'Global ban' / 'Ignore' under one automatic ban put up for review."""
    def __init__(self, uid, lang):
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
            print(f"[ERROR] tribunal global ban failed for {case['user_id']}: {e}", flush=True)

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

@bot.tree.command(name="setverify", description="give a role to members who wrote on more than one day (shared cross-server database)")
@app_commands.describe(
    role_id="ID of the role to give verified users",
    channel_id="ID of the announcement channel (optional, defaults to the /setup log channel)"
)
async def setverify_cmd(interaction: discord.Interaction, role_id: str, channel_id: str = None):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if not guild_row:
        await interaction.response.send_message(
            localized("verify_no_setup", DEFAULT_LANG), ephemeral=True
        )
        return

    try:
        rid = int(role_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("verify_invalid_role", lang, role_id=role_id), ephemeral=True
        )
        return

    role = interaction.guild.get_role(rid)
    if role is None:
        await interaction.response.send_message(
            localized("verify_invalid_role", lang, role_id=rid), ephemeral=True
        )
        return

    cid = None
    if channel_id is not None and channel_id.strip():
        try:
            cid = int(channel_id.strip())
        except ValueError:
            await interaction.response.send_message(
                localized("verify_invalid_channel", lang), ephemeral=True
            )
            return

    db.set_verify(interaction.guild.id, rid, cid)
    if cid is not None:
        await interaction.response.send_message(
            localized("verify_set", lang, role_id=rid, channel_id=cid)
        )
    else:
        await interaction.response.send_message(
            localized("verify_set_no_channel", lang, role_id=rid)
        )

@bot.tree.command(name="ban", description="ban a user by ID")
@app_commands.describe(
    user_id="ID of the user to ban",
    duration="Duration: 1h, 1d, 2m (months), 3y (years), or infinity (10 years); max 10 years",
    reason="Reason for the ban"
)
async def ban_cmd(interaction: discord.Interaction, user_id: str, duration: str, reason: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("ban_cmd_invalid_id", lang), ephemeral=True
        )
        return

    try:
        seconds = parse_global_duration(duration)
    except ValueError:
        await interaction.response.send_message(
            localized("global_duration_invalid", lang), ephemeral=True
        )
        return

    dur_str = format_duration(seconds, lang)

    invite_line = _purgatorium_invite_line(interaction.guild.id, lang)
    if invite_line:
        try:
            target = interaction.guild.get_member(uid) or await bot.fetch_user(uid)
            if target:
                await target.send(
                    localized("ban_invite_dm", lang,
                              server=interaction.guild.name, duration=dur_str, reason=reason)
                    + f"\n{invite_line}"
                )
        except Exception:
            pass

    try:
        await interaction.guild.ban(discord.Object(id=uid), reason=reason, delete_message_days=0)
    except Exception as e:
        await interaction.response.send_message(
            localized("ban_cmd_failed", lang, user_id=uid, error=str(e)), ephemeral=True
        )
        return

    db.record_ban(interaction.guild.id, uid)

    if seconds is not None:
        db.add_active_ban(interaction.guild.id, uid, int(time.time()) + seconds)
    await interaction.response.send_message(
        localized("ban_cmd_success", lang, user_id=uid, duration=dur_str, reason=reason)
    )

    await _purge_recent_messages(interaction.guild, uid)

    if guild_row:
        log_channel_id = int(guild_row["log_channel_id"])
        log_channel = bot.get_channel(log_channel_id)
        if not log_channel:
            try:
                log_channel = await bot.fetch_channel(log_channel_id)
            except Exception:
                log_channel = None
        if log_channel:
            try:
                await log_channel.send(
                    localized("ban_cmd_log", lang,
                              user_id=uid,
                              admin=str(interaction.user),
                              duration=dur_str,
                              reason=reason)
                )
            except Exception:
                pass

@bot.tree.command(name="globalban", description="ban on this server and across the whole network (duration: 1h, 2m=months, 3y=years, infinity=10 y...")
@app_commands.describe(
    user_id="ID of the user to ban",
    reason="Reason for the ban",
    duration="Duration: 1h, 1d, 2m (months), 3y (years), or infinity (10 years); max 10 years",
)
async def globalban_cmd(interaction: discord.Interaction, user_id: str, reason: str, duration: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(localized("ban_cmd_invalid_id", lang), ephemeral=True)
        return

    try:
        seconds = parse_global_duration(duration)
    except ValueError:
        await interaction.response.send_message(localized("global_duration_invalid", lang), ephemeral=True)
        return

    if not guild_row or guild_row["network"] is None:
        await interaction.response.send_message(localized("globalban_no_network", lang), ephemeral=True)
        return

    network = guild_row["network"]
    now = int(time.time())
    unban_at = now + seconds

    try:
        target = interaction.guild.get_member(uid) or await bot.fetch_user(uid)
        if target:
            dm_text = localized("globalban_dm", lang, server=interaction.guild.name,
                                reason=reason, remaining=f"<t:{unban_at}:R>")
            invite_line = _purgatorium_invite_line(interaction.guild.id, lang)
            if invite_line:
                dm_text = f"{dm_text}\n{invite_line}"
            await target.send(dm_text)
    except Exception:
        pass

    try:
        await interaction.guild.ban(discord.Object(id=uid), reason=reason, delete_message_days=0)
    except Exception as e:
        await interaction.response.send_message(
            localized("ban_cmd_failed", lang, user_id=uid, error=str(e)), ephemeral=True
        )
        return

    db.add_active_ban(interaction.guild.id, uid, unban_at)
    db.record_ban(interaction.guild.id, uid)
    db.add_global_ban(network, uid, reason, interaction.guild.id, now, unban_at)

    await interaction.response.send_message(
        localized("globalban_success", lang,
                  user_id=uid, network=network, reason=reason, unban=f"<t:{unban_at}:F>")
    )

    await _purge_recent_messages(interaction.guild, uid)

    if guild_row["log_channel_id"]:
        log_channel = await _get_channel(guild_row["log_channel_id"])
        if log_channel:
            try:
                await log_channel.send(
                    localized("globalban_log", lang,
                              user_id=uid, network=network, admin=str(interaction.user),
                              reason=reason, unban=f"<t:{unban_at}:F>")
                )
            except Exception:
                pass

    gban = db.get_global_ban(network, uid)
    for g in db.get_network_guilds(network):
        gid = int(g["guild_id"])
        if gid == interaction.guild.id or not db.is_gbans_enabled(gid):
            continue
        other_guild = bot.get_guild(gid)
        if not other_guild:
            continue
        member = other_guild.get_member(uid)
        if member is None:
            continue
        other_lang = g["lang"] if g["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
        try:
            await enforce_global_ban(other_guild, member, gban, g, other_lang)
        except Exception:
            pass

@bot.tree.command(name="globalunban", description="lift a global ban and unban across the network")
@app_commands.describe(user_id="ID of the user to globally unban")
async def globalunban_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(localized("ban_cmd_invalid_id", lang), ephemeral=True)
        return

    if not guild_row or guild_row["network"] is None:
        await interaction.response.send_message(localized("globalban_no_network", lang), ephemeral=True)
        return

    network = guild_row["network"]
    if not db.get_global_ban(network, uid):
        await interaction.response.send_message(
            localized("globalunban_not_banned", lang, user_id=uid), ephemeral=True
        )
        return

    count = await _execute_global_unban(network, uid)

    await interaction.response.send_message(
        localized("globalunban_success", lang, user_id=uid, count=count)
    )

@bot.tree.command(name="unban", description="unban a user by ID on this server")
@app_commands.describe(user_id="ID of the user to unban")
async def unban_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("ban_cmd_invalid_id", lang), ephemeral=True
        )
        return

    banned_on_discord = True
    try:
        await interaction.guild.unban(discord.Object(id=uid))
    except discord.NotFound:
        banned_on_discord = False
    except Exception as e:
        await interaction.response.send_message(
            localized("unban_cmd_failed", lang, user_id=uid, error=str(e)), ephemeral=True
        )
        return

    db.remove_active_ban(interaction.guild.id, uid)
    db.remove_gban_enforcement(interaction.guild.id, uid)
    db.remove_ban_history(interaction.guild.id, uid)

    if not banned_on_discord:
        await interaction.response.send_message(
            localized("unban_cmd_not_banned", lang, user_id=uid), ephemeral=True
        )
        return

    await interaction.response.send_message(
        localized("unban_cmd_success", lang, user_id=uid)
    )

    if guild_row and guild_row["log_channel_id"]:
        log_channel = await _get_channel(guild_row["log_channel_id"])
        if log_channel:
            try:
                await log_channel.send(
                    localized("unban_cmd_log", lang, user_id=uid, admin=str(interaction.user))
                )
            except Exception:
                pass

@bot.tree.command(name="setgbans", description="enable/disable enforcement of network bans on this server")
@app_commands.describe(mode="enable or disable")
async def setgbans_cmd(interaction: discord.Interaction, mode: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return

    mode = mode.lower().strip()
    if mode not in ("enable", "disable"):
        await interaction.response.send_message(localized("setgbans_usage", lang), ephemeral=True)
        return

    if not guild_row:
        await interaction.response.send_message(localized("guard_no_setup", DEFAULT_LANG), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    if mode == "enable":
        db.set_gbans_enabled(interaction.guild.id, True)
        network = guild_row["network"]
        gbans = {}
        if network is not None:
            gbans = {str(g["user_id"]): g for g in db.get_active_global_bans_for_network(network)}
            for member in list(interaction.guild.members):
                if member.bot:
                    continue
                gban = gbans.get(str(member.id))
                if not gban:
                    continue
                try:
                    await enforce_global_ban(interaction.guild, member, gban, guild_row, lang)
                except Exception:
                    pass
        await interaction.followup.send(localized("setgbans_enabled", lang, count=len(gbans)))
    else:
        db.set_gbans_enabled(interaction.guild.id, False)
        count = 0
        for uid_str in db.get_gban_enforcements(interaction.guild.id):
            try:
                await interaction.guild.unban(discord.Object(id=int(uid_str)))
            except Exception:
                pass
            db.remove_active_ban(interaction.guild.id, uid_str)
            count += 1
        db.clear_gban_enforcements(interaction.guild.id)
        await interaction.followup.send(localized("setgbans_disabled", lang, count=count))

@bot.tree.command(name="setappeal", description="text appended on a new line to the ban message")
@app_commands.describe(text="Appeal text appended to the spam ban message")
async def setappeal_cmd(interaction: discord.Interaction, text: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return

    if not guild_row:
        await interaction.response.send_message(localized("appeal_no_setup", DEFAULT_LANG), ephemeral=True)
        return

    db.set_appeal(interaction.guild.id, text)
    await interaction.response.send_message(localized("appeal_set", lang))

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
        guild_row = db.get_guild(member.guild.id)
        lang = guild_row["lang"] if guild_row else DEFAULT_LANG
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
        print(f"[ERROR] tribunal post failed for {member.id}: {e}", flush=True)

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

@bot.tree.command(name="setadmin", description="add a server admin")
@app_commands.describe(user_id="ID of the user to grant admin rights on this server")
async def setadmin_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("setadmin_invalid_id", lang), ephemeral=True
        )
        return

    if db.is_guild_admin(interaction.guild.id, uid):
        await interaction.response.send_message(
            localized("setadmin_already", lang, user_id=uid), ephemeral=True
        )
        return

    db.add_guild_admin(interaction.guild.id, uid)
    await interaction.response.send_message(
        localized("setadmin_success", lang, user_id=uid)
    )

    try:
        user = interaction.guild.get_member(uid) or await bot.fetch_user(uid)
        if user:
            await user.send(localized("setadmin_dm", lang, server=interaction.guild.name))
    except Exception:
        pass

@bot.tree.command(name="remadmin", description="remove a user's server admin status")
@app_commands.describe(user_id="ID of the user to revoke admin rights on this server")
async def remadmin_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("setadmin_invalid_id", lang), ephemeral=True
        )
        return

    if not db.is_guild_admin(interaction.guild.id, uid):
        await interaction.response.send_message(
            localized("remadmin_not_admin", lang, user_id=uid), ephemeral=True
        )
        return

    db.remove_guild_admin(interaction.guild.id, uid)
    await interaction.response.send_message(
        localized("remadmin_success", lang, user_id=uid)
    )

async def _set_consul_roles(uid, grant: bool):
    """Grant or take away the CONSULS role(s) on Purgatorium if the user is
    there. Returns silently when the guild, member or roles are unavailable."""
    purg = bot.get_guild(PURGATORIUM_GUILD_ID)
    if purg is None:
        return
    member = purg.get_member(uid)
    if member is None:
        return
    roles = [purg.get_role(int(rid)) for rid in CONSULS]
    roles = [r for r in roles if r is not None]
    if not roles:
        return
    try:
        if grant:
            await member.add_roles(*roles, reason="Purgatorium consul")
        else:
            await member.remove_roles(*roles, reason="Purgatorium consul dismissed")
    except Exception:
        pass

@bot.tree.command(name="setconsul", description="appoint an appeal-server consul (bot admins)")
@app_commands.describe(user_id="ID of the user to appoint as consul")
async def setconsul_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("setadmin_invalid_id", lang), ephemeral=True
        )
        return

    if db.is_consul(uid):
        await interaction.response.send_message(
            localized("setconsul_already", lang, user_id=uid), ephemeral=True
        )
        return

    db.add_consul(uid, interaction.user.id)
    await _set_consul_roles(uid, grant=True)
    await interaction.response.send_message(
        localized("setconsul_success", lang, user_id=uid)
    )

@bot.tree.command(name="remconsul", description="dismiss an appeal-server consul (bot admins)")
@app_commands.describe(user_id="ID of the consul to dismiss")
async def remconsul_cmd(interaction: discord.Interaction, user_id: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("setadmin_invalid_id", lang), ephemeral=True
        )
        return

    if not db.remove_consul(uid):
        await interaction.response.send_message(
            localized("remconsul_not_consul", lang, user_id=uid), ephemeral=True
        )
        return

    await _set_consul_roles(uid, grant=False)
    await interaction.response.send_message(
        localized("remconsul_success", lang, user_id=uid)
    )

async def _resolve_user_ref(guild, identifier):
    """Resolve a ping, raw ID or username to a user id (None when unknown)."""
    identifier = identifier.strip()
    if identifier.startswith("<@") and identifier.endswith(">"):
        nums = "".join(ch for ch in identifier if ch.isdigit())
        return int(nums) if nums else None
    if identifier.isdigit():
        return int(identifier)
    if guild is not None:
        name = identifier.lstrip("@").casefold()
        for m in guild.members:
            if m.name.casefold() == name or (m.display_name or "").casefold() == name:
                return m.id
        try:
            async for m in guild.fetch_members(limit=1000):
                if m.name.casefold() == name or (m.display_name or "").casefold() == name:
                    return m.id
        except Exception:
            pass
    return None

@bot.tree.command(name="localizer-add", description="grant Localizer status: lets the user edit this bot's localization in the control panel")
@app_commands.describe(user="User to make a localizer: ping, ID or username")
async def localizer_add_cmd(interaction: discord.Interaction, user: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    uid = await _resolve_user_ref(interaction.guild, user)
    if uid is None:
        await interaction.response.send_message(
            localized("could_not_resolve_user", lang), ephemeral=True
        )
        return

    if db.is_localizer("discord", uid):
        await interaction.response.send_message(
            localized("localizer_add_already", lang, user_id=uid), ephemeral=True
        )
        return

    username = None
    member = None
    try:
        member = (interaction.guild.get_member(uid) if interaction.guild else None) \
            or await bot.fetch_user(uid)
        username = getattr(member, "name", None)
    except Exception:
        pass
    db.add_localizer("discord", uid, username=username, added_by=interaction.user.id)
    await interaction.response.send_message(
        localized("localizer_add_done", lang, user_id=uid)
    )
    try:
        if member:
            await member.send(localized("localizer_add_dm", lang))
    except Exception:
        pass

@bot.tree.command(name="localizer-rem", description="revoke a delegated Localizer status")
@app_commands.describe(user="User to demote: ping, ID or username")
async def localizer_rem_cmd(interaction: discord.Interaction, user: str):
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    uid = await _resolve_user_ref(interaction.guild, user)
    if uid is None:
        await interaction.response.send_message(
            localized("could_not_resolve_user", lang), ephemeral=True
        )
        return

    if not db.remove_localizer("discord", uid):
        await interaction.response.send_message(
            localized("localizer_rem_not", lang, user_id=uid), ephemeral=True
        )
        return

    await interaction.response.send_message(
        localized("localizer_rem_done", lang, user_id=uid)
    )

@bot.tree.command(name="banlink", description="add a link or invite code to the banned list")
@app_commands.describe(link="A URL (evil.com) or a Discord invite code/link (fXaxuYdN or discord.gg/fXaxuYdN)")
async def banlink_cmd(interaction: discord.Interaction, link: str):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    kind, value = classify_banned_link(link)
    if not db.add_banned_link(kind, value):
        await interaction.response.send_message(
            localized("banlink_exists", lang), ephemeral=True
        )
        return

    if kind == "invite":
        await interaction.response.send_message(
            localized("banlink_added_invite", lang, code=value), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            localized("banlink_added_url", lang, url=value), ephemeral=True
        )

@bot.tree.command(name="unbanlink", description="remove a link from the banned list (number shown in /links)")
@app_commands.describe(link_id="Number of the link as shown in /links")
async def unbanlink_cmd(interaction: discord.Interaction, link_id: int):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if db.remove_banned_link(link_id):
        await interaction.response.send_message(
            localized("unbanlink_success", lang, link_id=link_id), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            localized("unbanlink_not_found", lang, link_id=link_id), ephemeral=True
        )

LINKS_PAGE_SIZE = 10

def _links_embed(lang, rows, page, pages):
    lines = []
    for r in rows[page * LINKS_PAGE_SIZE:(page + 1) * LINKS_PAGE_SIZE]:
        kind_key = "links_kind_invite" if r["kind"] == "invite" else "links_kind_url"
        lines.append(f"{r['id']}. {localized(kind_key, lang)}: `{r['value']}`")
    embed = discord.Embed(
        title=localized("links_title", lang),
        description="\n".join(lines),
        color=discord.Color.blurple()
    )
    if pages > 1:
        embed.set_footer(text=localized("links_page", lang, page=page + 1, pages=pages))
    return embed

class LinksView(discord.ui.View):
    def __init__(self, lang, rows):
        super().__init__(timeout=600)
        self.lang = lang
        self.rows = rows
        self.page = 0
        self.pages = (len(rows) + LINKS_PAGE_SIZE - 1) // LINKS_PAGE_SIZE
        self._update_buttons()

    def _update_buttons(self):
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.pages - 1

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_links_embed(self.lang, self.rows, self.page, self.pages), view=self
        )

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_links_embed(self.lang, self.rows, self.page, self.pages), view=self
        )

@bot.tree.command(name="links", description="list of banned links")
async def links_cmd(interaction: discord.Interaction):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    rows = db.get_banned_links()
    if not rows:
        await interaction.response.send_message(
            localized("links_empty", lang), ephemeral=True
        )
        return

    pages = (len(rows) + LINKS_PAGE_SIZE - 1) // LINKS_PAGE_SIZE
    embed = _links_embed(lang, rows, 0, pages)
    if pages > 1:
        await interaction.response.send_message(embed=embed, view=LinksView(lang, rows), ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="show this command list")
async def help_cmd(interaction: discord.Interaction):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    everyone_lines = "\n".join([
        localized("help_cmd_help", lang),
        localized("help_cmd_locale", lang),
        localized("help_cmd_loc_compare", lang),
        localized("help_cmd_loc_suggest", lang),
    ])

    server_admin_lines = "\n".join([
        localized("help_cmd_setup", lang),
        localized("help_cmd_guard", lang),
        localized("help_cmd_dm", lang),
        localized("help_cmd_autorole", lang),
        localized("help_cmd_setverify", lang),
        localized("help_cmd_ban", lang),
        localized("help_cmd_unban", lang),
        localized("help_cmd_setgbans", lang),
        localized("help_cmd_setappeal", lang),
        localized("help_cmd_links", lang),
    ])

    bot_admin_lines = "\n".join([
        localized("help_cmd_globalban", lang),
        localized("help_cmd_globalunban", lang),
        localized("help_cmd_banlink", lang),
        localized("help_cmd_unbanlink", lang),
        localized("help_cmd_setadmin", lang),
        localized("help_cmd_remadmin", lang),
        localized("help_cmd_setconsul", lang),
        localized("help_cmd_remconsul", lang),
        localized("help_cmd_localizer_add", lang),
        localized("help_cmd_localizer_rem", lang),
        localized("help_cmd_backup", lang),
        localized("help_cmd_list_chats", lang),
        localized("help_cmd_force_leave", lang),
        localized("help_cmd_loc_reply", lang),
    ])

    embed = discord.Embed(
        title=localized("help_title", lang),
        color=discord.Color.blurple()
    )
    embed.add_field(name=localized("help_section_everyone", lang), value=everyone_lines, inline=False)
    embed.add_field(name=localized("help_section_server_admins", lang), value=server_admin_lines, inline=False)
    embed.add_field(name=localized("help_section_bot_admins", lang), value=bot_admin_lines, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="list_chats", description="list of servers the bot is in")
async def list_chats_cmd(interaction: discord.Interaction):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    lines = [localized("list_chats_header", lang)]
    for g in bot.guilds:
        lines.append(f"- {g.name} — id: {g.id}")

    msg = "\n".join(lines)

    if len(msg) > 1900:
        import io
        bio = io.BytesIO(msg.encode("utf-8"))
        bio.seek(0)
        await interaction.response.send_message(
            localized("list_chats_too_long", lang), ephemeral=True
        )
        await interaction.followup.send(
            file=discord.File(bio, filename="chat_list.txt"), ephemeral=True
        )
    else:
        await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="force_leave", description="make the bot leave a server")
@app_commands.describe(server_id="ID of the server the bot should leave")
async def force_leave_cmd(interaction: discord.Interaction, server_id: str):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    try:
        gid = int(server_id.strip())
    except ValueError:
        await interaction.response.send_message(
            localized("force_leave_invalid_id", lang), ephemeral=True
        )
        return

    guild = bot.get_guild(gid)
    if not guild:
        await interaction.response.send_message(
            localized("force_leave_not_member", lang), ephemeral=True
        )
        return

    try:
        await guild.leave()
    except Exception as e:
        await interaction.response.send_message(
            localized("force_leave_failed", lang, error=e), ephemeral=True
        )
        return

    db.remove_guild_data(gid)

    await interaction.response.send_message(
        localized("force_leave_success", lang, guild_id=gid), ephemeral=True
    )

@bot.tree.command(name="backup", description="get a database backup")
async def backup_cmd(interaction: discord.Interaction):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(localized("no_permission", lang), ephemeral=True)
        return
    import io
    from backup_crypto import build_encrypted_backup, encrypted_filename
    try:
        data = build_encrypted_backup("guard.db")
    except Exception as e:
        await interaction.response.send_message(localized("backup_failed", lang, error=str(e)), ephemeral=True)
        return
    await interaction.response.send_message(
        file=discord.File(io.BytesIO(data), filename=encrypted_filename("guard.db"))
    )

_locale_file_cooldown = {}

def _locale_cooldown_ok(bucket):
    """10-minute cooldown per server for the /locale <lang> file download."""
    now = time.time()
    if now - _locale_file_cooldown.get(bucket, 0) < 600:
        return False
    _locale_file_cooldown[bucket] = now
    return True

async def post_loc_suggestion(*, lang, key, suggestion, code, ui_lang, username, user_id, avatar_url=None):
    """Post a localization suggestion to the Discord support chat(s)."""
    body = localized("loc_suggest_support_body", ui_lang,
                     suggestion=suggestion, name=language_name(lang), lang=lang, key=key)
    footer = f"{username} │ ID: {user_id} │ {code}"
    for cid in SUPPORT_CHATS.get("discord", set()):
        channel = await _get_channel(cid)
        if not channel:
            continue
        embed = discord.Embed(description=body)
        embed.set_footer(text=footer, icon_url=avatar_url)
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

async def post_loc_reply(*, admin, code, ui_lang, title, body):
    """Publish an admin's /loc-reply to the Discord support chat(s)."""
    prefix = localized("loc_reply_support_prefix", ui_lang, admin=admin, code=code)
    for cid in SUPPORT_CHATS.get("discord", set()):
        channel = await _get_channel(cid)
        if not channel:
            continue
        try:
            await channel.send(embed=discord.Embed(title=title, description=f"{prefix}\n\n{body}"))
        except Exception:
            pass

@bot.tree.command(name="locale", description="localization status, or a language's file")
@app_commands.describe(lang="Language code (optional). With a code, sends that language's localization file.")
async def locale_cmd(interaction: discord.Interaction, lang: str = None):
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not lang or not lang.strip():
        lines = [localized("loc_list_header", glang)]
        for code in available_locales():
            st = locale_stats(code)
            lines.append(f"{language_name(code)} (`{code}`): {locale_bar(code)} {st['percent']}%")
        lines.append("")
        lines.append(localized("loc_list_footer", glang))
        await interaction.response.send_message("\n".join(lines))
        return

    code = lang.strip().lower()
    if code not in available_locales():
        await interaction.response.send_message(
            localized("loc_unknown_lang", glang, lang=code, supported=", ".join(available_locales())),
            ephemeral=True
        )
        return

    bucket = interaction.guild_id or interaction.user.id
    if not _locale_cooldown_ok(bucket):
        await interaction.response.send_message(localized("loc_cooldown", glang), ephemeral=True)
        return

    path = os.path.join(os.path.dirname(utils.__file__), "i18n", f"{code}.json")
    st = locale_stats(code)
    caption = localized("loc_file_caption", glang, name=language_name(code), code=code, percent=st["percent"])
    try:
        await interaction.response.send_message(caption, file=discord.File(path, filename=f"{code}.json"))
    except Exception:
        await interaction.response.send_message(caption, ephemeral=True)

@bot.tree.command(name="loc-compare", description="compare a reply across languages")
@app_commands.describe(key="Reply code (as shown in the localization file)")
async def loc_compare_cmd(interaction: discord.Interaction, key: str):
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    key = key.strip()
    data = compare_reply(key)
    if data is None:
        await interaction.response.send_message(localized("loc_compare_not_found", glang, key=key), ephemeral=True)
        return

    lines = [localized("loc_compare_header", glang, key=key)]
    for code in LANG_ORDER:
        if code not in data:
            continue
        status, text = data[code]
        emoji = LOCALE_STATUS_EMOJI.get(status, "")
        if text is None:
            shown = localized("loc_compare_untranslated", glang)
        else:
            shown = str(text)
            if len(shown) > 300:
                shown = shown[:297] + "..."
        lines.append(f"{emoji} {language_name(code)}: {shown}")
    msg = "\n".join(lines)
    if len(msg) > 1990:
        msg = msg[:1990]
    await interaction.response.send_message(msg)

@bot.tree.command(name="loc-suggest", description="suggest a localization")
@app_commands.describe(language="Language code", code="Reply code", text="Suggested text")
async def loc_suggest_cmd(interaction: discord.Interaction, language: str, code: str, text: str):
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    language = language.strip().lower()
    if language not in SUPPORTED_LANGS:
        await interaction.response.send_message(
            localized("loc_unknown_lang", glang, lang=language, supported=", ".join(available_locales())),
            ephemeral=True
        )
        return
    if not SUPPORT_CHATS.get("discord"):
        await interaction.response.send_message(localized("loc_suggest_no_support", glang), ephemeral=True)
        return

    msg_code = secrets.token_hex(4)
    db.add_loc_suggestion(msg_code, "discord", interaction.user.id, str(interaction.user),
                          language, code.strip(), text, glang)
    avatar_url = None
    try:
        avatar_url = interaction.user.display_avatar.url
    except Exception:
        avatar_url = None
    await post_loc_suggestion(lang=language, key=code.strip(), suggestion=text, code=msg_code,
                              ui_lang=glang, username=str(interaction.user),
                              user_id=interaction.user.id, avatar_url=avatar_url)
    await interaction.response.send_message(localized("loc_suggest_confirm", glang, code=msg_code), ephemeral=True)

@bot.tree.command(name="loc-reply", description="reply to a localization suggestion (bot admins)")
@app_commands.describe(code="Message code from the suggestion", text="Reply text")
async def loc_reply_cmd(interaction: discord.Interaction, code: str, text: str):
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(localized("no_permission", glang), ephemeral=True)
        return

    row = db.get_loc_suggestion(code.strip())
    if not row:
        await interaction.response.send_message(localized("loc_reply_not_found", glang, code=code), ephemeral=True)
        return

    ui_lang = row["ui_lang"] or DEFAULT_LANG
    title = localized("loc_reply_dm_title", ui_lang)
    body = localized("loc_reply_dm_body", ui_lang,
                     suggestion=row["suggestion"], reply=text,
                     name=language_name(row["lang"]), lang=row["lang"], key=row["rkey"])

    ok = False
    if row["platform"] == "discord":
        try:
            user = await bot.fetch_user(int(row["user_id"]))
            await user.send(embed=discord.Embed(title=title, description=body))
            ok = True
        except Exception:
            ok = False

    await post_loc_reply(admin=str(interaction.user), code=code.strip(),
                         ui_lang=ui_lang, title=title, body=body)

    if ok:
        db.delete_loc_suggestion(code.strip())
        await interaction.response.send_message(localized("loc_reply_sent", glang), ephemeral=True)
    else:
        await interaction.response.send_message(localized("loc_reply_failed", glang), ephemeral=True)

async def status_loop(client: discord.Client):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            text = utils.get_next_status_text(db.count_guilds())
            await client.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=text)
            )
        except Exception:
            pass
        await asyncio.sleep(60)

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

GATE_SCAN_INTERVAL = 900

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
