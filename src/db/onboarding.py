"""Bookkeeping of the seven-day setup deadline — when the bot was added to a
server, whether that server was ever registered, and the moment the rule
itself came into force — plus the retention sweep that expires stored user
ids.

The policy that reads the deadline rows lives in `setup_deadline.py`; this
module only stores and answers. The two live together because they are the
same clock seen from both ends: one decides how long the bot stays somewhere,
the other how long it remembers someone.

Two things are worth knowing about the deadline. `joined_at` is a record, not
the clock — the sweep measures from Discord's own `Guild.me.joined_at`, which
no restart can lose. And `rule_since` is planted on the first start of the
version that introduced the rule and never rewritten: every server the bot was
already sitting in joined before that moment and is out of the rule's reach
for good.
"""
import time

from db import conn, cur

USER_ID_RETENTION_SECONDS = 10 * 365 * 24 * 3600

def cleanup_expired_user_data():
    """Delete stored user IDs older than the retention window (10 years from insertion).

    Covers the verification subsystem, which is the only place user IDs are kept
    long-term: cross-server verified users, per-server grant markers, and the
    first-seen activity dates. Operational state (active_bans, guild_admins) is
    left untouched.
    """
    now = int(time.time())
    cutoff_ts = now - USER_ID_RETENTION_SECONDS
    cur.execute(
        "DELETE FROM verified_users WHERE verified_at IS NOT NULL AND verified_at < ?",
        (cutoff_ts,)
    )
    cur.execute(
        "DELETE FROM verify_grants WHERE granted_at IS NOT NULL AND granted_at < ?",
        (cutoff_ts,)
    )
    from datetime import date, timedelta
    cutoff_date = (date.today() - timedelta(days=USER_ID_RETENTION_SECONDS // 86400)).isoformat()
    cur.execute(
        "DELETE FROM user_activity WHERE first_date < ?",
        (cutoff_date,)
    )
    cur.execute(
        "DELETE FROM ban_history WHERE banned_at IS NOT NULL AND banned_at < ?",
        (cutoff_ts,)
    )
    conn.commit()

def rule_since():
    """The unix time the seven-day setup deadline came into force, planted on
    first call and stable ever after.

    Every server the bot was already in joined before that instant, and the
    sweep leaves those alone — which is what keeps a deployment from walking
    out of its own servers the day the rule ships."""
    row = cur.execute(
        "SELECT value FROM bot_settings WHERE key='setup_rule_since'"
    ).fetchone()
    if row and row["value"]:
        try:
            return int(row["value"])
        except ValueError:
            pass
    now = int(time.time())
    cur.execute(
        "INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('setup_rule_since', ?)",
        (str(now),)
    )
    conn.commit()
    return now

def record_join(guild_id, joined_at=None):
    """Remember that the bot has just been added to a server. Does nothing
    when a row already exists: a replayed GUILD_CREATE must not restart a
    deadline, least of all a settled one."""
    cur.execute(
        "INSERT OR IGNORE INTO setup_deadlines (platform, server_id, joined_at)"
        " VALUES ('discord',?,?)",
        (str(guild_id), int(joined_at if joined_at is not None else time.time()))
    )
    conn.commit()

def get_deadline_row(guild_id):
    """The server's deadline row, or None — which is what every server from
    before the rule looks like."""
    return cur.execute(
        "SELECT * FROM setup_deadlines WHERE platform='discord' AND server_id=?",
        (str(guild_id),)
    ).fetchone()

def mark_settled(guild_id):
    """Note that the server has been registered with /setup, which takes it
    out of the rule for good."""
    now = int(time.time())
    cur.execute(
        "INSERT INTO setup_deadlines (platform, server_id, joined_at, settled_at)"
        " VALUES ('discord',?,?,?)"
        " ON CONFLICT(platform, server_id) DO UPDATE SET settled_at=excluded.settled_at",
        (str(guild_id), now, now)
    )
    conn.commit()

def forget_deadline(guild_id):
    """Drop the row once the bot has left, so that a later re-invitation is a
    fresh seven days rather than a settlement inherited from last time."""
    cur.execute(
        "DELETE FROM setup_deadlines WHERE platform='discord' AND server_id=?",
        (str(guild_id),)
    )
    conn.commit()
