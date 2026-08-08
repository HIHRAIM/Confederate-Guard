"""The four ban commands a human runs by hand.

They are thin: each parses its arguments, checks the caller, and then walks
the same path the automatic bans take in discord_bot/bans.py. What is worth
knowing is the split of authority — /ban and /unban are Server Admin
commands, scoped to one server, while /globalban and /globalunban are Bot
Admin commands that write against a whole network.

All four take a raw user id rather than a member argument, deliberately: the
people these commands are aimed at have usually left, or were never on the
server to begin with.
"""
import time

import discord
from discord import app_commands

import db
from discord_bot.bans import (
    enforce_global_ban, _execute_global_unban, _purge_recent_messages,
)
from discord_bot.client import _get_channel, bot
from discord_bot.purgatorium import _purgatorium_invite_line
from utils import (
    DEFAULT_LANG, SUPPORTED_LANGS, format_duration, is_admin, localized,
    parse_global_duration,
)

@bot.tree.command(name="ban", description="ban a user by ID")
@app_commands.describe(
    user_id="ID of the user to ban",
    duration="Duration: 1h, 1d, 2m (months), 3y (years), or infinity (10 years); max 10 years",
    reason="Reason for the ban"
)
async def ban_cmd(interaction: discord.Interaction, user_id: str, duration: str, reason: str):
    """Ban a user on this server, by id, whether or not they are here.

    The only DM this command sends is the Purgatorium invitation, and only
    when the server has no appeal text of its own — a manual ban is assumed to
    have been explained by the person issuing it, so the bot adds nothing
    except the route to appeal it.

    parse_global_duration rather than parse_duration: this is a moderator's
    sentence, so it is expressed in hours to years and always finite, capped
    at ten.
    """
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
    """Ban here and record the ban against this server's network (Bot Admins).

    Refuses on a server with no network — there would be nothing to record the
    ban against. The local ban is applied first and the network fan-out last,
    so the command has done its most important work before it starts touching
    servers that may be unreachable.

    The fan-out only reaches servers that have /setgbans enable *and* where
    the user is currently a member: this bans the people who are there, it
    does not pre-ban an absentee across the network. Those who join later are
    caught by on_member_join instead.
    """
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
    """Lift this network's ban and undo it everywhere it landed (Bot Admins).

    The whole operation is _execute_global_unban, shared with the pardon
    buttons and the consul pardon channel, so a network ban is always lifted
    the same way: record deleted, then unbanned on the origin server and on
    every server carrying an enforcement mark, with the ban history cleared so
    the prior-ban alert stops firing.

    The existence check uses get_global_ban rather than the active variant on
    purpose — a ban that expired minutes ago still has servers to clean up.
    """
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
    """Unban on this server and clear everything the bot remembers about it.

    Three rows go, not one: the scheduled unban, the network-enforcement mark
    and the ban-history entry — the last one so that a ban a moderator
    deliberately reverted stops producing prior-ban alerts when the user
    returns.

    A user who is not banned on Discord is still worth running this on: the
    rows are cleared either way and the reply says the ban was not found,
    because the database and Discord can disagree after a manual unban.
    """
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
