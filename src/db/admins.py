"""Who may tell the bot what to do: server admins, localizers, consuls.

Three tables, three unrelated powers, and none of them is a hierarchy — a
consul is not a super-admin and a localizer is not an admin at all:

* `guild_admins` — moderation rights on one server (/setadmin). The only one
  of the three checked on ordinary commands, through utils.is_admin, which
  also lets the hard-coded config.ADMINS through everywhere.
* `localizers` — permission to edit this bot's localization in the external
  control panel (/localizer-add). Grants nothing inside Discord.
* `consuls` — admission through the Purgatorium gate plus the right to press
  the tribunal and pardon buttons (/setconsul).

The Bot Admin tier itself is not stored: it lives in config.py, so that the
database can never hand anyone the keys to the bot.
"""
import time

from db import conn, cur

def add_guild_admin(guild_id, user_id):
    """Grant Server Admin rights on one server (/setadmin).

    INSERT OR IGNORE — the command checks for the existing row itself so it
    can tell the caller "already an admin" instead of silently re-granting.
    """
    cur.execute(
        "INSERT OR IGNORE INTO guild_admins (guild_id, user_id) VALUES (?,?)",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def remove_guild_admin(guild_id, user_id):
    """Revoke Server Admin rights on one server. A Bot Admin from config.py is
    unaffected — they were never in this table."""
    cur.execute(
        "DELETE FROM guild_admins WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    )
    conn.commit()

def is_guild_admin(guild_id, user_id):
    """Whether the user is a Server Admin *here*.

    Asked on nearly every command through utils.is_admin, which consults
    config.ADMINS first — so this answers the narrower question of a delegated
    per-server grant only.
    """
    row = cur.execute(
        "SELECT 1 FROM guild_admins WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    return row is not None

def add_localizer(platform, user_id, username=None, added_by=None):
    """Grant localizer status (set with /localizer-add): the user may edit
    this bot's localization through the control panel.  The username, when
    known, is kept for the panel's username login."""
    cur.execute(
        "INSERT INTO localizers (platform, user_id, username, added_by, added_at)"
        " VALUES (?,?,?,?,strftime('%s','now'))"
        " ON CONFLICT(platform, user_id) DO UPDATE SET"
        " username=COALESCE(excluded.username, localizers.username)",
        (platform, str(user_id), username,
         str(added_by) if added_by is not None else None)
    )
    conn.commit()

def remove_localizer(platform, user_id):
    """Revoke a delegated localizer status.  Returns True when a row existed
    (admins are localizers implicitly and have no row to remove)."""
    removed = cur.execute(
        "DELETE FROM localizers WHERE platform=? AND user_id=?",
        (platform, str(user_id))
    ).rowcount
    conn.commit()
    return removed > 0

def is_localizer(platform, user_id):
    """Whether the user holds a delegated localizer row.

    `platform` is always 'discord' for this bot; the column exists so that the
    control panel can read the same shape of table from every bot it serves.
    """
    row = cur.execute(
        "SELECT 1 FROM localizers WHERE platform=? AND user_id=?",
        (platform, str(user_id))
    ).fetchone()
    return row is not None

def add_consul(user_id, added_by):
    """Appoint an appeal-server consul (/setconsul).

    INSERT OR REPLACE, so re-appointing refreshes who appointed them and when.
    Handing over the actual Discord roles is the command's job; this row is
    what the Purgatorium gate and the button permission check read.
    """
    cur.execute(
        "INSERT OR REPLACE INTO consuls (user_id, added_by, added_at) VALUES (?,?,?)",
        (str(user_id), str(added_by), int(time.time()))
    )
    conn.commit()

def remove_consul(user_id):
    """Dismiss a consul. False when they were not one, which is how
    /remconsul distinguishes a typo from a real dismissal.

    Someone holding a config.CONSULS role keeps every right this table grants:
    the two paths are independent by design, so an operator can run the
    appeal server on roles alone.
    """
    cur.execute("DELETE FROM consuls WHERE user_id=?", (str(user_id),))
    removed = cur.rowcount > 0
    conn.commit()
    return removed

def is_consul(user_id):
    """Whether the user was appointed with /setconsul.

    Read by the Purgatorium gate before anything else, so that a consul is let
    in rather than examined for bans.
    """
    row = cur.execute(
        "SELECT 1 FROM consuls WHERE user_id=?",
        (str(user_id),)
    ).fetchone()
    return row is not None
