"""The seven-day setup deadline: a server nobody registered with /setup is
left again.

A moderation bot that was invited and then forgotten is a standing grant of
ban and kick rights paying for nothing. Seven days after being added, a
server that has never been through `/setup` is left, and the service chats
are told. Nothing is said in the server itself: `/setup` is a Bot Admin
command, so the people who could act on a warning are the operators, and the
service chats are exactly who those are.

Three things keep the rule from touching a server it should not:

* **Grandfathering.** The deadline counts from the join, and a join from
  before the rule came into force (`db.rule_since`) is out of reach. Every
  server the bot was already in the day this shipped therefore stays.
* **One-shot.** The first sweep that finds a server registered settles it
  (`db.mark_settled`) and never looks again. A `guilds` row disappearing
  years later does not put the bot out of the door.
* **Config exemption.** A server holding any channel named in `config.py` —
  service, backup and support chats, the verification channels, the appeal
  and tribunal channels, Purgatorium — is the operator's own infrastructure
  and is never left.

The deadline is measured from `Guild.me.joined_at`, Discord's own record of
when the bot arrived, the same way the appeal system reads `Member.joined_at`:
a restart, a lost event or an empty cache cannot make the bot believe it has
been somewhere longer than it has.
"""
import logging
import time

import config
import db

logger = logging.getLogger("guard.setup_deadline")

SETUP_GRACE_SECONDS = 7 * 24 * 3600

def _parse_chat_key(raw_key):
    """Split a config channel entry into ``(guild_id, channel_id)``.

    Entries are hand-written and come either as 'guild:channel' or as a bare
    channel id. Unparsable entries yield (None, None): a typo in the config
    must not decide whether the bot stays in a server."""
    key = str(raw_key).strip()
    if not key:
        return None, None
    if ":" in key:
        left, right = key.split(":", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return None, None
    try:
        return None, int(key)
    except ValueError:
        return None, None

def _collect(*sources):
    """Flatten config entries — plain ids, sets of them, and the
    {'discord': {...}} mappings — into one set of raw keys."""
    out = set()
    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            for value in source.values():
                out |= _collect(value)
        elif isinstance(source, (set, frozenset, list, tuple)):
            for value in source:
                out |= _collect(value)
        else:
            out.add(source)
    return out

def _protected_ids():
    """The guild ids and channel ids that config.py names.

    Read through getattr rather than a from-import so that a deployment whose
    config.py predates one of these settings still starts."""
    def cfg(name):
        """One config setting, or None when this deployment predates it."""
        return getattr(config, name, None)

    raw = _collect(
        (cfg("SERVICE_CHATS") or {}).get("discord"),
        (cfg("BACKUP_CHATS") or {}).get("discord"),
        (cfg("SUPPORT_CHATS") or {}).get("discord"),
        (cfg("APPEAL_PARDON_CHANNELS") or {}).get("discord"),
        (cfg("APPEAL_BANINFO_CHANNELS") or {}).get("discord"),
        (cfg("TRIBUNAL_CHANNELS") or {}).get("discord"),
        cfg("VERIFIED"), cfg("UNVERIFIED"),
    )
    guilds = {cfg("PURGATORIUM_GUILD_ID")} - {None}
    channels = set()
    for key in raw:
        guild_id, channel_id = _parse_chat_key(key)
        if guild_id is not None:
            guilds.add(guild_id)
        if channel_id is not None:
            channels.add(channel_id)
    return guilds, channels

def is_protected_guild(guild):
    """Whether this server is named in config.py — by its own id, or by
    holding one of the channels the deployment is wired to."""
    guilds, channels = _protected_ids()
    if guild.id in guilds:
        return True
    return any(channel.id in channels for channel in guild.channels)

async def setup_deadline_pass(client):
    """One sweep over the servers the bot is in.

    Returns the service-chat reports to send — `(event_key, kwargs)` pairs —
    rather than sending them, so the loop in main.py owns the reporting and
    this module needs nothing from it."""
    now = time.time()
    since = db.rule_since()
    events = []

    for guild in list(client.guilds):
        try:
            me = guild.me
            if me is None:
                try:
                    me = await guild.fetch_member(client.user.id)
                except Exception:
                    me = None
            joined_at = getattr(me, "joined_at", None)
            if joined_at is None:
                continue
            joined_ts = joined_at.timestamp()
            if joined_ts <= since:
                continue
            row = db.get_deadline_row(guild.id)
            if row and row["settled_at"]:
                continue
            if db.get_guild(guild.id):
                db.mark_settled(guild.id)
                continue
            if now - joined_ts < SETUP_GRACE_SECONDS:
                continue
            if is_protected_guild(guild):
                db.mark_settled(guild.id)
                continue
            await guild.leave()
            db.forget_deadline(guild.id)
            events.append(("guild_left_unconfigured", {
                "guild": guild.name or str(guild.id),
                "guild_id": guild.id,
                "days": SETUP_GRACE_SECONDS // 86400,
            }))
            logger.info("Left unregistered server %s (%s)", guild.name, guild.id)
        except Exception as e:
            logger.warning("setup deadline failed for server %s: %s", guild.id, e)

    return events
