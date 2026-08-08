"""Every CREATE TABLE of the bot, plus the additive column migrations.

This module owns the shape of guard.db and nothing else — no queries, no
business logic. `db.init()` calls `create_all()` once at start-up, before the
client connects.

The migration policy is strict because the database is live production data:
new tables only via CREATE TABLE IF NOT EXISTS, new columns only via a PRAGMA
table_info check followed by ALTER TABLE ADD COLUMN. Never DROP, never rebuild
a table, never an ALTER that can lose data.

Table documentation lives in `--` comments inside the SQL string: python-level
`#` comments would be stripped by the parent folder's clean_code.py, SQL
comments inside a string literal survive. They sit *above* each statement on
purpose — SQLite stores the text of a CREATE statement verbatim in
sqlite_master, so a comment placed inside the parentheses would become part of
the recorded schema. For the same reason the indentation of the SQL below is
not free to change: it is the indentation the tables were created with.
"""
import time

def create_all(cur, conn):
    """Bring the schema up to date: create the missing tables, then add the
    missing columns.

    Safe to run on every start and on a database at any age — every statement
    is a no-op once its object exists. Takes the connection and cursor as
    arguments rather than importing them, so that db/__init__.py can call this
    while it is still initializing itself.
    """
    cur.executescript("""
    -- guilds: one row per server registered with /setup. Written by
    -- setup_guild, erased by remove_guild_data (/force_leave). `network` is
    -- the integer that groups servers into a shared ban list; NULL means the
    -- server stands alone — /globalban refuses to run there, the tribunal
    -- never posts its bans and prior-ban alerts never fire.
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY,
        lang TEXT NOT NULL DEFAULT 'en',
        log_channel_id TEXT NOT NULL,
        network INTEGER
    );

    -- guarded_channels: channels /guard turned into no-post zones. Read on
    -- every message by discord_bot/events.py: on_message, which deletes what
    -- is posted there and bans the author when it was spam.
    -- duration_seconds NULL means the ban is permanent.
    CREATE TABLE IF NOT EXISTS guarded_channels (
        channel_id TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        duration_seconds INTEGER,
        reason TEXT NOT NULL
    );

    -- custom_dm: per-server replacement for the default spam-ban DM, set with
    -- /dm. The text may carry {server} and {reason} placeholders, substituted
    -- at send time.
    CREATE TABLE IF NOT EXISTS custom_dm (
        guild_id TEXT PRIMARY KEY,
        message TEXT NOT NULL
    );

    -- active_bans: the bans the bot issued and still tracks. Written by the
    -- spam guard, /ban, /globalban and network enforcement; unban_loop lifts
    -- the rows whose unban_at has passed. unban_at NULL is a permanent ban,
    -- recorded anyway so the Purgatorium gate can recognize it. The gate's own
    -- one-day bans are deliberately NOT stored here.
    CREATE TABLE IF NOT EXISTS active_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        unban_at INTEGER,
        UNIQUE(guild_id, user_id)
    );

    -- autoroles: the role /autorole hands to every member of the server, on
    -- join and to everyone present when the command runs. Independent of
    -- verification.
    CREATE TABLE IF NOT EXISTS autoroles (
        guild_id TEXT PRIMARY KEY,
        role_id TEXT NOT NULL
    );

    -- guild_admins: per-server admins appointed with /setadmin. Checked
    -- through utils.is_admin, which also lets config.ADMINS through
    -- everywhere.
    CREATE TABLE IF NOT EXISTS guild_admins (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    -- banned_links: the bot-wide forbidden list, maintained with /banlink and
    -- /unbanlink and read on every message outside a guarded channel. `kind`
    -- is 'invite' (value is the bare invite code) or 'url' (value is the
    -- normalized host+path, see utils.classify_banned_link). A listed link is
    -- deleted, never banned for.
    CREATE TABLE IF NOT EXISTS banned_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        value TEXT NOT NULL UNIQUE
    );

    -- verify_settings: per-server /setverify configuration. channel_id is
    -- optional; without it the announcements go to the /setup log channel.
    CREATE TABLE IF NOT EXISTS verify_settings (
        guild_id TEXT PRIMARY KEY,
        role_id TEXT NOT NULL,
        channel_id TEXT
    );

    -- verified_users: the cross-server verified set — one row per user, not
    -- per server. Written by two-day activity and by the bridge_bot sync
    -- channels. origin_guild_id is where the verification actually happened
    -- and decides the announcement wording elsewhere ('verified on another
    -- server'); NULL means unknown, and then every server gets the plain text.
    CREATE TABLE IF NOT EXISTS verified_users (
        user_id TEXT PRIMARY KEY,
        origin_guild_id TEXT,
        verified_at INTEGER
    );

    -- verify_grants: the per-server 'already done' marker — the verify role
    -- was granted and announced for this user here. Written even for the
    -- silent grants of the startup backfill, which is what makes that sweep
    -- idempotent across restarts and stops a later message from producing a
    -- second announcement. granted_at is added by the migration below and
    -- drives the retention sweep.
    CREATE TABLE IF NOT EXISTS verify_grants (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        granted_at INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    -- user_activity: the first calendar date (UTC) a member was seen posting a
    -- genuine message on this server. A message on a second, different date is
    -- what verifies them. Discord's own system notices are excluded, so merely
    -- joining does not start the clock.
    CREATE TABLE IF NOT EXISTS user_activity (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        first_date TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    -- global_bans: network bans, one row per (network, user). Written by
    -- /globalban and by the tribunal's 'Global ban' verdict, deleted by
    -- _execute_global_unban and by the expiry sweep. unban_at is always
    -- finite here — the longest term the parser yields is ten years.
    CREATE TABLE IF NOT EXISTS global_bans (
        network INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        reason TEXT,
        origin_guild_id TEXT,
        banned_at INTEGER,
        unban_at INTEGER,
        PRIMARY KEY (network, user_id)
    );

    -- gban_settings: whether this server enforces its network's bans
    -- (/setgbans). Membership of a network and enforcement of it are separate
    -- on purpose: a server may contribute bans and read alerts without handing
    -- the network authority over its own member list.
    CREATE TABLE IF NOT EXISTS gban_settings (
        guild_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0
    );

    -- gban_enforcements: the ledger of bans that network enforcement actually
    -- applied on this server. It exists so that /setgbans disable can lift
    -- exactly those and nothing else — a ban issued locally with /ban or
    -- /globalban has no row here and survives the switch being turned off.
    CREATE TABLE IF NOT EXISTS gban_enforcements (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );

    -- appeal_messages: the server's own appeal text (/setappeal), appended to
    -- the spam-ban DM. Its mere presence also suppresses the Purgatorium
    -- invitation: a server that runs its own appeal flow keeps it.
    CREATE TABLE IF NOT EXISTS appeal_messages (
        guild_id TEXT PRIMARY KEY,
        message TEXT NOT NULL
    );

    -- ban_history: every ban the bot issued, kept after the ban itself is
    -- gone. Read by the prior-ban alert, which warns a network server when
    -- someone banned elsewhere in the network joins. Rows are dropped
    -- deliberately — by /unban and /globalunban for one server, and wholesale
    -- for a user the consuls pardoned — so a reverted ban stops warning.
    CREATE TABLE IF NOT EXISTS ban_history (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        banned_at INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    -- loc_suggestions: open /loc-suggest dialogs, keyed by the random code the
    -- suggester is shown. `rkey` is the reply key being suggested for, `lang`
    -- its target language, `ui_lang` the language the suggester was speaking.
    -- Removed when answered with /loc-reply, and after a year regardless.
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

    -- localizers: users allowed to edit this bot's localization through the
    -- external control panel (/localizer-add). Grants nothing inside Discord.
    -- `username` is kept for the panel's username login.
    CREATE TABLE IF NOT EXISTS localizers (
        platform TEXT NOT NULL,
        user_id TEXT NOT NULL,
        username TEXT,
        added_by TEXT,
        added_at INTEGER,
        PRIMARY KEY (platform, user_id)
    );

    -- consuls: appeal-server consuls appointed with /setconsul. Not a
    -- moderation role on ordinary servers — it is admission through the
    -- Purgatorium gate plus the right to press the tribunal and pardon
    -- buttons. config.CONSULS roles grant the same rights independently.
    CREATE TABLE IF NOT EXISTS consuls (
        user_id TEXT PRIMARY KEY,
        added_by TEXT,
        added_at INTEGER
    );

    -- One row per spam-guard ban put before the tribunal channel. The row is
    -- what lets the buttons keep working across restarts and what the sweep
    -- reads to retire them; `resolved` is NULL while the case is open.
    CREATE TABLE IF NOT EXISTS tribunal_cases (
        message_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        guild_id TEXT NOT NULL,
        network INTEGER,
        user_id TEXT NOT NULL,
        reason TEXT,
        lang TEXT,
        created_at INTEGER,
        resolved TEXT
    );

    -- Ban summaries posted into appeal threads, remembered only so their
    -- per-network unban buttons can be re-registered after a restart.
    CREATE TABLE IF NOT EXISTS baninfo_posts (
        message_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_at INTEGER
    );

    -- Free-form switches of the bot itself. Currently only
    -- 'setup_rule_since', the moment the seven-day setup deadline came into
    -- force (setup_deadline.py).
    CREATE TABLE IF NOT EXISTS bot_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    -- The seven-day setup deadline (setup_deadline.py). One row per server
    -- the bot was added to AFTER the rule came into force — every server it
    -- was already in has no row and is never examined. joined_at is only a
    -- record: Discord's own Guild.me.joined_at is what the sweep measures
    -- from, and it survives a restart. settled_at is set the first time the
    -- server is found registered with /setup, and is what makes the check
    -- one-shot: a server registered once is never left afterwards.
    CREATE TABLE IF NOT EXISTS setup_deadlines (
        platform TEXT NOT NULL,
        server_id TEXT NOT NULL,
        joined_at INTEGER,
        settled_at INTEGER,
        PRIMARY KEY (platform, server_id)
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
