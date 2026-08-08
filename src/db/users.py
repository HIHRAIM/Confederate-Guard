"""Verification state: the per-server configuration, the cross-server set of
verified users, the per-server record that the role was already handed out,
and the activity dates that earn verification in the first place.

The important asymmetry is scope. `verify_settings`, `verify_grants` and
`user_activity` are per server; `verified_users` is not — it is one shared set
for every server the bot is on, and it is also the table bridge_bot writes
into through the sync channels. That is why verifying somewhere grants a role
everywhere, and why removing someone from the set does not take any role back:
the set says "this person is known", the grants say "this server has already
acted on it".

The policy on top of these rows lives in discord_bot/verification.py.
"""
import time

from db import conn, cur

def set_verify(guild_id, role_id, channel_id=None):
    """Configure activity-based verification on this server (/setverify).

    channel_id is optional; when it is None the announcements fall back to the
    /setup log channel, which is why the command can be run with a role alone.
    """
    cur.execute(
        "INSERT OR REPLACE INTO verify_settings (guild_id, role_id, channel_id) VALUES (?,?,?)",
        (str(guild_id), str(role_id), str(channel_id) if channel_id is not None else None)
    )
    conn.commit()

def get_verify(guild_id):
    """The server's verification settings, or None when it never ran
    /setverify.

    None is the switch that turns the whole mechanism off for this server: the
    activity tracker, the join handler and the cross-server fan-out all check
    it first and skip a server without a row.
    """
    return cur.execute(
        "SELECT * FROM verify_settings WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()

def is_verified(user_id):
    """Global, cross-server verification status."""
    row = cur.execute(
        "SELECT 1 FROM verified_users WHERE user_id=?",
        (str(user_id),)
    ).fetchone()
    return row is not None

def get_verified_origin(user_id):
    """Guild where the user's verification originated, or None if unknown."""
    row = cur.execute(
        "SELECT origin_guild_id FROM verified_users WHERE user_id=?",
        (str(user_id),)
    ).fetchone()
    return int(row["origin_guild_id"]) if row and row["origin_guild_id"] else None

def add_verified(user_id, origin_guild_id=None):
    """Add a user to the shared verified set.

    INSERT OR IGNORE, so a second verification — say activity here after a
    sync from bridge_bot — cannot overwrite the recorded origin, and the
    announcement wording stays true to where it first happened. origin None is
    a legitimate value: a sync line without a server id, or a verification
    that happened on Telegram, has no origin this bot could name.
    """
    cur.execute(
        "INSERT OR IGNORE INTO verified_users (user_id, origin_guild_id, verified_at) VALUES (?,?,?)",
        (str(user_id), str(origin_guild_id) if origin_guild_id is not None else None, int(time.time()))
    )
    conn.commit()

def remove_verified(user_id):
    """Remove a user from the cross-server verified database.

    Only clears verified status; per-server grant markers (verify_grants) and any
    already-assigned Discord roles are intentionally left untouched.
    """
    cur.execute("DELETE FROM verified_users WHERE user_id=?", (str(user_id),))
    conn.commit()
    return cur.rowcount > 0

def has_verify_grant(guild_id, user_id):
    """Whether the verify role was already granted and announced on this server."""
    row = cur.execute(
        "SELECT 1 FROM verify_grants WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    return row is not None

def add_verify_grant(guild_id, user_id):
    """Mark this server as done with this user.

    Written by every grant path including the silent startup backfill — that
    is what makes the backfill idempotent across restarts and stops the user's
    next message from producing a second announcement here.
    """
    cur.execute(
        "INSERT OR IGNORE INTO verify_grants (guild_id, user_id, granted_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), int(time.time()))
    )
    conn.commit()

def get_first_seen(guild_id, user_id):
    """The first calendar date (UTC, ISO string) this member posted a genuine
    message here, or None if they never have.

    None and "a date different from today" are the two branches of the whole
    activity rule: the first records today, the second verifies.
    """
    row = cur.execute(
        "SELECT first_date FROM user_activity WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    return row["first_date"] if row else None

def set_first_seen(guild_id, user_id, date_str):
    """Record the member's first activity date. INSERT OR IGNORE — the first
    date must never move forward, or a member could keep resetting their own
    clock by writing daily and never reach a second date."""
    cur.execute(
        "INSERT OR IGNORE INTO user_activity (guild_id, user_id, first_date) VALUES (?,?,?)",
        (str(guild_id), str(user_id), date_str)
    )
    conn.commit()
