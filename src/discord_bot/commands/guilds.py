"""Commands about the bot's presence in servers: registering one, listing
them, leaving one.

`/setup` is the door to everything else — without a `guilds` row a server has
no language, no log channel and no network, and most of the other commands
refuse to run. The other two are Bot Admin tools for the deployment as a
whole.

This is guard_bot's counterpart to bridge_bot's commands/bridges.py: the
module named after the bot's main object, holding /list_chats and
/force_leave.
"""
import discord
from discord import app_commands

import db
from discord_bot.client import bot
from utils import DEFAULT_LANG, SUPPORTED_LANGS, get_guild_lang, is_admin, localized

@bot.tree.command(name="setup", description="register the server: language, log channel, network")
@app_commands.describe(
    lang="Language code (ru, uk, pl, en, es, pt)",
    channel_id="ID of the channel for ban logs",
    network="Network ID (number) to group servers together"
)
async def setup_cmd(interaction: discord.Interaction, lang: str, channel_id: str, network: int = None):
    """Register the server, or change its registration.

    The three replies before the write are all in English on purpose: an
    unregistered server has no language yet, and the one being offered may be
    the thing that is wrong.

    `network` is optional and omitting it is meaningful — running /setup again
    without it takes the server out of its network. The channel is taken as a
    raw id rather than a channel argument so that a log channel the admin
    cannot see, or one in another server, can still be named.
    """
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

@bot.tree.command(name="list_chats", description="list of servers the bot is in")
async def list_chats_cmd(interaction: discord.Interaction):
    """Every server the bot is in, registered or not (Bot Admins).

    Deliberately `bot.guilds` rather than the guilds table: the point of the
    command is to find the servers nobody set up, which is exactly what the
    table cannot show. Long lists are sent as a file, since the answer is
    ephemeral and cannot be split across a readable thread.
    """
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
    """Leave a server and erase its data (Bot Admins).

    The only caller of db.remove_guild_data, and the difference from being
    kicked matters: on_guild_remove keeps everything so that a re-invitation
    resumes where it left off, while this command is the deliberate
    "we are done here" and takes the rows with it.

    The data is erased only after the leave succeeds, so a failed departure
    does not cost a server its settings.
    """
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
