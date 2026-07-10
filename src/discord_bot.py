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
from config import VERIFIED, UNVERIFIED, SUPPORT_CHATS
from utils import (
    is_admin, get_guild_lang, localized,
    parse_duration, parse_global_duration, format_duration, message_has_spam,
    classify_banned_link, message_has_banned_link,
    language_name, available_locales, locale_stats, locale_bar, compare_reply,
    LANG_ORDER, LOCALE_STATUS_EMOJI,
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

async def propagate_verified_roles(uid, announce: bool):
    """Grant the verify role to a verified user on every guild where they are a
    present member and verification is enabled, skipping guilds where the grant
    was already recorded.

    This is the single place cross-server verification fans out. It fixes two
    problems: verified users now get their role on *all* their servers (not only
    where they joined/chatted), and announcements land on the servers where the
    user actually is — never on whichever guild happens to host the bridge sync
    channel. Recording the grant also prevents a later message from the user from
    triggering a duplicate 'verified' announcement.
    """
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
        try:
            await _grant_verify(member, verify_row, guild_row, lang, cross_server=True, announce=announce)
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
        await _grant_verify(member, verify_row, guild_row, lang, cross_server=True)
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

    bridge_bot posts a bare user ID to the VERIFIED channel when a user consents
    to message forwarding, and to the UNVERIFIED channel when a user unverifies
    themselves. We add or remove the user from our cross-server verified database
    accordingly.
    """
    content = (message.content or "").strip()
    if not content.isdigit():
        return
    uid = int(content)

    if message.channel.id in UNVERIFIED:
        db.remove_verified(uid)
        return

    if db.is_verified(uid):
        return
    db.add_verified(uid, message.guild.id if message.guild else None)
    await propagate_verified_roles(uid, announce=True)

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
            await member_obj.send(
                localized(
                    "gban_enforce_dm", lang,
                    server=guild.name, reason=reason, remaining=f"<t:{unban_at}:R>",
                )
            )
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

    dur_str = format_duration(seconds, lang)
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
            await target.send(
                localized("globalban_dm", lang, server=interaction.guild.name,
                          reason=reason, remaining=f"<t:{unban_at}:R>")
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
    gban = db.get_global_ban(network, uid)
    if not gban:
        await interaction.response.send_message(
            localized("globalunban_not_banned", lang, user_id=uid), ephemeral=True
        )
        return

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
async def on_member_join(member: discord.Member):
    if member.bot:
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
        try:
            await _grant_verify(member, verify_row, guild_row, lang, cross_server=True)
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

    if duration_seconds is not None:
        db.add_active_ban(message.guild.id, member.id, int(time.time()) + duration_seconds)

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

async def unban_loop(client: discord.Client):
    await client.wait_until_ready()
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
        except Exception:
            pass
        await asyncio.sleep(60)
