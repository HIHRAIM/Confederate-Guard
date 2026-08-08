"""The slash commands, one module per domain.

Importing this package registers all 28 of them: `@bot.tree.command` fires at
import time, so a module nobody imports here contributes nothing and the bot
loses its commands with no error anywhere. The list below is therefore the
registration list — add a module to it or the command does not exist.

The order within the list does not matter (the tree is keyed by name and the
modules do not depend on each other), but the position of this package in
discord_bot/__init__.py does: commands are imported last, after the client and
the domain modules they call into.

Which module a command belongs in is decided by its subject, not by its size —
user.py holds a single command and that is correct. The addresses match
bridge_bot's so that the same command is found in the same file in both bots:
/backup with the admin commands, /locale and /loc-* in locale.py, /help in
user.py, and the commands about the bot's own presence in communities
(/setup, /list_chats, /force_leave) in the module named after that object,
here guilds.py.
"""
from discord_bot.commands import guilds
from discord_bot.commands import settings
from discord_bot.commands import bans
from discord_bot.commands import links
from discord_bot.commands import admins
from discord_bot.commands import locale
from discord_bot.commands import user
