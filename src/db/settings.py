"""The switches and free text a server (or a Bot Admin) can set: the custom
ban DM, the appeal text, the network-ban enforcement toggle, the bot-wide
banned-link list, and open localization suggestions.

The line against db/guilds.py is what the setting *is*, not who owns it:
there sit the rows that describe a server's identity and the channels the bot
acts in, here sit the ones that only change wording or flip a behaviour.
gban_settings lives here for that reason while its sibling
gban_enforcements — a record of bans actually applied — lives in db/bans.py.

bot_settings has no accessor here either: its single key belongs to the setup
deadline and is served by db/onboarding.py: rule_since.
"""
import time

from db import conn, cur

def set_custom_dm(guild_id, message):
    """Set the server's own text for the pre-ban DM (/dm).

    Stored raw, with its {server} and {reason} placeholders intact — they are
    substituted at send time, so the same row keeps working when a guarded
    channel's ban reason changes.
    """
    cur.execute(
        "INSERT OR REPLACE INTO custom_dm (guild_id, message) VALUES (?,?)",
        (str(guild_id), message)
    )
    conn.commit()

def get_custom_dm(guild_id):
    """The server's custom ban DM, or None to use the localized default."""
    row = cur.execute(
        "SELECT message FROM custom_dm WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["message"] if row else None

def set_appeal(guild_id, message):
    """Set the text appended to the spam-ban DM (/setappeal).

    Setting it has a second effect nothing in the name suggests: a server with
    its own appeal text stops receiving the Purgatorium invitation line, on
    the assumption that it runs an appeal flow of its own and does not want
    its banned users sent elsewhere.
    """
    cur.execute(
        "INSERT OR REPLACE INTO appeal_messages (guild_id, message) VALUES (?,?)",
        (str(guild_id), message)
    )
    conn.commit()

def get_appeal(guild_id):
    """The server's appeal text, or None.

    Read both to append it to a ban DM and, in _purgatorium_invite_line, as
    the test for "this server handles appeals itself".
    """
    row = cur.execute(
        "SELECT message FROM appeal_messages WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return row["message"] if row else None

def set_gbans_enabled(guild_id, enabled):
    """Turn enforcement of this server's network bans on or off (/setgbans).

    Only the switch is written here; applying the backlog on enable and
    reverting the enforced bans on disable is the command's own work, since
    both need Discord.
    """
    cur.execute(
        "INSERT OR REPLACE INTO gban_settings (guild_id, enabled) VALUES (?,?)",
        (str(guild_id), 1 if enabled else 0)
    )
    conn.commit()

def is_gbans_enabled(guild_id):
    """Whether this server enforces network bans. Default is False: a server
    joins a network without surrendering its member list, and has to opt in."""
    row = cur.execute(
        "SELECT enabled FROM gban_settings WHERE guild_id=?",
        (str(guild_id),)
    ).fetchone()
    return bool(row and row["enabled"])

def add_banned_link(kind, value):
    """Add an invite code or a normalized URL to the bot-wide list.

    Returns False when the link was already listed — INSERT OR IGNORE, and the
    caller reports the duplicate rather than silently succeeding. `kind` and
    `value` come pre-classified from utils.classify_banned_link; storing an
    unnormalized value here would simply never match.
    """
    cur.execute(
        "INSERT OR IGNORE INTO banned_links (kind, value) VALUES (?,?)",
        (kind, value)
    )
    conn.commit()
    return cur.rowcount > 0

def get_banned_links():
    """The whole banned-link list, ordered by id.

    Read on every message the bot sees outside a guarded channel, and by
    /links for its numbered pages — the id shown there is this row's id, which
    is what /unbanlink takes.
    """
    return cur.execute(
        "SELECT * FROM banned_links ORDER BY id"
    ).fetchall()

def remove_banned_link(link_id):
    """Delete a banned link by the number /links shows. False when there was
    no such row."""
    cur.execute("DELETE FROM banned_links WHERE id=?", (link_id,))
    conn.commit()
    return cur.rowcount > 0

def add_loc_suggestion(code, platform, user_id, username, lang, rkey, suggestion, ui_lang):
    """Record an open /loc-suggest dialog under the random code the suggester
    is shown.

    The code is the only handle: /loc-reply takes it, and the row is what lets
    an admin answer days later, in the language the suggester was speaking
    (ui_lang) rather than the one they were suggesting for (lang).
    """
    cur.execute(
        "INSERT OR REPLACE INTO loc_suggestions "
        "(code, platform, user_id, username, lang, rkey, suggestion, ui_lang, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (code, platform, str(user_id), username, lang, rkey, suggestion, ui_lang, int(time.time()))
    )
    conn.commit()

def get_loc_suggestion(code):
    """The suggestion behind a dialog code, or None when it is unknown or
    already answered."""
    return cur.execute(
        "SELECT * FROM loc_suggestions WHERE code=?",
        (code,)
    ).fetchone()

def delete_loc_suggestion(code):
    """Close a dialog. Called only after the reply actually reached the
    suggester — a failed DM keeps the code alive for a second attempt."""
    cur.execute("DELETE FROM loc_suggestions WHERE code=?", (code,))
    conn.commit()

def cleanup_old_loc_suggestions(max_age_seconds=365 * 24 * 3600):
    """Localization-suggestion dialog codes are kept at most a year.

    Run daily by main.py: retention_loop. An unanswered suggestion is the only
    thing lost, and its text has long since been read in the support chat.
    """
    cutoff = int(time.time()) - max_age_seconds
    cur.execute(
        "DELETE FROM loc_suggestions WHERE created_at IS NOT NULL AND created_at < ?",
        (cutoff,)
    )
    conn.commit()
