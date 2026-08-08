"""The bookkeeping behind the two families of buttons the bot leaves hanging
in Discord: tribunal cases and the ban summaries posted into appeal threads.

Both tables exist for the same reason, and it is not the data. Discord keeps
message components clickable forever, but the bot forgets everything when it
restarts — a view has to be re-registered against its message id or the
buttons answer nothing. So each posted message that carries buttons is
recorded here, and discord_bot/client.py: _restore_persistent_views reads both
tables at start-up to re-arm them.

`tribunal_cases` carries a second job: it is the record of what was decided.
`resolved` is NULL while a case is open, and claiming it is what makes two
consuls pressing at the same instant produce one ban.
"""
import time

from db import conn, cur

def add_tribunal_case(message_id, channel_id, guild_id, network, user_id, reason, lang):
    """Record a case posted to a tribunal channel, open (`resolved` NULL).

    Everything the verdict will need is copied in rather than looked up later:
    the network the ban would go to, the reason to reuse, and the language the
    case was written in — the message may be decided days later, by which time
    the guarded channel could have been reconfigured or the server moved to
    another network.
    """
    cur.execute(
        """
        INSERT OR REPLACE INTO tribunal_cases
        (message_id, channel_id, guild_id, network, user_id, reason, lang, created_at, resolved)
        VALUES (?,?,?,?,?,?,?,?,NULL)
        """,
        (str(message_id), str(channel_id), str(guild_id), network, str(user_id),
         reason, lang, int(time.time()))
    )
    conn.commit()

def get_tribunal_case(message_id):
    """The case behind a clicked message, or None.

    None is a real answer, not a bug: it is what a button from before the
    database was reset looks like, and the click handler says so rather than
    acting on nothing.
    """
    return cur.execute(
        "SELECT * FROM tribunal_cases WHERE message_id=?", (str(message_id),)
    ).fetchone()

def resolve_tribunal_case(message_id, outcome) -> bool:
    """Close an open case. Returns False when it was already closed, which is
    how two consuls pressing at once end up applying one verdict."""
    cur.execute(
        "UPDATE tribunal_cases SET resolved=? WHERE message_id=? AND resolved IS NULL",
        (outcome, str(message_id))
    )
    conn.commit()
    return cur.rowcount > 0

def get_open_tribunal_cases():
    """Every undecided case, for re-arming the buttons at start-up.

    Decided cases are deliberately not returned: their messages have already
    had their components stripped, so a view for them would be registered
    against nothing.
    """
    return cur.execute(
        "SELECT * FROM tribunal_cases WHERE resolved IS NULL"
    ).fetchall()

def get_stale_tribunal_cases(max_age_seconds):
    """Open cases older than the window, for the sweep that retires them.

    A case nobody touched is an offer that has to expire: Discord would keep
    its buttons live indefinitely, and a ban applied months after anyone
    remembers the case is an accident rather than a decision.
    """
    cutoff = int(time.time()) - max_age_seconds
    return cur.execute(
        "SELECT * FROM tribunal_cases WHERE resolved IS NULL"
        " AND created_at IS NOT NULL AND created_at < ?",
        (cutoff,)
    ).fetchall()

def add_baninfo_post(message_id, channel_id, user_id):
    """Remember a ban summary that carries per-network unban buttons.

    Written only for the message the buttons ended up on — a summary long
    enough to be split spans several messages, and only the last one has a
    view to restore.
    """
    cur.execute(
        """
        INSERT OR REPLACE INTO baninfo_posts
        (message_id, channel_id, user_id, created_at) VALUES (?,?,?,?)
        """,
        (str(message_id), str(channel_id), str(user_id), int(time.time()))
    )
    conn.commit()

def get_baninfo_posts(max_age_seconds=None):
    """Ban summaries whose buttons should be re-armed, optionally only the
    recent ones.

    The age limit is what keeps start-up from rebuilding a view for every
    appeal the bot ever answered; rows with no created_at predate the column
    and are always included rather than silently dropped.
    """
    if max_age_seconds is None:
        return cur.execute("SELECT * FROM baninfo_posts").fetchall()
    cutoff = int(time.time()) - max_age_seconds
    return cur.execute(
        "SELECT * FROM baninfo_posts WHERE created_at IS NULL OR created_at >= ?",
        (cutoff,)
    ).fetchall()

def cleanup_old_baninfo_posts(max_age_seconds=90 * 24 * 3600):
    """The summaries themselves stay in the thread; only the bookkeeping that
    keeps their buttons alive is dropped once an appeal is long over."""
    cutoff = int(time.time()) - max_age_seconds
    cur.execute(
        "DELETE FROM baninfo_posts WHERE created_at IS NOT NULL AND created_at < ?",
        (cutoff,)
    )
    conn.commit()
