"""The bot-wide banned-link list: adding, removing and paging through it.

The list is global — one set of forbidden URLs and invite codes for every
server the bot is on — which is why only Bot Admins may change it, while any
Server Admin may read it.

Matching a message against the list happens in utils.py and the deletion in
events.py; a listed link is deleted wherever the bot can see it and never
banned for. This module is only the maintenance surface.
"""
import discord
from discord import app_commands

import db
from discord_bot.client import bot
from utils import DEFAULT_LANG, classify_banned_link, get_guild_lang, is_admin, localized

LINKS_PAGE_SIZE = 10

@bot.tree.command(name="banlink", description="add a link or invite code to the banned list")
@app_commands.describe(link="A URL (evil.com) or a Discord invite code/link (fXaxuYdN or discord.gg/fXaxuYdN)")
async def banlink_cmd(interaction: discord.Interaction, link: str):
    """Add a URL or invite to the global list (Bot Admins).

    The input is classified before storage — an invite becomes its bare code,
    a URL is stripped of scheme, www and trailing slash — so that the same
    link written five ways is one row and matches all five. The reply differs
    by kind because the stored value no longer looks like what was typed.
    """
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    kind, value = classify_banned_link(link)
    if not db.add_banned_link(kind, value):
        await interaction.response.send_message(
            localized("banlink_exists", lang), ephemeral=True
        )
        return

    if kind == "invite":
        await interaction.response.send_message(
            localized("banlink_added_invite", lang, code=value), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            localized("banlink_added_url", lang, url=value), ephemeral=True
        )

@bot.tree.command(name="unbanlink", description="remove a link from the banned list (number shown in /links)")
@app_commands.describe(link_id="Number of the link as shown in /links")
async def unbanlink_cmd(interaction: discord.Interaction, link_id: int):
    """Remove a link by the number /links shows (Bot Admins).

    Takes the row id rather than the link text because the stored value is
    normalized and an admin reading the list has the number in front of them,
    not the original spelling.
    """
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    if db.remove_banned_link(link_id):
        await interaction.response.send_message(
            localized("unbanlink_success", lang, link_id=link_id), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            localized("unbanlink_not_found", lang, link_id=link_id), ephemeral=True
        )

def _links_embed(lang, rows, page, pages):
    """One page of the banned-link list as an embed.

    Numbers the entries by their database id, not by position, so the number
    a reader sees is the one /unbanlink takes. The footer appears only when
    there is more than one page.
    """
    lines = []
    for r in rows[page * LINKS_PAGE_SIZE:(page + 1) * LINKS_PAGE_SIZE]:
        kind_key = "links_kind_invite" if r["kind"] == "invite" else "links_kind_url"
        lines.append(f"{r['id']}. {localized(kind_key, lang)}: `{r['value']}`")
    embed = discord.Embed(
        title=localized("links_title", lang),
        description="\n".join(lines),
        color=discord.Color.blurple()
    )
    if pages > 1:
        embed.set_footer(text=localized("links_page", lang, page=page + 1, pages=pages))
    return embed

class LinksView(discord.ui.View):
    """Arrow buttons paging through the banned-link list.

    Not a persistent view: it carries the rows it was built from and expires
    with its 10-minute timeout, which is right for an ephemeral answer nobody
    else can see and which nothing needs to survive a restart.
    """
    def __init__(self, lang, rows):
        """Hold the rows and open on the first page."""
        super().__init__(timeout=600)
        self.lang = lang
        self.rows = rows
        self.page = 0
        self.pages = (len(rows) + LINKS_PAGE_SIZE - 1) // LINKS_PAGE_SIZE
        self._update_buttons()

    def _update_buttons(self):
        """Grey out whichever arrow would walk off the end."""
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.pages - 1

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Step back one page and redraw in place."""
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_links_embed(self.lang, self.rows, self.page, self.pages), view=self
        )

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Step forward one page and redraw in place."""
        self.page = min(self.pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_links_embed(self.lang, self.rows, self.page, self.pages), view=self
        )

@bot.tree.command(name="links", description="list of banned links")
async def links_cmd(interaction: discord.Interaction):
    """Show the banned-link list, paged, privately.

    Readable by Server Admins although only Bot Admins may edit it: a local
    moderator needs to know why a message vanished. The paging view is
    attached only when there is a second page to reach.
    """
    lang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not is_admin(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            localized("setup_no_perm", lang), ephemeral=True
        )
        return

    rows = db.get_banned_links()
    if not rows:
        await interaction.response.send_message(
            localized("links_empty", lang), ephemeral=True
        )
        return

    pages = (len(rows) + LINKS_PAGE_SIZE - 1) // LINKS_PAGE_SIZE
    embed = _links_embed(lang, rows, 0, pages)
    if pages > 1:
        await interaction.response.send_message(embed=embed, view=LinksView(lang, rows), ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
