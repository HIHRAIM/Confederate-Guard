"""The servers the bot knows about and the per-server switches keyed to them:
the `guilds` registration itself, guarded channels and autoroles.

This is the "what does the bot do here" layer. Who may command it is in
db/admins.py, what it does to people is in db/bans.py and db/users.py, and the
free-text settings a server can type at it (custom DM, appeal text, banned
links) are in db/settings.py.

`guilds.network` is the only cross-server key in the whole database: it is
what makes a ban global, an alert possible and a tribunal case worth posting.
A server with network NULL is complete and functional, just alone.
"""
from db import conn, cur

def setup_guild(guild_id, lang, log_channel_id, network=None):
    """Register the server, or overwrite its registration (/setup).

    INSERT OR REPLACE, so running /setup again is how a server changes its
    language, its log channel or its network — including moving out of a
    network by omitting the argument. Nothing else is touched: the bans,
    admins and settings attached to the server survive re-registration.
    """
    cur.execute(
        "INSERT OR REPLACE INTO guilds (guild_id, lang, log_channel_id, network) VALUES (?,?,?,?)",
        (str(guild_id), lang, str(log_channel_id), network)
    )
    conn.commit()

def get_network_guilds(network):
    """Every registered server of one network.

    The fan-out list for network bans: /globalban, the tribunal verdict and
    _execute_global_unban all walk it. Servers of the network the bot is no
    longer in are still returned — the caller checks presence itself, since a
    row without a live guild is exactly what a ban that cannot be delivered
    looks like.
    """
    return cur.execute(
        "SELECT * FROM guilds WHERE network=?",
        (network,)
    ).fetchall()

def get_guild(guild_id):
    """The server's registration row, or None when it was never /setup.

    None is a meaningful answer everywhere, not an error: an unregistered
    server still gets spam-link deletion and still answers /help, it simply
    has no language, no log channel and no network.
    """
    return cur.execute(
        "SELECT * FROM guilds WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()

def count_guilds():
    """Number of servers registered via /setup (rows in guilds).

    This is the N in the rotating presence text, deliberately counting
    registrations rather than `bot.guilds` — servers that merely invited the
    bot are not communities it guards.
    """
    return cur.execute("SELECT COUNT(*) FROM guilds").fetchone()[0]

def set_guard(channel_id, guild_id, duration_seconds, reason):
    """Turn a channel into a guarded one (/guard), with the ban term and
    reason every automatic ban issued there will carry.

    duration_seconds None means a permanent ban, which is why the command
    accepts 'infinity' and stores nothing rather than a large number.
    """
    cur.execute(
        "INSERT OR REPLACE INTO guarded_channels (channel_id, guild_id, duration_seconds, reason) VALUES (?,?,?,?)",
        (str(channel_id), str(guild_id), duration_seconds, reason)
    )
    conn.commit()

def get_guard(channel_id):
    """The channel's guard row, or None. Read on *every* message the bot
    sees, which is why it is a single indexed primary-key lookup."""
    return cur.execute(
        "SELECT * FROM guarded_channels WHERE channel_id=?",
        (str(channel_id),)
    ).fetchone()

def set_autorole(guild_id, role_id):
    """Set the role handed to every member of this server (/autorole).

    One role per server: INSERT OR REPLACE, so the command is also how the
    role is changed.
    """
    cur.execute(
        "INSERT OR REPLACE INTO autoroles (guild_id, role_id) VALUES (?,?)",
        (str(guild_id), str(role_id))
    )
    conn.commit()

def get_autorole(guild_id):
    """The server's autorole id as a string, or None. Read on every join."""
    row = cur.execute(
        "SELECT role_id FROM autoroles WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["role_id"] if row else None

def remove_guild_data(guild_id):
    """Erase everything this server owns, across all thirteen tables holding
    a guild_id.

    Called by /force_leave and nothing else — deliberately not by
    on_guild_remove, because a kick-and-reinvite must not cost a server its
    log channel, its bans or its network membership. The cross-server tables
    are untouched: verified_users is shared and global_bans belongs to the
    network, not to the server that happened to issue the ban.
    """
    gid = str(guild_id)
    cur.execute("DELETE FROM guilds WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM guarded_channels WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM custom_dm WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM active_bans WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM autoroles WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM guild_admins WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM verify_settings WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM verify_grants WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM user_activity WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM gban_settings WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM gban_enforcements WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM appeal_messages WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM ban_history WHERE guild_id=?", (gid,))
    conn.commit()
