"""Everything the bot remembers about a ban: the local ones it still has to
lift, the network-wide ones, the ledger of which servers a network ban was
actually applied on, and the history it keeps after a ban is gone.

Four tables with four different lifetimes, which is the whole reason they are
separate:

* `active_bans` — bans with a timer the bot owes someone an unban for. A row
  disappears the moment the ban is lifted.
* `global_bans` — one ban per (network, user), the network's own record. It
  outlives the local bans made in its name.
* `gban_enforcements` — "this server banned this user *because* of the
  network". The ledger /setgbans disable reads, and nothing else.
* `ban_history` — "this server banned this user, once". It outlives the ban
  itself and feeds the prior-ban alert; it is deleted only when a ban is
  deliberately reverted.

This module stores; it does not ban. Delivering a ban to Discord, DMing the
user and walking a network is discord_bot/bans.py.
"""
import time

from db import conn, cur

def add_active_ban(guild_id, user_id, unban_at):
    """Record a ban this server will have to lift.

    unban_at None means permanent — the row is still written, because the
    Purgatorium gate reads it to decide whether an arriving user has anything
    to appeal, and because /unban needs something to clear.
    """
    cur.execute(
        "INSERT OR REPLACE INTO active_bans (guild_id, user_id, unban_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), unban_at)
    )
    conn.commit()

def remove_active_ban(guild_id, user_id):
    """Forget a local ban: it expired, or somebody lifted it. Does not unban
    on Discord — the caller does that, and it must, since a row removed
    without the unban leaves a ban nobody will ever lift."""
    cur.execute(
        "DELETE FROM active_bans WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def get_expired_bans():
    """Local bans whose time is up, for the minute-by-minute unban sweep.

    Permanent bans (unban_at NULL) are excluded by the IS NOT NULL test, so
    they are never picked up here.
    """
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM active_bans WHERE unban_at IS NOT NULL AND unban_at <= ?",
        (now,)
    ).fetchall()

def get_user_active_bans(user_id):
    """Active local bans of this user on any guild, most recent unban first.

    A NULL unban_at means a permanent ban and counts as active. Joined with
    guilds so callers get the banning server's language without extra lookups.
    """
    now = int(time.time())
    return cur.execute(
        """
        SELECT ab.guild_id AS guild_id, ab.unban_at AS unban_at, g.lang AS lang
        FROM active_bans ab
        LEFT JOIN guilds g ON g.guild_id = ab.guild_id
        WHERE ab.user_id = ? AND (ab.unban_at IS NULL OR ab.unban_at > ?)
        ORDER BY (ab.unban_at IS NULL) DESC, ab.unban_at DESC
        """,
        (str(user_id), now)
    ).fetchall()

def record_ban(guild_id, user_id):
    """Record that a user was banned on a guild (kept after unban, for network history)."""
    cur.execute(
        "INSERT OR REPLACE INTO ban_history (guild_id, user_id, banned_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), int(time.time()))
    )
    conn.commit()

def remove_ban_history(guild_id, user_id):
    """Forget that a user was banned on a guild.

    Called when a ban is lifted (local /unban or network /globalunban) so the
    'previously banned in this network' admin notice stops firing for a user
    whose ban has been deliberately reverted.
    """
    cur.execute(
        "DELETE FROM ban_history WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def get_user_ban_history(user_id):
    """Ban-history rows of this user across all guilds (guild_id, banned_at)."""
    return cur.execute(
        "SELECT guild_id, banned_at FROM ban_history WHERE user_id=?",
        (str(user_id),)
    ).fetchall()

def remove_user_ban_history(user_id):
    """Forget every ban-history row of this user (consul pardon reverts the bans
    deliberately, so the 'previously banned in this network' notice must stop)."""
    cur.execute("DELETE FROM ban_history WHERE user_id=?", (str(user_id),))
    conn.commit()

def get_network_ban_history(network, user_id):
    """Return ban-history rows for this user across all guilds in the given network,
    most recent first. Each row has guild_id and banned_at."""
    return cur.execute(
        """
        SELECT bh.guild_id AS guild_id, bh.banned_at AS banned_at
        FROM ban_history bh
        JOIN guilds g ON g.guild_id = bh.guild_id
        WHERE g.network = ? AND bh.user_id = ?
        ORDER BY bh.banned_at DESC
        """,
        (network, str(user_id))
    ).fetchall()

def add_global_ban(network, user_id, reason, origin_guild_id, banned_at, unban_at):
    """Write the network's own ban record.

    INSERT OR REPLACE: a second /globalban for the same user in the same
    network overwrites the first, which is how a term is corrected and how the
    tribunal's ten-year verdict replaces whatever a moderator set. The
    per-server bans made in this ban's name are separate rows in active_bans
    and are not touched here.
    """
    cur.execute(
        """
        INSERT OR REPLACE INTO global_bans
        (network, user_id, reason, origin_guild_id, banned_at, unban_at)
        VALUES (?,?,?,?,?,?)
        """,
        (network, str(user_id), reason, str(origin_guild_id) if origin_guild_id is not None else None,
         banned_at, unban_at)
    )
    conn.commit()

def get_global_ban(network, user_id):
    """The network's ban record for this user, expired or not.

    Returns a row for a ban whose unban_at has already passed but which the
    expiry sweep has not yet collected — /globalunban wants exactly that, so
    that lifting a just-expired ban still cleans up the servers it reached.
    For "is this user banned right now" use get_active_global_ban().
    """
    return cur.execute(
        "SELECT * FROM global_bans WHERE network=? AND user_id=?",
        (network, str(user_id))
    ).fetchone()

def get_active_global_ban(network, user_id):
    """Return the global ban for this user/network only if it has not yet expired."""
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM global_bans WHERE network=? AND user_id=? AND unban_at > ?",
        (network, str(user_id), now)
    ).fetchone()

def get_active_global_bans_for_network(network):
    """Every unexpired ban of one network.

    The backlog /setgbans enable works through: it is matched against the
    server's current member list, so the size of this result is the number the
    command reports, not the number of bans it applied.
    """
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM global_bans WHERE network=? AND unban_at > ?",
        (network, now)
    ).fetchall()

def get_user_active_global_bans(user_id):
    """Active global bans of this user across all networks, most recent first.

    Joined with guilds on the origin guild so callers get the language of the
    server that issued the global ban.
    """
    now = int(time.time())
    return cur.execute(
        """
        SELECT gb.network AS network, gb.origin_guild_id AS origin_guild_id,
               gb.banned_at AS banned_at, gb.unban_at AS unban_at, g.lang AS lang
        FROM global_bans gb
        LEFT JOIN guilds g ON g.guild_id = gb.origin_guild_id
        WHERE gb.user_id = ? AND gb.unban_at > ?
        ORDER BY gb.banned_at DESC
        """,
        (str(user_id), now)
    ).fetchall()

def get_user_global_bans(user_id):
    """All still-active global bans of this user with full details (reason,
    origin guild, dates), for the appeal ban-info summary.

    "Active" means the same here as in get_active_global_ban and
    get_user_active_global_bans: unban_at strictly in the future. The column
    is nullable but no version of the bot has ever written NULL into it — both
    writers pass now + a finite term — so the three queries agree.
    """
    now = int(time.time())
    return cur.execute(
        """
        SELECT network, user_id, reason, origin_guild_id, banned_at, unban_at
        FROM global_bans
        WHERE user_id = ? AND unban_at > ?
        """,
        (str(user_id), now)
    ).fetchall()

def remove_global_ban(network, user_id):
    """Delete a network's ban record. Only the record: unbanning on the
    servers that enforced it is _execute_global_unban's job, and it reads
    gban_enforcements to know which those were — so this must not run first
    from anywhere else."""
    cur.execute(
        "DELETE FROM global_bans WHERE network=? AND user_id=?",
        (network, str(user_id))
    )
    conn.commit()

def cleanup_expired_global_bans():
    """Drop network ban records whose term has run out.

    Runs every minute next to the local unban sweep. The local bans made in
    their name expire on their own timers in active_bans, which is why nothing
    has to be unbanned here.
    """
    now = int(time.time())
    cur.execute("DELETE FROM global_bans WHERE unban_at IS NOT NULL AND unban_at <= ?", (now,))
    conn.commit()

def add_gban_enforcement(guild_id, user_id):
    """Mark that this server banned this user *because of* its network.

    The mark is what makes /setgbans disable reversible and precise: without
    it, turning enforcement off could only choose between lifting every ban on
    the server or none.
    """
    cur.execute(
        "INSERT OR IGNORE INTO gban_enforcements (guild_id, user_id) VALUES (?,?)",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def remove_gban_enforcement(guild_id, user_id):
    """Clear one enforcement mark — the ban was lifted, expired, or the local
    admin overrode it with /unban."""
    cur.execute(
        "DELETE FROM gban_enforcements WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def get_gban_enforcements(guild_id):
    """User ids this server banned on behalf of its network, as strings.

    Read twice: by /setgbans disable to know what to lift, and by
    _execute_global_unban to know which servers a lifted network ban has to
    reach.
    """
    return [
        r["user_id"] for r in cur.execute(
            "SELECT user_id FROM gban_enforcements WHERE guild_id=?",
            (str(guild_id),)
        ).fetchall()
    ]

def clear_gban_enforcements(guild_id):
    """Drop every enforcement mark of one server, after /setgbans disable has
    lifted the bans they stood for."""
    cur.execute("DELETE FROM gban_enforcements WHERE guild_id=?", (str(guild_id),))
    conn.commit()
