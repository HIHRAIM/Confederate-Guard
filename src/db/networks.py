"""Stable network ownership and the two-sided invitation to join a network.

The guild's ``network`` column is its membership. A ``networks`` row is the
identity and owner, retained even after every guild has left because ban
history and enforcement still refer to that number.
"""
import time

from db import conn, cur

INVITE_SECONDS = 7 * 86400

def create_network(owner_id, name):
    """Allocate a never-reused network number with one SQLite write."""
    now = int(time.time())
    claimed = cur.execute(
        "INSERT INTO networks (network, owner_id, name, created_at)"
        " SELECT COALESCE(MAX(network), 0) + 1, ?, ?, ? FROM networks",
        (str(owner_id) if owner_id is not None else None, name, now)
    )
    conn.commit()
    return int(claimed.lastrowid)

def ensure_operator_network(network):
    """Let a Bot Admin name a number while recording it permanently."""
    cur.execute(
        "INSERT OR IGNORE INTO networks (network, owner_id, name, created_at)"
        " VALUES (?, NULL, '', ?)", (int(network), int(time.time()))
    )
    conn.commit()

def get_network(network):
    """A network's stable identity, or None."""
    return cur.execute(
        "SELECT * FROM networks WHERE network=?", (int(network),)
    ).fetchone()

def owner_networks(discord_id):
    """All network numbers owned by this Discord account, oldest first."""
    return cur.execute(
        "SELECT * FROM networks WHERE owner_id=? ORDER BY created_at, network",
        (str(discord_id),)
    ).fetchall()

def rename_network(network, name):
    """Change a network's display name, never its number or owner."""
    cur.execute("UPDATE networks SET name=? WHERE network=?", (name, int(network)))
    conn.commit()

def transfer_network(network, new_owner):
    """Transfer management without moving guilds or bans."""
    cur.execute(
        "UPDATE networks SET owner_id=? WHERE network=?",
        (str(new_owner) if new_owner is not None else None, int(network))
    )
    conn.commit()

def invite_guild(network, guild_id, invited_by):
    """Invite exactly one guild for seven days; acceptance is a second act."""
    cur.execute(
        "INSERT INTO network_invites (network, guild_id, invited_by, expires_at, accepted_at)"
        " VALUES (?,?,?,?,NULL) ON CONFLICT(network, guild_id) DO UPDATE SET"
        " invited_by=excluded.invited_by, expires_at=excluded.expires_at,"
        " accepted_at=NULL",
        (int(network), str(guild_id), str(invited_by), int(time.time()) + INVITE_SECONDS)
    )
    conn.commit()

def get_invite(network, guild_id):
    """An unexpired invitation, including whether the guild accepted it."""
    return cur.execute(
        "SELECT * FROM network_invites WHERE network=? AND guild_id=?"
        " AND expires_at>=?",
        (int(network), str(guild_id), int(time.time()))
    ).fetchone()

def accept_invite(network, guild_id):
    """Record the server's consent if an invitation is still live."""
    changed = cur.execute(
        "UPDATE network_invites SET accepted_at=? WHERE network=? AND guild_id=?"
        " AND expires_at>=?",
        (int(time.time()), int(network), str(guild_id), int(time.time()))
    )
    conn.commit()
    return bool(changed.rowcount)

def revoke_invite(network, guild_id):
    """Stop an unconsumed invitation without changing existing membership."""
    changed = cur.execute(
        "DELETE FROM network_invites WHERE network=? AND guild_id=?",
        (int(network), str(guild_id))
    )
    conn.commit()
    return bool(changed.rowcount)

def network_guild_count(network):
    """Number of registered guilds in a network."""
    return cur.execute(
        "SELECT COUNT(*) FROM guilds WHERE network=?", (int(network),)
    ).fetchone()[0]

def network_ban_count(network):
    """Number of current global ban records for a network."""
    return cur.execute(
        "SELECT COUNT(*) FROM global_bans WHERE network=?", (int(network),)
    ).fetchone()[0]

def leave_network(guild_id, network):
    """Remove one guild's membership, preserving its registration and bans."""
    changed = cur.execute(
        "UPDATE guilds SET network=NULL WHERE guild_id=? AND network=?",
        (str(guild_id), int(network))
    )
    conn.commit()
    return bool(changed.rowcount)
