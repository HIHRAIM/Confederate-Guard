"""The one resolver for a network's Purgatorium and its protocol channels.

Operator networks keep config.py as their default. Sponsor networks start
unconfigured and never inherit the operator's appeal server or channels.
"""
import time

from config import (
    APPEAL_BANINFO_CHANNELS, APPEAL_PARDON_CHANNELS, CONSULS,
    PURGATORIUM_GUILD_ID, PURGATORIUM_INVITE_URL, TRIBUNAL_CHANNELS,
)
from db import conn, cur

_FIELDS = {"guild_id", "invite_url", "appeal_text", "pardon_channel_id",
           "baninfo_channel_id", "tribunal_channel_id", "consul_roles"}

def set_network_setting(network, field, value):
    """Store one vetted setting for a network without replacing the others."""
    if field not in _FIELDS:
        raise ValueError(field)
    cur.execute(
        f"INSERT INTO network_purgatorium (network, {field}) VALUES (?,?)"
        f" ON CONFLICT(network) DO UPDATE SET {field}=excluded.{field}",
        (int(network), str(value) if value is not None else None)
    )
    conn.commit()

def network_settings(network):
    """All appeal settings with operator defaults where appropriate."""
    identity = cur.execute(
        "SELECT owner_id FROM networks WHERE network=?", (int(network),)
    ).fetchone()
    if identity is None:
        return None
    row = cur.execute(
        "SELECT * FROM network_purgatorium WHERE network=?", (int(network),)
    ).fetchone()
    operator = identity["owner_id"] is None
    shared = None
    if not operator:
        shared = cur.execute(
            "SELECT p.* FROM network_purgatorium p JOIN networks n"
            " ON n.network=p.network WHERE n.owner_id=?"
            " AND p.guild_id IS NOT NULL ORDER BY p.network LIMIT 1",
            (identity["owner_id"],)
        ).fetchone()

    def value(field, fallback=None):
        """Stored value wins; only operator networks get config defaults."""
        stored = row[field] if row else None
        if stored is not None:
            return stored
        if operator:
            return fallback
        return shared[field] if shared else None

    return {
        "guild_id": value("guild_id", str(PURGATORIUM_GUILD_ID)),
        "invite_url": value("invite_url", PURGATORIUM_INVITE_URL),
        "appeal_text": value("appeal_text"),
        "pardon_channels": ({value("pardon_channel_id")} if value("pardon_channel_id")
                            else {str(item) for item in APPEAL_PARDON_CHANNELS.get("discord", set())}
                            if operator else set()),
        "baninfo_channels": ({value("baninfo_channel_id")} if value("baninfo_channel_id")
                             else {str(item) for item in APPEAL_BANINFO_CHANNELS.get("discord", set())}
                             if operator else set()),
        "tribunal_channels": ({value("tribunal_channel_id")} if value("tribunal_channel_id")
                              else {str(item) for item in TRIBUNAL_CHANNELS.get("discord", set())}
                              if operator else set()),
        "consul_roles": {int(item) for item in (value("consul_roles", ",".join(str(x) for x in CONSULS)) or "").split(",") if item.strip().isdigit()},
    }

def purgatorium_scope(guild_id):
    """Return ``(found, network)`` for an appeal server, operator as None."""
    row = cur.execute(
        "SELECT p.network, n.owner_id FROM network_purgatorium p"
        " JOIN networks n ON n.network=p.network WHERE p.guild_id=?",
        (str(guild_id),)
    ).fetchone()
    if row:
        return True, int(row["network"]) if row["owner_id"] is not None else None
    if str(guild_id) == str(PURGATORIUM_GUILD_ID):
        return True, None
    return False, None

def channel_scope(channel_id, kind):
    """Return ``(found, network)`` for a protocol channel of one kind."""
    column = {"pardon": "pardon_channel_id", "baninfo": "baninfo_channel_id",
              "tribunal": "tribunal_channel_id"}.get(kind)
    if column is None:
        raise ValueError(kind)
    row = cur.execute(
        f"SELECT p.network, n.owner_id FROM network_purgatorium p"
        f" JOIN networks n ON n.network=p.network WHERE p.{column}=?",
        (str(channel_id),)
    ).fetchone()
    if row:
        return True, int(row["network"]) if row["owner_id"] is not None else None
    defaults = {"pardon": APPEAL_PARDON_CHANNELS,
                "baninfo": APPEAL_BANINFO_CHANNELS,
                "tribunal": TRIBUNAL_CHANNELS}[kind]
    return (str(channel_id) in {str(item) for item in defaults.get("discord", set())}, None)

def scope_networks(network):
    """Network numbers reachable from this Purgatorium's owner."""
    if network is None:
        return [int(row[0]) for row in cur.execute(
            "SELECT network FROM networks WHERE owner_id IS NULL"
        ).fetchall()]
    owner = cur.execute(
        "SELECT owner_id FROM networks WHERE network=?", (int(network),)
    ).fetchone()
    if owner is None or owner["owner_id"] is None:
        return []
    return [int(row[0]) for row in cur.execute(
        "SELECT network FROM networks WHERE owner_id=?", (owner["owner_id"],)
    ).fetchall()]

def scope_guild_ids(network):
    """Server IDs in the networks belonging to this appeal scope."""
    networks = scope_networks(network)
    if not networks:
        return {str(row[0]) for row in cur.execute(
            "SELECT guild_id FROM guilds WHERE network IS NULL"
        ).fetchall()} if network is None else set()
    placeholders = ",".join("?" for _ in networks)
    found = {str(row[0]) for row in cur.execute(
        f"SELECT guild_id FROM guilds WHERE network IN ({placeholders})",
        networks
    ).fetchall()}
    if network is None:
        found.update(str(row[0]) for row in cur.execute(
            "SELECT guild_id FROM guilds WHERE network IS NULL"
        ).fetchall())
    return found

def all_purgatorium_guild_ids():
    """Every configured appeal server plus the operator default."""
    rows = cur.execute(
        "SELECT guild_id FROM network_purgatorium WHERE guild_id IS NOT NULL"
    ).fetchall()
    return {str(PURGATORIUM_GUILD_ID)} | {str(row[0]) for row in rows}

def protected_settings_ids():
    """IDs of sponsor appeal servers and protocol channels for retention."""
    rows = cur.execute(
        "SELECT guild_id, pardon_channel_id, baninfo_channel_id,"
        " tribunal_channel_id FROM network_purgatorium"
    ).fetchall()
    return {str(value) for row in rows for value in row if value}

def add_network_consul(network, user_id, added_by):
    """Appoint a consul to exactly one sponsor network."""
    cur.execute(
        "INSERT INTO network_consuls (network, user_id, added_by, added_at)"
        " VALUES (?,?,?,?) ON CONFLICT(network, user_id) DO UPDATE SET"
        " added_by=excluded.added_by, added_at=excluded.added_at",
        (int(network), str(user_id), str(added_by), int(time.time()))
    )
    conn.commit()

def remove_network_consul(network, user_id):
    """Remove only this network's appointment."""
    changed = cur.execute(
        "DELETE FROM network_consuls WHERE network=? AND user_id=?",
        (int(network), str(user_id))
    )
    conn.commit()
    return bool(changed.rowcount)

def is_network_consul(network, user_id):
    """Whether this account was appointed on one sponsor network."""
    return cur.execute(
        "SELECT 1 FROM network_consuls WHERE network=? AND user_id=?",
        (int(network), str(user_id))
    ).fetchone() is not None
