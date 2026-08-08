"""The bot's single SQLite connection and the public db API.

This package replaces the old monolithic db.py. The split is by domain — see
each submodule's docstring — but the *interface* is unchanged: every helper is
re-imported here, so call sites keep saying ``db.add_global_ban(...)`` and
``db.cur.execute(...)`` exactly as before.

Connection model: one process-wide connection in WAL mode and one cursor,
created here exactly once. Unlike bridge_bot there is no locking facade — this
bot runs a single asyncio loop with nothing else touching the file, and adding
one would be a change of behaviour rather than a move. ``cur`` is a real
shared cursor, which is what keeps ``cur.rowcount`` meaningful to the caller
of a helper defined in another submodule. The database file is opened by
RELATIVE path: the process must run with cwd = src/ (main.py and the control
panel both do).

Import order at the bottom matters: submodules do ``from db import conn, cur``
against this partially-initialized module, which works only because conn/cur
are defined above those imports. Keep new submodule imports at the bottom, and
re-export new helpers here by explicit name — never ``import *``, or the
package stops documenting its own surface.
"""
import sqlite3

conn = sqlite3.connect("guard.db", check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def init():
    """Bring the schema up to date (create missing tables, add missing
    columns — strictly additive, see db/schema.py). Called once from main.py
    before the client starts."""
    from db import schema
    schema.create_all(cur, conn)

from db.onboarding import (
    USER_ID_RETENTION_SECONDS,
    cleanup_expired_user_data,
    forget_deadline,
    get_deadline_row,
    mark_settled,
    record_join,
    rule_since,
)
from db.guilds import (
    count_guilds,
    get_autorole,
    get_guard,
    get_guild,
    get_network_guilds,
    remove_guild_data,
    set_autorole,
    set_guard,
    setup_guild,
)
from db.settings import (
    add_banned_link,
    add_loc_suggestion,
    cleanup_old_loc_suggestions,
    delete_loc_suggestion,
    get_appeal,
    get_banned_links,
    get_custom_dm,
    get_loc_suggestion,
    is_gbans_enabled,
    remove_banned_link,
    set_appeal,
    set_custom_dm,
    set_gbans_enabled,
)
from db.admins import (
    add_consul,
    add_guild_admin,
    add_localizer,
    is_consul,
    is_guild_admin,
    is_localizer,
    remove_consul,
    remove_guild_admin,
    remove_localizer,
)
from db.bans import (
    add_active_ban,
    add_gban_enforcement,
    add_global_ban,
    cleanup_expired_global_bans,
    clear_gban_enforcements,
    get_active_global_ban,
    get_active_global_bans_for_network,
    get_expired_bans,
    get_gban_enforcements,
    get_global_ban,
    get_network_ban_history,
    get_user_active_bans,
    get_user_active_global_bans,
    get_user_ban_history,
    get_user_global_bans,
    record_ban,
    remove_active_ban,
    remove_ban_history,
    remove_gban_enforcement,
    remove_global_ban,
    remove_user_ban_history,
)
from db.users import (
    add_verified,
    add_verify_grant,
    get_first_seen,
    get_verified_origin,
    get_verify,
    has_verify_grant,
    is_verified,
    remove_verified,
    set_first_seen,
    set_verify,
)
from db.tribunal import (
    add_baninfo_post,
    add_tribunal_case,
    cleanup_old_baninfo_posts,
    get_baninfo_posts,
    get_open_tribunal_cases,
    get_stale_tribunal_cases,
    get_tribunal_case,
    resolve_tribunal_case,
)
