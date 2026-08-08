"""The Discord bot, as a package.

Importing it builds the client and registers everything: the import order
below runs the @bot.event and @bot.tree.command decorators of every module.
The order is the dependency order (client first, commands last) — append new
modules in a place that respects it, and remember the two persistent-view
classes live in purgatorium.py and tribunal.py, not under commands/.

Within discord_bot/ the modules reference each other in cycles (events →
purgatorium → bans → client). Those are broken at the call site, inside the
function that needs the import; do not hoist one to module level, or the
package stops importing.

The re-exports are the package's public API: everything main.py and utils.py
import from `discord_bot`. Keep importing it lazily (at the call site) from
utils.py, as it already does.
"""
from discord_bot.client import (
    GuardBot,
    bot,
)
from discord_bot import verification
from discord_bot import bans
from discord_bot import purgatorium
from discord_bot import tribunal
from discord_bot import events
from discord_bot import commands
