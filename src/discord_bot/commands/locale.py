"""The localization commands, and the support-chat plumbing behind them.

Everything here is open to everyone except /loc-reply: reporting a bad
translation is the one thing a bot should never gatekeep, since the people who
notice are exactly the ones with no rights.

`_locale_file_cooldown` is module-level mutable state and must stay declared
here, in one place. It is per server, ten minutes, and it exists because
/locale <code> uploads a file — a command anyone can run and that would
otherwise be a free file-spam button.

The reading side of localization (loading the files, `localized`, the status
statistics) is in utils.py; this module only talks about it.
"""
import os
import secrets
import time

import discord
from discord import app_commands

import db
import utils
from config import SUPPORT_CHATS
from discord_bot.client import _get_channel, bot
from utils import (
    DEFAULT_LANG, LANG_ORDER, LOCALE_STATUS_EMOJI, SUPPORTED_LANGS,
    available_locales, compare_reply, get_guild_lang, is_admin, language_name,
    locale_bar, locale_stats, localized,
)

_locale_file_cooldown = {}

def _locale_cooldown_ok(bucket):
    """10-minute cooldown per server for the /locale <lang> file download."""
    now = time.time()
    if now - _locale_file_cooldown.get(bucket, 0) < 600:
        return False
    _locale_file_cooldown[bucket] = now
    return True

async def post_loc_suggestion(*, lang, key, suggestion, code, ui_lang, username, user_id, avatar_url=None):
    """Post a localization suggestion to the Discord support chat(s)."""
    body = localized("loc_suggest_support_body", ui_lang,
                     suggestion=suggestion, name=language_name(lang), lang=lang, key=key)
    footer = f"{username} │ ID: {user_id} │ {code}"
    for cid in SUPPORT_CHATS.get("discord", set()):
        channel = await _get_channel(cid)
        if not channel:
            continue
        embed = discord.Embed(description=body)
        embed.set_footer(text=footer, icon_url=avatar_url)
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

async def post_loc_reply(*, admin, code, ui_lang, title, body):
    """Publish an admin's /loc-reply to the Discord support chat(s)."""
    prefix = localized("loc_reply_support_prefix", ui_lang, admin=admin, code=code)
    for cid in SUPPORT_CHATS.get("discord", set()):
        channel = await _get_channel(cid)
        if not channel:
            continue
        try:
            await channel.send(embed=discord.Embed(title=title, description=f"{prefix}\n\n{body}"))
        except Exception:
            pass

@bot.tree.command(name="locale", description="localization status, or a language's file")
@app_commands.describe(lang="Language code (optional). With a code, sends that language's localization file.")
async def locale_cmd(interaction: discord.Interaction, lang: str = None):
    """Show translation progress, or send one language's file.

    Two commands in one argument. Without a code it prints a bar and a
    verified percentage per language and costs nothing. With a code it uploads
    the JSON, which is why only that branch takes the cooldown — keyed on the
    server, or on the user in a DM where there is no server to key on.

    The file is read from utils.__file__'s directory rather than from this
    module's, because the i18n folder sits next to utils.py in src/ and this
    module is two levels below it.
    """
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG

    if not lang or not lang.strip():
        lines = [localized("loc_list_header", glang)]
        for code in available_locales():
            st = locale_stats(code)
            lines.append(f"{language_name(code)} (`{code}`): {locale_bar(code)} {st['percent']}%")
        lines.append("")
        lines.append(localized("loc_list_footer", glang))
        await interaction.response.send_message("\n".join(lines))
        return

    code = lang.strip().lower()
    if code not in available_locales():
        await interaction.response.send_message(
            localized("loc_unknown_lang", glang, lang=code, supported=", ".join(available_locales())),
            ephemeral=True
        )
        return

    bucket = interaction.guild_id or interaction.user.id
    if not _locale_cooldown_ok(bucket):
        await interaction.response.send_message(localized("loc_cooldown", glang), ephemeral=True)
        return

    path = os.path.join(os.path.dirname(utils.__file__), "i18n", f"{code}.json")
    st = locale_stats(code)
    caption = localized("loc_file_caption", glang, name=language_name(code), code=code, percent=st["percent"])
    try:
        await interaction.response.send_message(caption, file=discord.File(path, filename=f"{code}.json"))
    except Exception:
        await interaction.response.send_message(caption, ephemeral=True)

@bot.tree.command(name="loc-compare", description="compare a reply across languages")
@app_commands.describe(key="Reply code (as shown in the localization file)")
async def loc_compare_cmd(interaction: discord.Interaction, key: str):
    """Show one reply key in all six languages with its status emoji.

    The tool a translator uses before suggesting anything: it shows what the
    other languages already say, so a suggestion can match them. Long texts
    are cut at 300 characters each and the whole message at 1990, since six
    languages of a long reply do not fit a Discord message.
    """
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    key = key.strip()
    data = compare_reply(key)
    if data is None:
        await interaction.response.send_message(localized("loc_compare_not_found", glang, key=key), ephemeral=True)
        return

    lines = [localized("loc_compare_header", glang, key=key)]
    for code in LANG_ORDER:
        if code not in data:
            continue
        status, text = data[code]
        emoji = LOCALE_STATUS_EMOJI.get(status, "")
        if text is None:
            shown = localized("loc_compare_untranslated", glang)
        else:
            shown = str(text)
            if len(shown) > 300:
                shown = shown[:297] + "..."
        lines.append(f"{emoji} {language_name(code)}: {shown}")
    msg = "\n".join(lines)
    if len(msg) > 1990:
        msg = msg[:1990]
    await interaction.response.send_message(msg)

@bot.tree.command(name="loc-suggest", description="suggest a localization")
@app_commands.describe(language="Language code", code="Reply code", text="Suggested text")
async def loc_suggest_cmd(interaction: discord.Interaction, language: str, code: str, text: str):
    """Send a translation suggestion to the support chat.

    Refuses when no support chat is configured rather than accepting a
    suggestion nobody will ever read. The random dialog code is the only
    handle the conversation has afterwards: the suggester is shown it, and
    /loc-reply takes it.

    The suggester's UI language is stored with the suggestion, so an answer
    written days later still reaches them in the language they were speaking.
    """
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    language = language.strip().lower()
    if language not in SUPPORTED_LANGS:
        await interaction.response.send_message(
            localized("loc_unknown_lang", glang, lang=language, supported=", ".join(available_locales())),
            ephemeral=True
        )
        return
    if not SUPPORT_CHATS.get("discord"):
        await interaction.response.send_message(localized("loc_suggest_no_support", glang), ephemeral=True)
        return

    msg_code = secrets.token_hex(4)
    db.add_loc_suggestion(msg_code, "discord", interaction.user.id, str(interaction.user),
                          language, code.strip(), text, glang)
    avatar_url = None
    try:
        avatar_url = interaction.user.display_avatar.url
    except Exception:
        avatar_url = None
    await post_loc_suggestion(lang=language, key=code.strip(), suggestion=text, code=msg_code,
                              ui_lang=glang, username=str(interaction.user),
                              user_id=interaction.user.id, avatar_url=avatar_url)
    await interaction.response.send_message(localized("loc_suggest_confirm", glang, code=msg_code), ephemeral=True)

@bot.tree.command(name="loc-reply", description="reply to a localization suggestion (bot admins)")
@app_commands.describe(code="Message code from the suggestion", text="Reply text")
async def loc_reply_cmd(interaction: discord.Interaction, code: str, text: str):
    """Answer a suggestion by DM and publish the exchange (Bot Admins).

    The dialog is closed only when the DM actually arrived: a suggester with
    DMs shut keeps their code, so the answer can be retried or delivered by
    hand. The support chat is told either way, which is what makes an
    undeliverable answer visible instead of lost.
    """
    glang = get_guild_lang(interaction.guild_id) if interaction.guild_id else DEFAULT_LANG
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(localized("no_permission", glang), ephemeral=True)
        return

    row = db.get_loc_suggestion(code.strip())
    if not row:
        await interaction.response.send_message(localized("loc_reply_not_found", glang, code=code), ephemeral=True)
        return

    ui_lang = row["ui_lang"] or DEFAULT_LANG
    title = localized("loc_reply_dm_title", ui_lang)
    body = localized("loc_reply_dm_body", ui_lang,
                     suggestion=row["suggestion"], reply=text,
                     name=language_name(row["lang"]), lang=row["lang"], key=row["rkey"])

    ok = False
    if row["platform"] == "discord":
        try:
            user = await bot.fetch_user(int(row["user_id"]))
            await user.send(embed=discord.Embed(title=title, description=body))
            ok = True
        except Exception:
            ok = False

    await post_loc_reply(admin=str(interaction.user), code=code.strip(),
                         ui_lang=ui_lang, title=title, body=body)

    if ok:
        db.delete_loc_suggestion(code.strip())
        await interaction.response.send_message(localized("loc_reply_sent", glang), ephemeral=True)
    else:
        await interaction.response.send_message(localized("loc_reply_failed", glang), ephemeral=True)
