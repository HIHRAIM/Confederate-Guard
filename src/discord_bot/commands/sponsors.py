"""A sponsor's server and network slots, backed by Patreon Discord roles."""
import discord
import time
from discord import app_commands

import db
import sponsors
from config import SPONSOR_URL
from discord_bot.client import bot
from utils import get_guild_lang, localized

async def _refresh(discord_id):
    """Pick up a new role promptly while preserving state on API failure."""
    tier = await sponsors.live_discord_tier(discord_id)
    if tier is not None:
        sponsors.apply_tier(discord_id, tier)

@bot.tree.command(name="sponsor", description="show your Guard sponsorship and slots")
async def sponsor_cmd(interaction: discord.Interaction):
    """Show one Discord account's paid tier, claims and grace period."""
    lang = get_guild_lang(interaction.guild_id)
    uid = interaction.user.id
    await _refresh(uid)
    row = db.get_sponsor_state(uid)
    owned = db.owner_networks(uid)
    if not row and not owned:
        await interaction.response.send_message(localized("sponsor_not_active", lang), ephemeral=True)
        return
    tier = sponsors.paid_sponsor_tier(uid)
    rights = sponsors.sponsor_rights_tier(uid)
    limits = sponsors.sponsor_limits(sponsors.sponsor_tier(uid))
    servers = db.sponsor_server_usage(uid)
    networks = db.sponsor_networks(uid)
    grace_window = (sponsors.COMMUNITY_GRACE_SECONDS
                    if row and row["grace_from_tier"] == -1
                    else sponsors.SPONSOR_GRACE_SECONDS)
    remaining = (grace_window -
                 (time.time() - int(row["grace_since"]))) if row and row["grace_since"] else 0
    days = max(0, int((remaining + 86399) // 86400))
    status = localized(
        "sponsor_status", lang,
        tier=(localized("sponsor_tier_community", lang) if tier == -1 else tier),
        rights=(localized("sponsor_tier_community", lang) if rights == -1 else rights),
        servers=servers, server_limit=limits["servers"],
        networks=len(networks), network_limit=limits["networks"], days=int(days),
        network_ids=", ".join(str(item["network"]) for item in networks) or "—")
    addendum = sponsors.status_addendum(uid, lang)
    if addendum:
        status += "\n" + addendum
    if owned:
        status += "\n" + localized("sponsor_saved_networks", lang,
                                    ids=", ".join(str(item["network"]) for item in owned))
    await interaction.response.send_message(status, ephemeral=True)

@bot.tree.command(name="sponsor-claim", description="claim this server with your subscription")
async def sponsor_claim_cmd(interaction: discord.Interaction):
    """Claim a server only when Discord confirms Manage Server permission."""
    lang = get_guild_lang(interaction.guild_id)
    if interaction.guild is None:
        await interaction.response.send_message(localized("sponsor_no_guild", lang), ephemeral=True)
        return
    perms = interaction.user.guild_permissions
    if not (perms.administrator or perms.manage_guild):
        await interaction.response.send_message(localized("sponsor_manage_required", lang), ephemeral=True)
        return
    await _refresh(interaction.user.id)
    status = sponsors.claim_server_status(interaction.user.id, interaction.guild.id)
    if status != "ok":
        await interaction.response.send_message(localized(status, lang), ephemeral=True)
        return
    old = db.get_guild(interaction.guild.id)
    if old and old["network"] is not None:
        network = db.get_network(old["network"])
        if (network and network["owner_id"] is not None
                and str(network["owner_id"]) != str(interaction.user.id)
                and sponsors.sponsor_rights_tier(network["owner_id"]) == 0):
            from discord_bot.commands.guilds import _release_network_enforcements
            await _release_network_enforcements(interaction.guild)
            db.setup_guild(interaction.guild.id, old["lang"],
                           old["log_channel_id"], None)
    db.claim_sponsor_guild(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(localized("sponsor_claimed", lang), ephemeral=True)

@bot.tree.command(name="sponsor-release", description="release one claimed server slot")
@app_commands.describe(server_id="Claimed server ID; defaults to this server")
async def sponsor_release_cmd(interaction: discord.Interaction, server_id: str = None):
    """Release a sponsor grant without touching a manual Server Admin row."""
    lang = get_guild_lang(interaction.guild_id)
    target = (server_id or str(interaction.guild_id or "")).strip()
    if not db.release_sponsor_guild(target, interaction.user.id):
        await interaction.response.send_message(localized("sponsor_release_none", lang), ephemeral=True)
        return
    db.touch_slot_change(interaction.user.id)
    await interaction.response.send_message(localized(
        "sponsor_released", lang, days=sponsors.SPONSOR_SLOT_CHANGE_SECONDS // 86400),
        ephemeral=True)

@bot.tree.command(name="network-claim", description="reclaim your saved network after renewing")
@app_commands.describe(network="Your network number")
async def network_claim_cmd(interaction: discord.Interaction, network: int):
    """Resume management of a preserved network when a slot is available."""
    lang = get_guild_lang(interaction.guild_id)
    await _refresh(interaction.user.id)
    row = db.get_network(network)
    if row is None or str(row["owner_id"]) != str(interaction.user.id):
        await interaction.response.send_message(localized("network_not_owner", lang), ephemeral=True)
        return
    if sponsors.sponsor_tier(interaction.user.id) == 0:
        await interaction.response.send_message(localized("sponsor_not_active", lang), ephemeral=True)
        return
    if db.get_network_claim(network):
        await interaction.response.send_message(localized("sponsor_network_claimed", lang), ephemeral=True)
        return
    if len(db.sponsor_networks(interaction.user.id)) >= sponsors.sponsor_limits(
            sponsors.sponsor_tier(interaction.user.id))["networks"]:
        await interaction.response.send_message(localized("sponsor_network_limit", lang), ephemeral=True)
        return
    if db.sponsor_server_usage(interaction.user.id) + db.network_guild_count(network) > sponsors.sponsor_limits(
            sponsors.sponsor_tier(interaction.user.id))["servers"]:
        await interaction.response.send_message(localized("sponsor_server_limit", lang), ephemeral=True)
        return
    db.claim_network(network, interaction.user.id)
    await interaction.response.send_message(localized("sponsor_network_claimed_ok", lang), ephemeral=True)

@bot.tree.command(name="sponsor-subscribe", description="how to subscribe for Guard access")
async def sponsor_subscribe_cmd(interaction: discord.Interaction):
    """Describe both tiers and the Patreon to Discord link requirement."""
    lang = get_guild_lang(interaction.guild_id)
    one = sponsors.sponsor_limits(1)
    two = sponsors.sponsor_limits(2)
    await interaction.response.send_message(localized(
        "sponsor_subscribe", lang, url=SPONSOR_URL,
        servers1=one["servers"], networks1=one["networks"],
        servers2=two["servers"], networks2=two["networks"]), ephemeral=True)
