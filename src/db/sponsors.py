"""Stored Patreon tier and sponsor slots for Guard.

The Discord account is the identity. Network identities and bans remain when
slot claims expire, and manual guild_admins rows are kept separately.
"""
import time

from db import conn, cur

def observe_community_role(discord_id, present):
    """Track the first verified sighting of either qualifying community role."""
    owner = str(discord_id)
    now = int(time.time())
    row = get_sponsor_state(owner)
    if row is None and not present:
        return
    if row is None:
        cur.execute("INSERT INTO sponsor_state"
                    " (discord_id,tier,community_role_since,community_role_present,updated_at)"
                    " VALUES (?,0,?,1,?)", (owner, now, now))
    elif present and not row["community_role_present"]:
        cur.execute("UPDATE sponsor_state SET community_role_since=?,community_role_present=1"
                    " WHERE discord_id=?", (now, owner))
    elif not present and row["community_role_present"]:
        cur.execute("UPDATE sponsor_state SET community_role_present=0 WHERE discord_id=?",
                    (owner,))
    conn.commit()

def get_sponsor_state(discord_id):
    """Last observed tier and grace clock for a Discord account."""
    return cur.execute(
        "SELECT * FROM sponsor_state WHERE discord_id=?", (str(discord_id),)
    ).fetchone()

def save_sponsor_state(discord_id, tier, grace_from_tier=None, grace_since=None,
                       slot_changed_at=None):
    """Replace the coherent sponsor state in one write."""
    cur.execute(
        "INSERT INTO sponsor_state"
        " (discord_id, tier, grace_from_tier, grace_since, slot_changed_at, updated_at)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET"
        " tier=excluded.tier, grace_from_tier=excluded.grace_from_tier,"
        " grace_since=excluded.grace_since, slot_changed_at=excluded.slot_changed_at,"
        " updated_at=excluded.updated_at",
        (str(discord_id), int(tier), grace_from_tier, grace_since,
         slot_changed_at, int(time.time()))
    )
    conn.commit()

def touch_slot_change(discord_id):
    """Start the thirty-day wait before claiming a replacement server."""
    cur.execute(
        "UPDATE sponsor_state SET slot_changed_at=? WHERE discord_id=?",
        (int(time.time()), str(discord_id))
    )
    conn.commit()

def get_known_sponsors():
    """Accounts observed before, including those whose role disappeared."""
    return cur.execute("SELECT * FROM sponsor_state").fetchall()

def set_retention_due(discord_id, due_at):
    """Set or cancel the date when lapsed communities may be removed."""
    cur.execute("UPDATE sponsor_state SET retention_due_at=? WHERE discord_id=?",
                (int(due_at) if due_at is not None else None, str(discord_id)))
    conn.commit()

def due_sponsor_owners(now=None):
    """Expired accounts with a full additional month of retention elapsed."""
    return cur.execute(
        "SELECT * FROM sponsor_state WHERE tier=0 AND retention_due_at IS NOT NULL"
        " AND retention_due_at<=?", (int(now if now is not None else time.time()),)
    ).fetchall()

def lapsed_owner_guilds(discord_id):
    """Direct claims and guilds inside networks still owned by this sponsor."""
    owner = str(discord_id)
    return [str(row[0]) for row in cur.execute(
        "SELECT guild_id FROM sponsor_guilds WHERE discord_id=?"
        " UNION SELECT g.guild_id FROM guilds g JOIN networks n"
        " ON n.network=g.network WHERE n.owner_id=?", (owner, owner)
    ).fetchall()]

def lapsed_guild_owned_by(guild_id, discord_id):
    """Check the exact owner again immediately before a server departure."""
    guild = str(guild_id)
    owner = str(discord_id)
    state = get_sponsor_state(owner)
    if (state is None or int(state["tier"] or 0) != 0
            or state["retention_due_at"] is None
            or int(state["retention_due_at"]) > int(time.time())):
        return False
    record = sponsor_guild_record(guild)
    if record and str(record["discord_id"]) != owner:
        return False
    guild_row = cur.execute("SELECT network FROM guilds WHERE guild_id=?",
                            (guild,)).fetchone()
    network = guild_row["network"] if guild_row else None
    network_row = (cur.execute("SELECT owner_id FROM networks WHERE network=?",
                               (network,)).fetchone()
                   if network is not None else None)
    if network_row and str(network_row["owner_id"]) != owner:
        return False
    if not (record and str(record["discord_id"]) == owner
            or network_row and str(network_row["owner_id"]) == owner):
        return False
    return True

def purge_lapsed_guild(guild_id, discord_id):
    """Erase one departed sponsor server without touching other guilds.

    Network bans survive while any other guild remains in that network. Its
    ban origin is redacted when the origin guild leaves. A Bot Admin's move to
    an operator network or another sponsor's claim cancels this cleanup.
    """
    if not lapsed_guild_owned_by(guild_id, discord_id):
        return False
    guild = str(guild_id)
    owner = str(discord_id)
    guild_row = cur.execute("SELECT network FROM guilds WHERE guild_id=?",
                            (guild,)).fetchone()
    network = guild_row["network"] if guild_row else None
    network_row = (cur.execute("SELECT owner_id FROM networks WHERE network=?",
                               (network,)).fetchone()
                   if network is not None else None)

    appeal_networks = [int(row[0]) for row in cur.execute(
        "SELECT network FROM network_purgatorium WHERE guild_id=?", (guild,)
    ).fetchall()]
    for table in ("guarded_channels", "custom_dm", "active_bans",
                  "autoroles", "guild_admins", "verify_settings",
                  "verify_grants", "user_activity", "gban_settings",
                  "gban_enforcements", "appeal_messages", "ban_history",
                  "tribunal_cases", "network_invites"):
        cur.execute(f"DELETE FROM {table} WHERE guild_id=?", (guild,))
    cur.execute("DELETE FROM setup_deadlines WHERE platform='discord'"
                " AND server_id=?", (guild,))
    cur.execute("UPDATE verified_users SET origin_guild_id=NULL"
                " WHERE origin_guild_id=?", (guild,))
    cur.execute("UPDATE global_bans SET origin_guild_id=NULL"
                " WHERE origin_guild_id=?", (guild,))
    cur.execute("DELETE FROM network_purgatorium WHERE guild_id=?", (guild,))
    for item in appeal_networks:
        cur.execute("DELETE FROM baninfo_posts WHERE scope_network=?", (item,))
    cur.execute("DELETE FROM sponsor_guilds WHERE guild_id=? AND discord_id=?",
                (guild, owner))
    cur.execute("DELETE FROM guilds WHERE guild_id=?", (guild,))
    if network is not None and network_row and str(network_row["owner_id"]) == owner:
        if not cur.execute("SELECT 1 FROM guilds WHERE network=? LIMIT 1",
                           (network,)).fetchone():
            for table in ("global_bans", "network_invites",
                          "sponsor_network_claims", "network_purgatorium",
                          "network_consuls"):
                cur.execute(f"DELETE FROM {table} WHERE network=?", (network,))
            cur.execute("DELETE FROM baninfo_posts WHERE scope_network=?", (network,))
            cur.execute("DELETE FROM networks WHERE network=? AND owner_id=?",
                        (network, owner))
    conn.commit()
    return True

def get_sponsor_guild(guild_id):
    """Who claimed this server as a sponsor, if anyone."""
    return cur.execute(
        "SELECT * FROM sponsor_guilds WHERE guild_id=? AND active=1", (str(guild_id),)
    ).fetchone()

def sponsor_guild_record(guild_id):
    """Retained ownership marker, including a paused sponsor server."""
    return cur.execute("SELECT * FROM sponsor_guilds WHERE guild_id=?",
                       (str(guild_id),)).fetchone()

def clear_inactive_sponsor_guild(guild_id):
    """Bot Admin takeover removes only a paused sponsorship marker."""
    cur.execute("DELETE FROM sponsor_guilds WHERE guild_id=? AND active=0",
                (str(guild_id),))
    conn.commit()

def claim_sponsor_guild(guild_id, discord_id):
    """Claim a server without replacing another sponsor's claim."""
    cur.execute(
        "INSERT INTO sponsor_guilds (guild_id, discord_id, claimed_at,active)"
        " VALUES (?,?,?,1) ON CONFLICT(guild_id) DO UPDATE SET"
        " discord_id=excluded.discord_id,claimed_at=excluded.claimed_at,active=1"
        " WHERE active=0",
        (str(guild_id), str(discord_id), int(time.time()))
    )
    conn.commit()
    row = get_sponsor_guild(guild_id)
    return bool(row and str(row["discord_id"]) == str(discord_id))

def release_sponsor_guild(guild_id, discord_id):
    """Release only the caller's slot; manual admin rights are separate."""
    changed = cur.execute(
        "UPDATE sponsor_guilds SET active=0 WHERE guild_id=? AND discord_id=? AND active=1",
        (str(guild_id), str(discord_id))
    )
    conn.commit()
    return bool(changed.rowcount)

def sponsor_guilds(discord_id):
    """Directly claimed servers, oldest claim first."""
    return cur.execute(
        "SELECT * FROM sponsor_guilds WHERE discord_id=? AND active=1"
        " ORDER BY claimed_at, guild_id", (str(discord_id),)
    ).fetchall()

def sponsor_server_usage(discord_id):
    """Count the union of direct claims and guilds in claimed networks."""
    return cur.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT guild_id FROM sponsor_guilds WHERE discord_id=? AND active=1"
        " UNION SELECT g.guild_id FROM guilds g"
        " JOIN sponsor_network_claims c ON c.network=g.network"
        " WHERE c.discord_id=?)", (str(discord_id), str(discord_id))
    ).fetchone()[0]

def get_network_claim(network):
    """Active sponsor slot for a network, if any."""
    return cur.execute(
        "SELECT * FROM sponsor_network_claims WHERE network=?", (int(network),)
    ).fetchone()

def sponsor_networks(discord_id):
    """Claimed network slots, oldest first."""
    return cur.execute(
        "SELECT * FROM sponsor_network_claims WHERE discord_id=?"
        " ORDER BY claimed_at, network", (str(discord_id),)
    ).fetchall()

def claim_network(network, discord_id):
    """Take a network slot without displacing an existing claimant."""
    cur.execute(
        "INSERT OR IGNORE INTO sponsor_network_claims"
        " (network, discord_id, claimed_at) VALUES (?,?,?)",
        (int(network), str(discord_id), int(time.time()))
    )
    conn.commit()
    row = get_network_claim(network)
    return bool(row and str(row["discord_id"]) == str(discord_id))

def release_network(network, discord_id):
    """Release a slot while keeping the network's identity and bans."""
    changed = cur.execute(
        "DELETE FROM sponsor_network_claims WHERE network=? AND discord_id=?",
        (int(network), str(discord_id))
    )
    conn.commit()
    return bool(changed.rowcount)
