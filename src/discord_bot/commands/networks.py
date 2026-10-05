"""Network identity, ownership, invitations and membership commands.

A sponsor controls only networks they own. A Bot Admin can manage every
network. Joining needs two acts: the owner invites a guild, then one of that
guild's Server Admins accepts before /setup names the network.
"""
import discord
from discord import app_commands

import db
import sponsors
from discord_bot.client import bot
from discord_bot.commands.guilds import _release_network_enforcements
from utils import get_guild_lang, is_admin, localized

def _controls(user_id, network, *, new_work=True):
    """Whether this account may manage this network."""
    row = db.get_network(network)
    return bool(row and (is_admin(user_id) or sponsors.can_manage_network(
        user_id, network, new_work=new_work)))

@bot.tree.command(name="network-create", description="create a network with a permanent number")
@app_commands.describe(name="Name shown for the network")
async def network_create_cmd(interaction: discord.Interaction, name: str):
    """Allocate a number that is never reassigned to somebody else's bans."""
    lang = get_guild_lang(interaction.guild_id)
    admin = is_admin(interaction.user.id)
    if not admin:
        tier = await sponsors.live_discord_tier(interaction.user.id)
        if tier is not None:
            sponsors.apply_tier(interaction.user.id, tier)
        limit = sponsors.sponsor_limits(sponsors.sponsor_tier(interaction.user.id))["networks"]
        if len(db.sponsor_networks(interaction.user.id)) >= limit:
            await interaction.response.send_message(localized("sponsor_network_limit", lang), ephemeral=True)
            return
    name = name.strip()[:100]
    if not name:
        await interaction.response.send_message(localized("network_name_required", lang), ephemeral=True)
        return
    number = db.create_network(None if admin else interaction.user.id, name)
    if not admin:
        db.claim_network(number, interaction.user.id)
    await interaction.response.send_message(
        localized("network_created", lang, network=number, name=name), ephemeral=True)

@bot.tree.command(name="network-rename", description="rename a network you manage")
@app_commands.describe(network="Network number", name="New display name")
async def network_rename_cmd(interaction: discord.Interaction, network: int, name: str):
    """Rename without changing the network number or its bans."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    name = name.strip()[:100]
    if not name:
        await interaction.response.send_message(localized("network_name_required", lang), ephemeral=True)
        return
    db.rename_network(network, name)
    await interaction.response.send_message(localized("network_renamed", lang, network=network), ephemeral=True)

@bot.tree.command(name="network-info", description="show a network and its member servers")
@app_commands.describe(network="Network number")
async def network_info_cmd(interaction: discord.Interaction, network: int):
    """Show one network's owner, member guilds and active ban count."""
    lang = get_guild_lang(interaction.guild_id)
    row = db.get_network(network)
    if row is None:
        await interaction.response.send_message(localized("network_unknown", lang), ephemeral=True)
        return
    guilds = db.get_network_guilds(network)
    members = ", ".join(str(g["guild_id"]) for g in guilds) or "—"
    await interaction.response.send_message(localized(
        "network_info", lang, network=network, name=row["name"] or "—",
        owner=row["owner_id"] or "operator", guilds=members,
        bans=db.network_ban_count(network)), ephemeral=True)

@bot.tree.command(name="network-invite", description="invite a server into your network")
@app_commands.describe(network="Network number", server_id="Discord server ID")
async def network_invite_cmd(interaction: discord.Interaction, network: int, server_id: str):
    """Invite one server; its admin must still accept the invitation."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    try:
        guild_id = int(server_id)
    except ValueError:
        await interaction.response.send_message(localized("network_invalid_guild", lang), ephemeral=True)
        return
    row = db.get_network(network)
    if row["owner_id"] is not None and not is_admin(interaction.user.id):
        if not sponsors.can_add_server(row["owner_id"], guild_id):
            await interaction.response.send_message(localized("sponsor_server_limit", lang), ephemeral=True)
            return
    db.invite_guild(network, guild_id, interaction.user.id)
    await interaction.response.send_message(localized(
        "network_invited", lang, network=network, guild_id=guild_id,
        days=db.INVITE_SECONDS // 86400), ephemeral=True)

@bot.tree.command(name="network-revoke", description="revoke a server invitation")
@app_commands.describe(network="Network number", server_id="Discord server ID")
async def network_revoke_cmd(interaction: discord.Interaction, network: int, server_id: str):
    """Cancel an invitation while leaving existing membership alone."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    try:
        guild_id = int(server_id)
    except ValueError:
        await interaction.response.send_message(localized("network_invalid_guild", lang), ephemeral=True)
        return
    db.revoke_invite(network, guild_id)
    await interaction.response.send_message(localized("network_revoked", lang), ephemeral=True)

@bot.tree.command(name="network-accept", description="accept this server's network invitation")
@app_commands.describe(network="Network number")
async def network_accept_cmd(interaction: discord.Interaction, network: int):
    """Record server consent; /setup is still needed to enter the network."""
    lang = get_guild_lang(interaction.guild_id)
    if interaction.guild is None or not is_admin(interaction.user.id, interaction.guild.id):
        await interaction.response.send_message(localized("setup_no_perm", lang), ephemeral=True)
        return
    row = db.get_network(network)
    if row is None:
        await interaction.response.send_message(localized("network_unknown", lang), ephemeral=True)
        return
    if row["owner_id"] is not None and not is_admin(interaction.user.id) and (not sponsors.can_manage_network(
            row["owner_id"], network, new_work=True) or not sponsors.can_add_server(
            row["owner_id"], interaction.guild.id)):
        await interaction.response.send_message(localized("sponsor_server_limit", lang), ephemeral=True)
        return
    if not db.accept_invite(network, interaction.guild.id):
        await interaction.response.send_message(localized("network_invite_required", lang), ephemeral=True)
        return
    await interaction.response.send_message(localized("network_accepted", lang, network=network), ephemeral=True)

@bot.tree.command(name="network-kick", description="remove a server from your network")
@app_commands.describe(network="Network number", server_id="Discord server ID")
async def network_kick_cmd(interaction: discord.Interaction, network: int, server_id: str):
    """Remove membership and only the bans the network itself enforced."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network, new_work=False):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    try:
        guild_id = int(server_id)
    except ValueError:
        await interaction.response.send_message(localized("network_invalid_guild", lang), ephemeral=True)
        return
    row = db.get_guild(guild_id)
    if not row or row["network"] != network:
        await interaction.response.send_message(localized("network_not_member", lang), ephemeral=True)
        return
    guild = bot.get_guild(guild_id)
    if guild:
        await _release_network_enforcements(guild)
    db.leave_network(guild_id, network)
    db.revoke_invite(network, guild_id)
    await interaction.response.send_message(localized("network_kicked", lang), ephemeral=True)

@bot.tree.command(name="network-transfer", description="transfer a network to another Discord account")
@app_commands.describe(network="Network number", user_id="New owner's Discord user ID")
async def network_transfer_cmd(interaction: discord.Interaction, network: int, user_id: str):
    """Transfer ownership while keeping all member servers and bans."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    if not user_id.isdecimal():
        await interaction.response.send_message(localized("network_invalid_user", lang), ephemeral=True)
        return
    row = db.get_network(network)
    old_owner = row["owner_id"]
    new_owner = None if is_admin(int(user_id)) else user_id
    if new_owner is not None:
        tier = await sponsors.live_discord_tier(new_owner)
        if tier is not None:
            sponsors.apply_tier(new_owner, tier)
        limits = sponsors.sponsor_limits(sponsors.sponsor_tier(new_owner))
        if (len(db.sponsor_networks(new_owner)) >= limits["networks"] or
                db.sponsor_server_usage(new_owner) + db.network_guild_count(network) > limits["servers"]):
            await interaction.response.send_message(localized("sponsor_network_limit", lang), ephemeral=True)
            return
    if old_owner is not None:
        db.release_network(network, old_owner)
    db.transfer_network(network, new_owner)
    if new_owner is not None:
        db.claim_network(network, new_owner)
    await interaction.response.send_message(localized("network_transferred", lang, network=network, user_id=user_id), ephemeral=True)

@bot.tree.command(name="network-purgatorium", description="set your network's appeal server and invite")
@app_commands.describe(network="Network number", server_id="Appeal server ID", invite_url="Invite URL")
async def network_purgatorium_cmd(interaction: discord.Interaction, network: int,
                                   server_id: str, invite_url: str):
    """Bind an appeal server and invite to one network.

    A sponsor cannot reuse the operator's Purgatorium or one belonging to
    another network; routing by channel and membership depends on uniqueness.
    """
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    invite_url = invite_url.strip()
    if (not server_id.isdecimal() or not invite_url.startswith((
            "https://discord.gg/", "https://discord.com/invite/",
            "https://discordapp.com/invite/"))):
        await interaction.response.send_message(localized("network_purg_invalid", lang), ephemeral=True)
        return
    current = db.network_settings(network)
    found, existing = db.purgatorium_scope(server_id)
    identity = db.get_network(network)
    existing_identity = db.get_network(existing) if existing is not None else None
    same_owner = (existing_identity is not None and identity["owner_id"] is not None
                  and existing_identity["owner_id"] == identity["owner_id"])
    if found and str(current["guild_id"]) != server_id and not same_owner:
        await interaction.response.send_message(localized("network_purg_taken", lang), ephemeral=True)
        return
    if identity["owner_id"] is not None and current["guild_id"] not in (None, server_id):
        await interaction.response.send_message(localized("network_purg_taken", lang), ephemeral=True)
        return
    purg_guild = bot.get_guild(int(server_id))
    if purg_guild is None:
        await interaction.response.send_message(localized("network_purg_bot_missing", lang), ephemeral=True)
        return
    if not is_admin(interaction.user.id):
        member = purg_guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await purg_guild.fetch_member(interaction.user.id)
            except Exception:
                member = None
        perms = member.guild_permissions if member else None
        if perms is None or not (perms.administrator or perms.manage_guild):
            await interaction.response.send_message(localized("sponsor_manage_required", lang), ephemeral=True)
            return
    db.set_network_setting(network, "guild_id", server_id)
    db.set_network_setting(network, "invite_url", invite_url.strip())
    await interaction.response.send_message(localized("network_purg_saved", lang), ephemeral=True)

@bot.tree.command(name="network-channels", description="set your network's appeal protocol and tribunal channels")
@app_commands.describe(network="Network number", pardon="Pardon channel ID",
                       baninfo="Ban summary channel ID", tribunal="Tribunal channel ID")
async def network_channels_cmd(interaction: discord.Interaction, network: int,
                               pardon: str, baninfo: str, tribunal: str):
    """Keep all machine channels inside the network's appeal server."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    settings = db.network_settings(network)
    if not settings or not settings["guild_id"]:
        await interaction.response.send_message(localized("network_purg_first", lang), ephemeral=True)
        return
    ids = (pardon.strip(), baninfo.strip(), tribunal.strip())
    if not all(item.isdecimal() for item in ids) or len(set(ids)) != 3:
        await interaction.response.send_message(localized("network_channel_invalid", lang), ephemeral=True)
        return
    for item in ids:
        channel = bot.get_channel(int(item))
        if channel is None or str(channel.guild.id) != str(settings["guild_id"]):
            await interaction.response.send_message(localized("network_channel_invalid", lang), ephemeral=True)
            return
        if any(db.channel_scope(item, kind)[0] and item not in settings[f"{kind}_channels"]
               for kind in ("pardon", "baninfo", "tribunal")):
            await interaction.response.send_message(localized("network_channel_taken", lang), ephemeral=True)
            return
    for field, value in zip(("pardon_channel_id", "baninfo_channel_id", "tribunal_channel_id"), ids):
        db.set_network_setting(network, field, value)
    await interaction.response.send_message(localized("network_channels_saved", lang), ephemeral=True)

@bot.tree.command(name="network-consulroles", description="set your network's consul roles")
@app_commands.describe(network="Network number", roles="Role IDs separated by commas")
async def network_consulroles_cmd(interaction: discord.Interaction, network: int, roles: str):
    """Accept only roles belonging to this network's appeal server."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    settings = db.network_settings(network)
    purg = bot.get_guild(int(settings["guild_id"])) if settings and settings["guild_id"] else None
    items = [item.strip() for item in roles.split(",") if item.strip()]
    if purg is None or not items or any(not item.isdecimal() or purg.get_role(int(item)) is None for item in items):
        await interaction.response.send_message(localized("network_roles_invalid", lang), ephemeral=True)
        return
    db.set_network_setting(network, "consul_roles", ",".join(items))
    await interaction.response.send_message(localized("network_roles_saved", lang), ephemeral=True)

@bot.tree.command(name="network-appealtext", description="set the appeal instructions in your network's ban DMs")
@app_commands.describe(network="Network number", text="Text to include in a ban DM")
async def network_appealtext_cmd(interaction: discord.Interaction, network: int, text: str):
    """Store a network-specific appeal hint; an invite remains the fallback."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    db.set_network_setting(network, "appeal_text", text.strip()[:1000])
    await interaction.response.send_message(localized("network_appealtext_saved", lang), ephemeral=True)

@bot.tree.command(name="network-settings", description="show your network's appeal settings")
@app_commands.describe(network="Network number")
async def network_settings_cmd(interaction: discord.Interaction, network: int):
    """Display the effective settings, including operator defaults."""
    lang = get_guild_lang(interaction.guild_id)
    if not _controls(interaction.user.id, network, new_work=False):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    settings = db.network_settings(network)
    await interaction.response.send_message(localized(
        "network_settings", lang, network=network,
        guild=settings["guild_id"] or "—", invite=settings["invite_url"] or "—",
        pardon=", ".join(settings["pardon_channels"]) or "—",
        baninfo=", ".join(settings["baninfo_channels"]) or "—",
        tribunal=", ".join(settings["tribunal_channels"]) or "—",
        roles=", ".join(str(item) for item in settings["consul_roles"]) or "—",
        text=settings["appeal_text"] or "—"), ephemeral=True)
