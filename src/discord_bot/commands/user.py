"""The one command aimed at people who are not moderating anything: /help.

A module for a single command looks wrong and is not. The address is what
matters: /help lives in commands/user.py in bridge_bot too, so someone coming
from that repo finds it without opening the tree. Anything else the bot ever
offers an ordinary member belongs here as well.
"""
import discord

import db
from discord_bot.client import bot
from utils import DEFAULT_LANG, get_guild_lang, localized

@bot.tree.command(name="help", description="show this command list")
async def help_cmd(interaction: discord.Interaction):
    """List the commands, grouped by who may run them.

    Deliberately not filtered by the caller's own rights: seeing that
    /globalban exists and is a Bot Admin command is how a server admin learns
    what to ask for, and hiding it would make the bot look smaller than it is.

    The three groups are built from individual localization keys rather than
    from the command tree, so a command's help line can explain what it is for
    instead of repeating the one-line description Discord already shows. The
    cost is that a new command needs its line added here by hand.
    """
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
