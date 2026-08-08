"""Per-server switches: what the bot guards, what it says, what roles it
hands out, and whether it enforces its network's bans.

Every command here needs the server to be registered first, and each says so
in its own words — the six replies are separate keys because "run /setup
first" arrives at six different moments and the operator should be able to
tell which one they hit.

The switches themselves are trivial writes; two of them are not. /autorole
walks the whole member list on the spot, and /setgbans both applies a backlog
of bans and reverts them, which is why those two are the only commands in this
module that talk to Discord after answering.
"""
import discord
from discord import app_commands

import db
from discord_bot.bans import enforce_global_ban
from discord_bot.client import bot
from utils import DEFAULT_LANG, format_duration, is_admin, localized, parse_duration

@bot.tree.command(name="guard", description="enable spam guard on this channel")
@app_commands.describe(
    duration="Ban duration (e.g. 30m, 2h, 1d, infinity)",
    reason="Reason shown in the ban"
)
async def guard_cmd(interaction: discord.Interaction, duration: str, reason: str):
    """Turn the current channel into a guarded one.

    Refuses to re-guard a channel that already is: changing the term or the
    reason means unguarding first, which is deliberate — silently overwriting
    a colleague's settings on a channel that is already policed is worse than
    an error message.

    The duration accepts the short units (30s … 1w, infinity); the term is
    stored, not resolved, so bans issued months apart use whatever the setting
    says at the time.
    """
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
    """Replace the default pre-ban DM with the server's own text.

    Stored with its {server} and {reason} placeholders intact — they are
    substituted when the DM is sent, so one text keeps working across every
    guarded channel and survives their reasons being changed.
    """
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
    """Set the role every member gets, and hand it out immediately.

    The backfill is the reason this command answers twice: the first reply
    confirms the setting, the follow-up reports how many members were reached.
    Failures per member are swallowed — a role above the bot in the hierarchy,
    or a member it cannot touch, must not abort the sweep for everyone else.

    Independent of verification: this role goes to everyone, including members
    who never wrote a word.
    """
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

@bot.tree.command(name="setverify", description="give a role to members who wrote on more than one day (shared cross-server database)")
@app_commands.describe(
    role_id="ID of the role to give verified users",
    channel_id="ID of the announcement channel (optional, defaults to the /setup log channel)"
)
async def setverify_cmd(interaction: discord.Interaction, role_id: str, channel_id: str = None):
    """Turn on activity-based verification for this server.

    The role is checked for existence here and nowhere else — the grant path
    runs on every message and cannot afford to explain itself, so a wrong id
    has to be caught at the moment it is typed.

    The announcement channel is optional and falls back to the /setup log
    channel. What is *not* per server is the verified set itself: switching
    this on means members verified anywhere already qualify here, and the
    start-up backfill will hand them the role without announcing.
    """
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

@bot.tree.command(name="setgbans", description="enable/disable enforcement of network bans on this server")
@app_commands.describe(mode="enable or disable")
async def setgbans_cmd(interaction: discord.Interaction, mode: str):
    """Opt this server in or out of enforcing its network's bans.

    Both directions do real work and both can take a while, hence the defer.
    Enabling walks the current member list against the network's active bans
    and enforces each match — so the count in the reply is the size of the
    network's ban list, not the number of people actually banned here.

    Disabling reverts exactly the bans this mechanism applied, by walking
    gban_enforcements. A ban issued locally with /ban or /globalban has no
    enforcement row and survives untouched, which is the entire reason that
    table exists.
    """
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
    """Set the server's own appeal instructions, appended to the ban DM.

    Setting this has a second effect the name does not suggest: the server
    stops sending the Purgatorium invitation, on the assumption that a server
    with its own appeal route does not want its banned users sent to a shared
    appeal server instead.
    """
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
