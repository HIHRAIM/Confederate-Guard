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
    """)
    conn.commit()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(guilds)").fetchall()]
    if "network" not in cols:
        cur.execute("ALTER TABLE guilds ADD COLUMN network INTEGER")
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

def remove_guild_data(guild_id):
    gid = str(guild_id)
    cur.execute("DELETE FROM guilds WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM guarded_channels WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM custom_dm WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM active_bans WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM autoroles WHERE guild_id=?", (gid,))
    cur.execute("DELETE FROM guild_admins WHERE guild_id=?", (gid,))
    conn.commit()

def get_expired_bans():
    now = int(time.time())
    return cur.execute(
        "SELECT * FROM active_bans WHERE unban_at IS NOT NULL AND unban_at <= ?",
        (now,)
    ).fetchall()
