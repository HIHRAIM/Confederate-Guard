import discord
from discord import app_commands
import asyncio
import time
import traceback
from datetime import timedelta
import db
from utils import (
    is_admin, get_guild_lang, localized,
    parse_duration, format_duration, message_has_spam,
    classify_banned_link, message_has_banned_link,
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

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

bot = GuardBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    tb = traceback.format_exc()
    print(f"[ERROR] Command '{interaction.command.name if interaction.command else '?'}': {error}\n{tb}")
    msg = f"Internal error: {error}"
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass

@bot.tree.command(name="setup", description="Register server: set language and log channel")
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

@bot.tree.command(name="guard", description="Enable spam guard on this channel")
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

@bot.tree.command(name="dm", description="Set custom DM message sent to users before banning")
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

@bot.tree.command(name="autorole", description="Set a role to be automatically given to all members")
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

async def _check_report_access(interaction: discord.Interaction):
    """Returns (guild_row, lang, network) or (None, lang, None) after responding with an error."""
    guild_row = db.get_guild(interaction.guild.id) if interaction.guild else None
    lang = guild_row["lang"] if guild_row else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return None, lang, None

    if not guild_row:
        await interaction.response.send_message(
            localized("report_no_setup", lang), ephemeral=True
        )
        return None, lang, None

    network = guild_row["network"]
    if network is None:
        await interaction.response.send_message(
            localized("report_no_network", lang), ephemeral=True
        )
        return None, lang, None

    return guild_row, lang, network

async def _send_report(interaction: discord.Interaction, reported_msg: discord.Message, lang, network):
    server_name = interaction.guild.name

    network_guilds = db.get_network_guilds(network)
    await interaction.response.send_message(localized("report_sent", lang), ephemeral=True)

    for row in network_guilds:
        if int(row["guild_id"]) == interaction.guild.id:
            continue
        log_cid = int(row["log_channel_id"])
        log_ch = bot.get_channel(log_cid)
        if not log_ch:
            try:
                log_ch = await bot.fetch_channel(log_cid)
            except Exception:
                continue
        report_text = localized(
            "report_text", row["lang"],
            server=server_name,
            nick=reported_msg.author.display_name,
            content=reported_msg.content or "",
            user_id=reported_msg.author.id
        )
        try:
            await log_ch.send(report_text)
        except Exception:
            pass

@bot.tree.command(name="report", description="Report a message to the network log channels")
@app_commands.describe(
    message_id="ID of the message to report (optional, defaults to the latest message in this channel)"
)
async def report_cmd(interaction: discord.Interaction, message_id: str = None):
    guild_row, lang, network = await _check_report_access(interaction)
    if not guild_row:
        return

    reported_msg = None
    if message_id is not None:
        try:
            mid = int(message_id.strip())
        except ValueError:
            await interaction.response.send_message(
                localized("report_invalid_message_id", lang), ephemeral=True
            )
            return
        for channel in interaction.guild.text_channels:
            try:
                reported_msg = await channel.fetch_message(mid)
                break
            except Exception:
                continue
    else:
        try:
            async for msg in interaction.channel.history(limit=50):
                if msg.author.id != interaction.user.id and msg.author.id != bot.user.id:
                    reported_msg = msg
                    break
        except Exception:
            pass

    if reported_msg is None:
        await interaction.response.send_message(
            localized("report_message_not_found", lang), ephemeral=True
        )
        return

    await _send_report(interaction, reported_msg, lang, network)

@bot.tree.context_menu(name="report")
async def report_context_menu(interaction: discord.Interaction, message: discord.Message):
    guild_row, lang, network = await _check_report_access(interaction)
    if not guild_row:
        return
    await _send_report(interaction, message, lang, network)

@bot.tree.command(name="ban", description="Ban a user by ID (works even if not on the server)")
@app_commands.describe(
    user_id="ID of the user to ban",
    duration="Ban duration (e.g. 30m, 2h, 1d, infinity)",
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
        seconds = parse_duration(duration)
    except ValueError:
        await interaction.response.send_message(
            localized("duration_invalid", lang), ephemeral=True
        )
        return

    try:
        await interaction.guild.ban(discord.Object(id=uid), reason=reason, delete_message_days=0)
    except Exception as e:
        await interaction.response.send_message(
            localized("ban_cmd_failed", lang, user_id=uid, error=str(e)), ephemeral=True
        )
        return

    if seconds is not None:
        db.add_active_ban(interaction.guild.id, uid, int(time.time()) + seconds)

    dur_str = format_duration(seconds, lang)
    await interaction.response.send_message(
        localized("ban_cmd_success", lang, user_id=uid, duration=dur_str, reason=reason)
    )

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

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
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
    if message.author.bot or message.webhook_id:
        return
    if not message.guild:
        return

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
        dm_text = custom_dm.replace("{server}", message.guild.name)
    else:
        dm_text = localized("ban_dm", lang, server=message.guild.name)
    try:
        await member.send(dm_text)
    except Exception:
        pass

    try:
        await message.guild.ban(member, reason=reason, delete_message_days=0)
    except Exception:
        return

    cutoff = discord.utils.utcnow() - timedelta(hours=24)
    for channel in message.guild.text_channels:
        try:
            to_delete = []
            async for msg in channel.history(after=cutoff, limit=None):
                if msg.author.id == member.id:
                    to_delete.append(msg)
            if to_delete:
                for i in range(0, len(to_delete), 100):
                    await channel.delete_messages(to_delete[i:i+100])
        except Exception:
            pass

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

@bot.tree.command(name="setadmin", description="Add a user as a server admin")
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

@bot.tree.command(name="remadmin", description="Remove a user's server admin status (bot admins)")
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

@bot.tree.command(name="banlink", description="Add a link or Discord invite to the banned list (bot admins)")
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

@bot.tree.command(name="unbanlink", description="Remove a link from the banned list by its number (bot admins)")
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

@bot.tree.command(name="links", description="Show the list of banned links")
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

@bot.tree.command(name="help", description="Show the list of commands")
async def help_cmd(interaction: discord.Interaction):
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    server_admin_lines = "\n".join([
        localized("help_cmd_setup", lang),
        localized("help_cmd_guard", lang),
        localized("help_cmd_dm", lang),
        localized("help_cmd_autorole", lang),
        localized("help_cmd_ban", lang),
        localized("help_cmd_report", lang),
        localized("help_cmd_links", lang),
    ])

    bot_admin_lines = "\n".join([
        localized("help_cmd_banlink", lang),
        localized("help_cmd_unbanlink", lang),
        localized("help_cmd_setadmin", lang),
        localized("help_cmd_remadmin", lang),
        localized("help_cmd_backup", lang),
        localized("help_cmd_list_chats", lang),
        localized("help_cmd_force_leave", lang),
    ])

    embed = discord.Embed(
        title=localized("help_title", lang),
        color=discord.Color.blurple()
    )
    embed.add_field(name=localized("help_section_server_admins", lang), value=server_admin_lines, inline=False)
    embed.add_field(name=localized("help_section_bot_admins", lang), value=bot_admin_lines, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="list_chats", description="List all servers the bot is in (bot admins)")
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

@bot.tree.command(name="force_leave", description="Force the bot to leave a server by ID (bot admins)")
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

@bot.tree.command(name="backup", description="Send current database backup")
async def backup_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("No permission", ephemeral=True)
        return
    import io
    try:
        with open("guard.db", "rb") as f:
            data = f.read()
    except Exception as e:
        await interaction.response.send_message(f"Failed to read database: {e}", ephemeral=True)
        return
    await interaction.response.send_message(
        file=discord.File(io.BytesIO(data), filename="guard.db")
    )

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
        except Exception:
            pass
        await asyncio.sleep(60)
