"""Granting and revoking the three delegated roles, plus /backup.

Server admins, consuls and localizers have nothing to do with each other
except that only a Bot Admin may hand any of them out, which is what puts
them in one module. /backup is here for the same reason and because
bridge_bot keeps it here too.

Two of the three grants have an effect outside the database. A consul is
handed the config.CONSULS roles on Purgatorium immediately, so that
appointment does not wait for them to re-join; a new server admin and a new
localizer are told by DM, since neither would otherwise notice.
"""
import discord
from discord import app_commands

import db
from config import CONSULS, PURGATORIUM_GUILD_ID
from discord_bot.client import bot
from utils import DEFAULT_LANG, get_guild_lang, is_admin, localized

@bot.tree.command(name="setadmin", description="add a server admin")
@app_commands.describe(user_id="ID of the user to grant admin rights on this server")
async def setadmin_cmd(interaction: discord.Interaction, user_id: str):
    """Grant Server Admin rights on this server (Bot Admins).

    Scoped to the server the command was run in, and checked for an existing
    grant first so that re-running it reports the fact instead of pretending
    to do something. The DM is best-effort: the grant stands whether or not
    the user has DMs open.
    """
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
    """Revoke Server Admin rights on this server (Bot Admins).

    A Bot Admin from config.py cannot be demoted this way and the command
    says so through 'not an admin' — their rights never came from a row.
    """
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
    """Appoint an appeal-server consul (Bot Admins).

    Writes the row and, if the user is already on Purgatorium, hands them the
    roles on the spot — otherwise the gate does it when they arrive. The row
    is what grants the right to press the tribunal and pardon buttons, and it
    works whether or not the roles were ever applied.
    """
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
    """Dismiss a consul and take the Purgatorium roles back (Bot Admins).

    Someone who holds a config.CONSULS role directly keeps every right this
    removes: the roles and the table are two independent routes in, and only
    the table is under this command's control.
    """
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
    """Grant Localizer status (Bot Admins).

    The only two commands that accept a name rather than an id are these,
    because the person being appointed is usually someone the admin knows by
    handle. The username is stored alongside the grant for the control
    panel's username login, and the DM tells the user a door they never asked
    for has been opened.
    """
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
    """Revoke a delegated Localizer status (Bot Admins).

    Server Admins hold the status implicitly while they remain admins and
    have no row to remove, which is what the 'not a localizer' reply means
    when it lands on one of them.
    """
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

@bot.tree.command(name="backup", description="get a database backup")
async def backup_cmd(interaction: discord.Interaction):
    """Send an encrypted database snapshot on demand (Bot Admins).

    The same authenticated format the 12-hourly automatic backup uses, and
    the same reason it is encrypted: the file goes into a Discord channel,
    which stores it indefinitely. Without BACKUP_KEY the build raises and the
    error is reported rather than a plaintext database being sent.

    Deliberately not ephemeral — an operator asking for a backup wants it in
    the channel, where it survives the interaction.
    """
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
