"""The Discord client itself: its construction, what it starts, what it
restores after a restart, and the two small helpers every other module in the
package reaches for.

Nothing about moderation lives here. This module knows how to *be* a Discord
bot — sync the command tree, launch the background tasks, re-arm the buttons
left on old messages, answer a failed interaction — and delegates the meaning
of any of it to the domain modules.

The imports of those domain modules are at the call site on purpose. Every one
of them imports `bot` from here at module level, so a module-level import in
the other direction would close the cycle; and `setup_hook` and
`_restore_persistent_views` both run long after import time, so there is
nothing to gain by hoisting them.
"""
import asyncio
import logging
import traceback

import discord
from discord import app_commands

import db
import utils
from config import PURGATORIUM_GUILD_ID
from utils import DEFAULT_LANG, SUPPORTED_LANGS, get_guild_lang, localized

logger = logging.getLogger("guard.discord")

class GuardBot(discord.Client):
    """The bot's client, with the command tree hanging off it.

    Both privileged intents are required and neither is optional: without
    `message_content` the spam guard sees empty messages and bans nobody,
    without `members` the join handlers never fire and the verification
    backfill has no member list to walk.
    """

    def __init__(self):
        """Build the client with the two privileged intents and its own
        application-command tree."""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._loops_started = False

    async def setup_hook(self):
        """Publish the command tree and start everything that runs on a timer.

        Called by discord.py at the end of login(), before the first
        connection. The three tasks are fire-and-forget: each waits for the
        client to be ready on its own, so none of them delays the login.

        Idempotent, because main.py retries a login that failed on a
        transient error and a second run would leave two copies of every
        loop. The flag is set once the tasks exist rather than on entry: a
        login that failed before that point — a tree.sync() that met a 5xx,
        say — gets the whole hook again on the retry, which is what it needs.
        """
        if self._loops_started:
            return

        from discord_bot.bans import unban_loop
        from discord_bot.verification import backfill_verified_roles

        await self.tree.sync()
        asyncio.create_task(unban_loop(self))
        asyncio.create_task(status_loop(self))
        asyncio.create_task(backfill_verified_roles(self))
        self._loops_started = True
        self._restore_persistent_views()

    def _restore_persistent_views(self):
        """Re-arm the buttons the bot left on messages before it was restarted.

        Both sets are bound to their message, since their custom_ids carry the
        user (and network) they act on rather than being one fixed shape. The
        pardon buttons are rebuilt from what is banned *now*, so a ban lifted
        while the bot was down does not come back as a live button."""
        from discord_bot.purgatorium import NetworkPardonView, _user_ban_networks
        from discord_bot.tribunal import TribunalView

        try:
            for case in db.get_open_tribunal_cases():
                lang = case["lang"] if case["lang"] in SUPPORTED_LANGS else DEFAULT_LANG
                self.add_view(TribunalView(int(case["user_id"]), lang),
                              message_id=int(case["message_id"]))
        except Exception as e:
            logger.error("restoring tribunal views: %s", e)
        try:
            lang = get_guild_lang(PURGATORIUM_GUILD_ID)
            for row in db.get_baninfo_posts(max_age_seconds=90 * 86400):
                uid = int(row["user_id"])
                networks = _user_ban_networks(uid)
                if networks:
                    self.add_view(NetworkPardonView(uid, networks, lang),
                                  message_id=int(row["message_id"]))
        except Exception as e:
            logger.error("restoring pardon views: %s", e)

    async def on_ready(self):
        """Log the identity the bot actually connected as.

        Defined on the class rather than through @bot.event so that it is not
        one of the four event handlers events.py owns — this one is a log
        line, not a dispatcher.
        """
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)

bot = GuardBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Last resort for a slash command that raised.

    Logs the traceback for the operator and tells the user something went
    wrong, in their server's language, privately. The reply is attempted both
    ways because the command may have deferred already, and the whole attempt
    is swallowed: an interaction that has expired cannot be answered at all,
    and failing to apologize must not raise a second error.
    """
    tb = traceback.format_exc()
    logger.error("Command '%s': %s\n%s",
                 interaction.command.name if interaction.command else "?", error, tb)
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    msg = localized("internal_error", lang, error=error)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass

async def _get_channel(channel_id):
    """Resolve a channel id — as int, string or None — to a channel object.

    Tries the cache first and falls back to an API fetch, because the log,
    service, tribunal and appeal channels are configured by id and may sit in
    servers whose channel list the bot has not populated. Returns None for
    anything unusable, so callers can treat "no channel" and "unreachable
    channel" as one case, which for a best-effort log line they should.
    """
    if channel_id is None:
        return None
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            ch = None
    return ch

async def _acknowledge_message(message: discord.Message, emoji: str = "✅"):
    """React to mark a sync-channel message as processed.

    Used by both consumers of the bridge_bot channels — the verification sync
    and the appeal pardon/ban-info handlers — which is why it sits here rather
    than in either of them. The reaction is the only receipt the other bot and
    the operators get, and failing to leave it is not worth aborting over.

    The emoji is the receipt's content: ✅ means the line changed something,
    ☑️ that it was already true and nothing was done. Anyone reading the
    channel can tell a working sync from a no-op without opening the logs.
    """
    try:
        await message.add_reaction(emoji)
    except Exception:
        pass

async def status_loop(client: discord.Client):
    """Rotate the presence text through the six languages, once a minute.

    The count is registered servers rather than `client.guilds`: a server that
    invited the bot but never ran /setup is not a community it guards.
    """
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            text = utils.get_next_status_text(db.count_guilds())
            await client.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=text)
            )
        except Exception:
            pass
        await asyncio.sleep(60)
