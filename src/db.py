import sqlite3
import time

conn = sqlite3.connect("guard.db", check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def init():
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY,
        lang TEXT NOT NULL DEFAULT 'en',
        log_channel_id TEXT NOT NULL,
        network INTEGER
    );

    CREATE TABLE IF NOT EXISTS guarded_channels (
        channel_id TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        duration_seconds INTEGER,
        reason TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS custom_dm (
        guild_id TEXT PRIMARY KEY,
        message TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS active_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        unban_at INTEGER,
        UNIQUE(guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS autoroles (
        guild_id TEXT PRIMARY KEY,
        role_id TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS guild_admins (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS banned_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        value TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS verify_settings (
        guild_id TEXT PRIMARY KEY,
        role_id TEXT NOT NULL,
        channel_id TEXT
    );

    CREATE TABLE IF NOT EXISTS verified_users (
        user_id TEXT PRIMARY KEY,
        origin_guild_id TEXT,
        verified_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS verify_grants (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        granted_at INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS user_activity (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        first_date TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS global_bans (
        network INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        reason TEXT,
        origin_guild_id TEXT,
        banned_at INTEGER,
        unban_at INTEGER,
        PRIMARY KEY (network, user_id)
    );

    CREATE TABLE IF NOT EXISTS gban_settings (
        guild_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS gban_enforcements (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS appeal_messages (
        guild_id TEXT PRIMARY KEY,
        message TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ban_history (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        banned_at INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS loc_suggestions (
        code TEXT PRIMARY KEY,
        platform TEXT,
        user_id TEXT,
        username TEXT,
        lang TEXT,
        rkey TEXT,
        suggestion TEXT,
        ui_lang TEXT,
        created_at INTEGER
    );
    """)
    conn.commit()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(guilds)").fetchall()]
    if "network" not in cols:
        cur.execute("ALTER TABLE guilds ADD COLUMN network INTEGER")
        conn.commit()
    grant_cols = [r[1] for r in cur.execute("PRAGMA table_info(verify_grants)").fetchall()]
    if "granted_at" not in grant_cols:
        cur.execute("ALTER TABLE verify_grants ADD COLUMN granted_at INTEGER")
        cur.execute("UPDATE verify_grants SET granted_at=? WHERE granted_at IS NULL", (int(time.time()),))
        conn.commit()

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

def setup_guild(guild_id, lang, log_channel_id, network=None):
    cur.execute(
        "INSERT OR REPLACE INTO guilds (guild_id, lang, log_channel_id, network) VALUES (?,?,?,?)",
        (str(guild_id), lang, str(log_channel_id), network)
    )
    conn.commit()

def get_network_guilds(network):
    return cur.execute(
        "SELECT * FROM guilds WHERE network=?",
        (network,)
    ).fetchall()

def get_guild(guild_id):
    return cur.execute(
        "SELECT * FROM guilds WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()

def set_guard(channel_id, guild_id, duration_seconds, reason):
    cur.execute(
        "INSERT OR REPLACE INTO guarded_channels (channel_id, guild_id, duration_seconds, reason) VALUES (?,?,?,?)",
        (str(channel_id), str(guild_id), duration_seconds, reason)
    )
    conn.commit()

def get_guard(channel_id):
    return cur.execute(
        "SELECT * FROM guarded_channels WHERE channel_id=?",
        (str(channel_id),)
    ).fetchone()

def remove_guard(channel_id):
    cur.execute("DELETE FROM guarded_channels WHERE channel_id=?", (str(channel_id),))
    conn.commit()

def set_custom_dm(guild_id, message):
    cur.execute(
        "INSERT OR REPLACE INTO custom_dm (guild_id, message) VALUES (?,?)",
        (str(guild_id), message)
    )
    conn.commit()

def get_custom_dm(guild_id):
    row = cur.execute(
        "SELECT message FROM custom_dm WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["message"] if row else None

def add_active_ban(guild_id, user_id, unban_at):
    cur.execute(
        "INSERT OR REPLACE INTO active_bans (guild_id, user_id, unban_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), unban_at)
    )
    conn.commit()

def remove_active_ban(guild_id, user_id):
    cur.execute(
        "DELETE FROM active_bans WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def set_autorole(guild_id, role_id):
    cur.execute(
        "INSERT OR REPLACE INTO autoroles (guild_id, role_id) VALUES (?,?)",
        (str(guild_id), str(role_id))
    )
    conn.commit()

def get_autorole(guild_id):
    row = cur.execute(
        "SELECT role_id FROM autoroles WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["role_id"] if row else None

def remove_autorole(guild_id):
    cur.execute("DELETE FROM autoroles WHERE guild_id=?", (str(guild_id),))
    conn.commit()

def add_guild_admin(guild_id, user_id):
    cur.execute(
        "INSERT OR IGNORE INTO guild_admins (guild_id, user_id) VALUES (?,?)",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def remove_guild_admin(guild_id, user_id):
    cur.execute(
        "DELETE FROM guild_admins WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def is_guild_admin(guild_id, user_id):
    row = cur.execute(
        "SELECT 1 FROM guild_admins WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    return row is not None

def add_banned_link(kind, value):
    cur.execute(
        "INSERT OR IGNORE INTO banned_links (kind, value) VALUES (?,?)",
        (kind, value)
    )
    conn.commit()
    return cur.rowcount > 0

def get_banned_links():
    return cur.execute(
        "SELECT * FROM banned_links ORDER BY id"
    ).fetchall()

def remove_banned_link(link_id):
    cur.execute("DELETE FROM banned_links WHERE id=?", (link_id,))
    conn.commit()
    return cur.rowcount > 0

def set_verify(guild_id, role_id, channel_id=None):
    cur.execute(
        "INSERT OR REPLACE INTO verify_settings (guild_id, role_id, channel_id) VALUES (?,?,?)",
        (str(guild_id), str(role_id), str(channel_id) if channel_id is not None else None)
    )
    conn.commit()

def get_verify(guild_id):
    return cur.execute(
        "SELECT * FROM verify_settings WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()

def remove_verify(guild_id):
    cur.execute("DELETE FROM verify_settings WHERE guild_id=?", (str(guild_id),))
    conn.commit()

def is_verified(user_id):
    """Global, cross-server verification status."""
    row = cur.execute(
        "SELECT 1 FROM verified_users WHERE user_id=?",
        (str(user_id),)
    ).fetchone()
    return row is not None

def add_verified(user_id, origin_guild_id=None):
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
    cur.execute(
        "INSERT OR IGNORE INTO verify_grants (guild_id, user_id, granted_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), int(time.time()))
    )
    conn.commit()

def get_first_seen(guild_id, user_id):
    row = cur.execute(
        "SELECT first_date FROM user_activity WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    return row["first_date"] if row else None

def set_first_seen(guild_id, user_id, date_str):
    cur.execute(
        "INSERT OR IGNORE INTO user_activity (guild_id, user_id, first_date) VALUES (?,?,?)",
        (str(guild_id), str(user_id), date_str)
    )
    conn.commit()

def record_ban(guild_id, user_id):
    """Record that a user was banned on a guild (kept after unban, for network history)."""
    cur.execute(
        "INSERT OR REPLACE INTO ban_history (guild_id, user_id, banned_at) VALUES (?,?,?)",
        (str(guild_id), str(user_id), int(time.time()))
    )
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
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM global_bans WHERE network=? AND unban_at > ?",
        (network, now)
    ).fetchall()

def remove_global_ban(network, user_id):
    cur.execute(
        "DELETE FROM global_bans WHERE network=? AND user_id=?",
        (network, str(user_id))
    )
    conn.commit()

def cleanup_expired_global_bans():
    now = int(time.time())
    cur.execute("DELETE FROM global_bans WHERE unban_at IS NOT NULL AND unban_at <= ?", (now,))
    conn.commit()

def set_gbans_enabled(guild_id, enabled):
    cur.execute(
        "INSERT OR REPLACE INTO gban_settings (guild_id, enabled) VALUES (?,?)",
        (str(guild_id), 1 if enabled else 0)
    )
    conn.commit()

def is_gbans_enabled(guild_id):
    row = cur.execute(
        "SELECT enabled FROM gban_settings WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return bool(row and row["enabled"])

def add_gban_enforcement(guild_id, user_id):
    cur.execute(
        "INSERT OR IGNORE INTO gban_enforcements (guild_id, user_id) VALUES (?,?)",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def remove_gban_enforcement(guild_id, user_id):
    cur.execute(
        "DELETE FROM gban_enforcements WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def get_gban_enforcements(guild_id):
    return [
        r["user_id"] for r in cur.execute(
            "SELECT user_id FROM gban_enforcements WHERE guild_id=?",
            (str(guild_id),)
        ).fetchall()
    ]

def clear_gban_enforcements(guild_id):
    cur.execute("DELETE FROM gban_enforcements WHERE guild_id=?", (str(guild_id),))
    conn.commit()

def set_appeal(guild_id, message):
    cur.execute(
        "INSERT OR REPLACE INTO appeal_messages (guild_id, message) VALUES (?,?)",
        (str(guild_id), message)
    )
    conn.commit()

def get_appeal(guild_id):
    row = cur.execute(
        "SELECT message FROM appeal_messages WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["message"] if row else None

def remove_guild_data(guild_id):
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

def get_expired_bans():
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM active_bans WHERE unban_at IS NOT NULL AND unban_at <= ?",
        (now,)
    ).fetchall()

def add_loc_suggestion(code, platform, user_id, username, lang, rkey, suggestion, ui_lang):
    cur.execute(
        "INSERT OR REPLACE INTO loc_suggestions "
        "(code, platform, user_id, username, lang, rkey, suggestion, ui_lang, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (code, platform, str(user_id), username, lang, rkey, suggestion, ui_lang, int(time.time()))
    )
    conn.commit()

def get_loc_suggestion(code):
    return cur.execute(
        "SELECT * FROM loc_suggestions WHERE code=?",
        (code,)
    ).fetchone()

def delete_loc_suggestion(code):
    cur.execute("DELETE FROM loc_suggestions WHERE code=?", (code,))
    conn.commit()

def cleanup_old_loc_suggestions(max_age_seconds=365 * 24 * 3600):
    """Localization-suggestion dialog codes are kept at most a year."""
    cutoff = int(time.time()) - max_age_seconds
    cur.execute(
        "DELETE FROM loc_suggestions WHERE created_at IS NOT NULL AND created_at < ?",
        (cutoff,)
    )
    conn.commit()