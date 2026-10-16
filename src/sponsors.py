"""One Patreon subscription's Guard rights, slots and grace period.

Patreon grants Discord roles on the creator's server. This module is the only
place that interprets those roles. A stored tier supports cheap command checks
and an hourly reconciliation restores state after missed Discord events.
"""
import logging
import math
import time

import db
from config import (
    PATREON_GUILD_ID, PATREON_TIER_ROLES, COMMUNITY_SPONSOR_ROLE_IDS,
    PURGATORIUM_GUILD_ID,
)
from utils import DEFAULT_LANG, localized

logger = logging.getLogger("guard.sponsors")

SPONSOR_SERVERS = 2
SPONSOR_NETWORKS = 1
SPONSOR_TIER2_MULTIPLIER = 3
SPONSOR_TIER3_MULTIPLIER = 5
SPONSOR_GRACE_SECONDS = 30 * 86400
SPONSOR_RETENTION_SECONDS = 30 * 86400
SPONSOR_SLOT_CHANGE_SECONDS = 30 * 86400
SPONSOR_RECONCILE_SECONDS = 3600
COMMUNITY_WAIT_SECONDS = 7 * 86400
COMMUNITY_GRACE_SECONDS = 86400

def _rank(tier):
    return 0 if tier == -1 else -1 if tier == 0 else tier

def sponsor_limits(tier):
    """Server and network slots at this paid tier."""
    factor = (0 if tier == 0 else SPONSOR_TIER3_MULTIPLIER if tier == 3
              else SPONSOR_TIER2_MULTIPLIER if tier == 2 else 1)
    return {"servers": SPONSOR_SERVERS * factor,
            "networks": SPONSOR_NETWORKS * factor}

def _patreon_guild():
    """The creator's Discord server, or None during an outage."""
    from discord_bot import bot
    try:
        return bot.get_guild(int(PATREON_GUILD_ID))
    except (TypeError, ValueError):
        return None

def _tier_from_roles(role_ids):
    """The highest configured Patreon tier among a member's roles."""
    tiers = []
    for tier, role_id in (PATREON_TIER_ROLES or {}).items():
        try:
            if int(role_id) in role_ids:
                tiers.append(int(tier))
        except (TypeError, ValueError):
            continue
    if tiers:
        return max(tiers)
    return -1 if set(role_ids).intersection(map(int, COMMUNITY_SPONSOR_ROLE_IDS)) else 0

async def live_discord_tier(discord_id):
    """The single live tier source: 0/1/2, or None when Discord cannot tell."""
    guild = _patreon_guild()
    if guild is None:
        return None
    try:
        uid = int(discord_id)
    except (TypeError, ValueError):
        return 0
    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception as error:
            if "Unknown Member" in str(error) or "404" in str(error):
                return 0
            logger.info("sponsor lookup failed for %s: %s", uid, error)
            return None
    role_ids = {role.id for role in getattr(member, "roles", ())}
    db.observe_community_role(discord_id,
                              bool(role_ids.intersection(map(int, COMMUNITY_SPONSOR_ROLE_IDS))))
    return _tier_from_roles(role_ids)

async def live_tier_table():
    """Every cached role holder's highest tier, or None during an outage."""
    guild = _patreon_guild()
    if guild is None:
        return None
    tiers = {}
    seen_role = False
    for tier, role_id in (PATREON_TIER_ROLES or {}).items():
        try:
            role = guild.get_role(int(role_id))
            number = int(tier)
        except (TypeError, ValueError):
            continue
        if role is None:
            return None
        seen_role = True
        for member in role.members:
            key = str(member.id)
            tiers[key] = max(tiers.get(key, 0), number)
    community_members = set()
    for role_id in COMMUNITY_SPONSOR_ROLE_IDS:
        role = guild.get_role(int(role_id))
        if role is None:
            return None
        community_members.update(str(member.id) for member in role.members)
    for owner in {str(row["discord_id"]) for row in db.get_known_sponsors()} | set(tiers) | community_members:
        db.observe_community_role(owner, owner in community_members)
        if owner in community_members and owner not in tiers:
            tiers[owner] = -1
    return tiers if seen_role else None

def rights_tier(row):
    """The tier whose slots survive a still-open grace period."""
    if not row:
        return 0
    tier = int(row["tier"] or 0)
    since = row["grace_since"]
    former = int(row["grace_from_tier"] or 0)
    window = COMMUNITY_GRACE_SECONDS if former == -1 else SPONSOR_GRACE_SECONDS
    if since and _rank(former) > _rank(tier) and time.time() - int(since) < window:
        return former
    if tier == -1:
        seen = row["community_role_since"]
        return -1 if seen is not None and time.time() - int(seen) >= COMMUNITY_WAIT_SECONDS else 0
    return tier

def sponsor_tier(discord_id):
    """Tier usable for work, including thirty days after payment ends."""
    row = db.get_sponsor_state(discord_id)
    return rights_tier(row)

def paid_sponsor_tier(discord_id):
    """The role-backed tier without grace, for subscription status."""
    row = db.get_sponsor_state(discord_id)
    return int(row["tier"] or 0) if row else 0

def sponsor_rights_tier(discord_id):
    """Stored rights, including an open grace period."""
    return rights_tier(db.get_sponsor_state(discord_id))

def status_addendum(discord_id, lang):
    """Word the seven-day role wait and one-day grace for the free tier."""
    row = db.get_sponsor_state(discord_id)
    if row is None:
        return ""
    from utils import localized
    effective = sponsor_tier(discord_id)
    if row["tier"] == -1 and effective == 0 and row["community_role_since"]:
        days = max(1, math.ceil((COMMUNITY_WAIT_SECONDS -
                                 (time.time() - row["community_role_since"])) / 86400))
        return localized("sponsor_community_pending", lang, days=days)
    if row["tier"] == 0 and row["grace_since"]:
        former = int(row["grace_from_tier"] or 0)
        window = COMMUNITY_GRACE_SECONDS if former == -1 else SPONSOR_GRACE_SECONDS
        left = window - (time.time() - int(row["grace_since"]))
        if left > 0:
            return localized("sponsor_community_grace" if former == -1
                             else "sponsor_grace_active", lang,
                             days=math.ceil(left / 86400))
    if row["tier"] == 0 and effective == 0 and row["retention_due_at"]:
        return localized("sponsor_retention_warning", lang)
    return ""

def apply_tier(discord_id, live_tier):
    """Record a role change; never remove a ban or a network identity."""
    now = int(time.time())
    row = db.get_sponsor_state(discord_id)
    live_tier = int(live_tier)
    if row is None:
        if live_tier:
            db.save_sponsor_state(discord_id, live_tier)
        return None
    previous = int(row["tier"] or 0)
    if previous == live_tier:
        return None
    in_force = rights_tier(row)
    changed_at = row["slot_changed_at"]
    if _rank(live_tier) >= _rank(in_force):
        db.save_sponsor_state(discord_id, live_tier,
                              slot_changed_at=changed_at)
        db.set_retention_due(discord_id, None)
        return "restored" if row["grace_since"] else None
    if _rank(live_tier) > _rank(previous):
        db.save_sponsor_state(discord_id, live_tier,
                              row["grace_from_tier"], row["grace_since"],
                              changed_at)
        if live_tier != 0:
            db.set_retention_due(discord_id, None)
        return None
    db.save_sponsor_state(discord_id, live_tier, in_force, now, changed_at)
    if live_tier == 0:
        grace = COMMUNITY_GRACE_SECONDS if in_force == -1 else SPONSOR_GRACE_SECONDS
        db.set_retention_due(discord_id, now + grace + SPONSOR_RETENTION_SECONDS)
    else:
        db.set_retention_due(discord_id, None)
    return "frozen" if live_tier == 0 else "downgraded"

def expire_grace(row):
    """Release surplus slots after 30 days, preserving all bans and networks."""
    since = row["grace_since"]
    window = COMMUNITY_GRACE_SECONDS if int(row["grace_from_tier"] or 0) == -1 else SPONSOR_GRACE_SECONDS
    if not since or time.time() - int(since) < window:
        return False
    owner = str(row["discord_id"])
    tier = int(row["tier"] or 0)
    limits = sponsor_limits(tier)
    networks = db.sponsor_networks(owner)
    for claim in networks[limits["networks"]:]:
        db.release_network(claim["network"], owner)
    while db.sponsor_server_usage(owner) > limits["servers"]:
        direct = db.sponsor_guilds(owner)
        if direct:
            db.release_sponsor_guild(direct[-1]["guild_id"], owner)
            continue
        remaining = db.sponsor_networks(owner)
        if not remaining:
            break
        db.release_network(remaining[-1]["network"], owner)
    db.save_sponsor_state(owner, tier, slot_changed_at=row["slot_changed_at"])
    return True

async def reconcile_pass():
    """Restore stored tiers from roles and advance finished grace clocks."""
    live = await live_tier_table()
    if live is None:
        return None
    notices = []
    known = {str(row["discord_id"]) for row in db.get_known_sponsors()}
    for owner in sorted(known | set(live)):
        kind = apply_tier(owner, live.get(owner, 0))
        if kind:
            notices.append((owner, kind))
    for row in db.get_known_sponsors():
        if expire_grace(row):
            notices.append((str(row["discord_id"]), "expired"))
    return notices

async def cleanup_due_guilds():
    """Leave lapsed sponsor servers after grace plus thirty more days.

    A fresh role read and exact ownership check happen before each departure;
    any uncertainty keeps the server and its data intact for the next pass.
    The database purge follows a successful leave or a confirmed API result
    that the bot no longer has access to the server.
    """
    import discord
    from discord_bot import bot

    protected = {str(PATREON_GUILD_ID), str(PURGATORIUM_GUILD_ID)}
    for row in db.due_sponsor_owners():
        owner = str(row["discord_id"])
        live = await live_discord_tier(owner)
        if live is None:
            return
        apply_tier(owner, live)
        if live != 0 or sponsor_tier(owner) != 0:
            continue
        for guild_id in db.lapsed_owner_guilds(owner):
            if guild_id in protected or not db.lapsed_guild_owned_by(guild_id, owner):
                continue
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                try:
                    guild = await bot.fetch_guild(int(guild_id))
                except discord.NotFound:
                    guild = None
                except discord.HTTPException as error:
                    logger.warning("expired sponsor guild lookup failed (%s): %s",
                                   guild_id, error)
                    continue
            try:
                if guild is not None:
                    await guild.leave()
            except Exception as error:
                logger.warning("expired sponsor guild leave failed (%s): %s",
                               guild_id, error)
                continue
            if db.purge_lapsed_guild(guild_id, owner):
                logger.info("expired sponsor guild removed (%s)", guild_id)

async def send_notice(discord_id, kind):
    """Explain a role or grace change privately without blocking moderation."""
    if kind not in {"frozen", "downgraded", "restored", "expired"}:
        return
    from discord_bot import bot
    try:
        user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
        if kind == "frozen":
            row = db.get_sponsor_state(discord_id)
            key = ("sponsor_notice_community_lost"
                   if row and row["grace_from_tier"] == -1
                   else "sponsor_notice_paid_lost")
        elif kind == "expired":
            key = "sponsor_notice_retention"
        else:
            key = f"sponsor_notice_{kind}"
        await user.send(localized(key, DEFAULT_LANG))
    except Exception as error:
        logger.info("sponsor notice failed for %s: %s", discord_id, error)

def can_manage_network(discord_id, network, *, new_work=False):
    """Whether this sponsor owns an active slot for this network.

    New network bans and settings keep working for thirty days after payment.
    """
    row = db.get_network(network)
    claim = db.get_network_claim(network)
    if not row or str(row["owner_id"]) != str(discord_id):
        return False
    if not claim or str(claim["discord_id"]) != str(discord_id):
        return False
    tier = sponsor_tier(discord_id) if new_work else sponsor_rights_tier(discord_id)
    return tier != 0

def can_add_server(discord_id, guild_id):
    """Whether a new guild may consume this account's server quota."""
    existing = db.get_sponsor_guild(guild_id)
    if existing and str(existing["discord_id"]) == str(discord_id):
        return True
    return db.sponsor_server_usage(discord_id) < sponsor_limits(
        sponsor_tier(discord_id))["servers"]

def claim_server_status(discord_id, guild_id):
    """Return a refusal code or 'ok' for a new server slot."""
    tier = sponsor_tier(discord_id)
    if tier == 0:
        return "sponsor_not_active"
    existing = db.get_sponsor_guild(guild_id)
    if existing:
        return "sponsor_already_claimed" if str(existing["discord_id"]) == str(discord_id) else "sponsor_taken"
    guild = db.get_guild(guild_id)
    if guild and guild["network"] is not None:
        network = db.get_network(guild["network"])
        if (network and network["owner_id"] is not None
                and str(network["owner_id"]) != str(discord_id)
                and sponsor_rights_tier(network["owner_id"]) != 0):
            return "sponsor_taken"
    row = db.get_sponsor_state(discord_id)
    changed = row["slot_changed_at"] if row else None
    if changed and time.time() - int(changed) < SPONSOR_SLOT_CHANGE_SECONDS:
        return "sponsor_rotation_wait"
    if not can_add_server(discord_id, guild_id):
        return "sponsor_server_limit"
    return "ok"

def guild_service_active(guild_id):
    """Pause sponsor moderation after the paid tier and grace both end."""
    guild = db.get_guild(guild_id)
    if guild and guild["network"] is not None:
        identity = db.get_network(guild["network"])
        if identity and identity["owner_id"] is not None:
            claim = db.get_network_claim(guild["network"])
            if (claim is None or str(claim["discord_id"]) != str(identity["owner_id"])
                    or sponsor_rights_tier(identity["owner_id"]) == 0):
                return False
    direct = db.sponsor_guild_record(guild_id)
    if direct and (not direct["active"] or sponsor_rights_tier(direct["discord_id"]) == 0):
        return False
    return True

def limit_text(owner, lang):
    """Only the authenticated subscription's current server/network usage."""
    caps = sponsor_limits(sponsor_tier(owner))
    return localized("limit_status", lang, servers=db.sponsor_server_usage(owner),
                     server_limit=caps["servers"], networks=len(db.sponsor_networks(owner)),
                     network_limit=caps["networks"])
