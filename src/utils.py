import re
import db
from config import ADMINS

SUPPORTED_LANGS = {"ru", "uk", "pl", "en", "es", "pt"}
DEFAULT_LANG = "en"

import os as _i18n_os
import json as _i18n_json

_I18N_DIR = _i18n_os.path.join(_i18n_os.path.dirname(__file__), "i18n")

LOCALE_STATUS_EMOJI = {"verified": "\U0001F7E9", "unverified": "\U0001F7E7", "untranslated": "\U0001F7E5"}

def _load_i18n():
    """Build the runtime localization structures from the i18n/<lang>.json files.

    Returns (locale, status, flat):
      locale[key][lang] = text, with dotted keys 'group.sub' rebuilt into
        locale[group][sub][lang] so the legacy localized_* helpers keep working.
      status[flat_key][lang] = 'verified' | 'unverified' | 'untranslated'
      flat[lang][flat_key] = text
    """
    locale, status, flat = {}, {}, {}
    if _i18n_os.path.isdir(_I18N_DIR):
        for _fname in sorted(_i18n_os.listdir(_I18N_DIR)):
            if not _fname.endswith(".json"):
                continue
            _lang = _fname[:-5]
            with open(_i18n_os.path.join(_I18N_DIR, _fname), encoding="utf-8") as _f:
                _entries = _i18n_json.load(_f)
            flat[_lang] = {}
            for _k, _entry in _entries.items():
                _text = _entry["text"]
                flat[_lang][_k] = _text
                status.setdefault(_k, {})[_lang] = _entry.get("status", "unverified")
                if "." in _k:
                    _g, _s = _k.split(".", 1)
                    locale.setdefault(_g, {}).setdefault(_s, {})[_lang] = _text
                else:
                    locale.setdefault(_k, {})[_lang] = _text
    return locale, status, flat

_LOCALE, _LOCALE_STATUS, _LOCALE_FLAT = _load_i18n()

LANGUAGE_NAMES = {
    "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439",
    "uk": "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430",
    "pl": "Polski",
    "en": "English",
    "es": "Espa\u00f1ol",
    "pt": "Portugu\u00eas",
}

LANG_ORDER = ["ru", "uk", "pl", "en", "es", "pt"]

def language_name(code):
    return LANGUAGE_NAMES.get(code, code)

def available_locales():
    """Languages that have an i18n file, in display order."""
    return [L for L in LANG_ORDER if L in _LOCALE_FLAT]

def reply_keys():
    """All reply codes, taken from the reference (DEFAULT_LANG) localization."""
    return sorted(_LOCALE_FLAT.get(DEFAULT_LANG, {}).keys())

def get_reply(lang, key):
    return _LOCALE_FLAT.get(lang, {}).get(key)

def reply_status(lang, key):
    """'verified' | 'unverified' | 'untranslated', or None if the key is unknown."""
    known = any(key in _LOCALE_FLAT.get(L, {}) for L in _LOCALE_FLAT)
    if not known:
        return None
    if key not in _LOCALE_FLAT.get(lang, {}):
        return "untranslated"
    return _LOCALE_STATUS.get(key, {}).get(lang, "unverified")

def locale_stats(lang):
    """Counts relative to the DEFAULT_LANG key set, plus the verified percentage."""
    ref = list(_LOCALE_FLAT.get(DEFAULT_LANG, {}).keys())
    total = len(ref)
    have = _LOCALE_FLAT.get(lang, {})
    verified = unverified = untranslated = 0
    for k in ref:
        if k not in have:
            untranslated += 1
            continue
        st = _LOCALE_STATUS.get(k, {}).get(lang, "unverified")
        if st == "verified":
            verified += 1
        elif st == "untranslated":
            untranslated += 1
        else:
            unverified += 1
    percent = round(verified / total * 100) if total else 0
    return {"total": total, "verified": verified, "unverified": unverified,
            "untranslated": untranslated, "percent": percent}

def locale_bar(lang, width=12):
    s = locale_stats(lang)
    total = s["total"] or 1
    v = round(s["verified"] / total * width)
    u = round(s["unverified"] / total * width)
    v = min(v, width)
    u = min(u, width - v)
    t = width - v - u
    return LOCALE_STATUS_EMOJI["verified"] * v + LOCALE_STATUS_EMOJI["unverified"] * u + LOCALE_STATUS_EMOJI["untranslated"] * t

def compare_reply(key):
    """Return {lang: (status, text|None)} across all languages, or None if unknown."""
    known = any(key in _LOCALE_FLAT.get(L, {}) for L in _LOCALE_FLAT)
    if not known:
        return None
    out = {}
    for L in LANG_ORDER:
        text = _LOCALE_FLAT.get(L, {}).get(key)
        if text is None:
            out[L] = ("untranslated", None)
        else:
            out[L] = (_LOCALE_STATUS.get(key, {}).get(L, "unverified"), text)
    return out

URL_RE = re.compile(
    r"(https?://|discord\.gg/|discord\.com/invite/)\S+",
    re.IGNORECASE
)

INVITE_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)",
    re.IGNORECASE
)

def normalize_banned_url(raw):
    value = raw.strip().lower()
    value = re.sub(r"^https?://", "", value)
    if value.startswith("www."):
        value = value[4:]
    return value.rstrip("/")

def classify_banned_link(raw):
    """Returns ('invite', code) or ('url', normalized_url) for a user-supplied link."""
    raw = raw.strip().rstrip("/")
    m = INVITE_LINK_RE.fullmatch(raw)
    if m:
        return "invite", m.group(1)
    if re.fullmatch(r"[A-Za-z0-9-]+", raw):
        return "invite", raw
    return "url", normalize_banned_url(raw)

def message_has_banned_link(content):
    """Returns True if the text contains a banned invite code or a banned URL."""
    if not content:
        return False
    rows = db.get_banned_links()
    if not rows:
        return False
    invites = {r["value"] for r in rows if r["kind"] == "invite"}
    urls = [r["value"] for r in rows if r["kind"] == "url"]
    for m in INVITE_LINK_RE.finditer(content):
        if m.group(1) in invites:
            return True
    if urls:
        low = content.lower()
        for u in urls:
            if u in low:
                return True
    return False

def is_admin(user_id, guild_id=None):
    if user_id in ADMINS:
        return True
    if guild_id is not None:
        return db.is_guild_admin(guild_id, user_id)
    return False

def get_guild_lang(guild_id):
    row = db.get_guild(guild_id)
    if row and row["lang"] in SUPPORTED_LANGS:
        return row["lang"]
    return DEFAULT_LANG

def localized(_key, locale, **kwargs):
    table = _LOCALE.get(_key, {})
    template = table.get(locale, table.get(DEFAULT_LANG, _key))
    try:
        return template.format(**kwargs)
    except Exception:
        return template

def parse_duration(text):
    """Returns duration in seconds, or None for infinity. Raises ValueError on invalid format."""
    text = text.strip().lower()
    if text == "infinity":
        return None
    m = re.fullmatch(r"(\d+)(s|m|h|d|w)", text)
    if not m:
        raise ValueError("invalid_duration")
    n = int(m.group(1))
    unit = m.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return n * multipliers[unit]

GLOBAL_BAN_MAX_SECONDS = 10 * 365 * 86400

def parse_global_duration(text):
    """Duration for network-wide bans and /ban.

    Only these units are accepted: h=hours, d=days, m=months, y=years.
    'infinity' means 10 years. Everything is capped at 10 years. Always returns a
    finite number of seconds (never None). Raises ValueError on invalid format.
    """
    text = text.strip().lower()
    if text == "infinity":
        return GLOBAL_BAN_MAX_SECONDS
    m = re.fullmatch(r"(\d+)(h|d|m|y)", text)
    if not m:
        raise ValueError("invalid_duration")
    n = int(m.group(1))
    unit = m.group(2)
    multipliers = {
        "h": 3600,
        "d": 86400,
        "m": 30 * 86400,
        "y": 365 * 86400,
    }
    return min(n * multipliers[unit], GLOBAL_BAN_MAX_SECONDS)

def format_duration(seconds, lang):
    if seconds is None:
        return localized("duration_infinity", lang)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"
    if seconds < 30 * 86400:
        return f"{seconds // 604800}w"
    if seconds < 365 * 86400:
        return f"{seconds // (30 * 86400)}mo"
    return f"{seconds // (365 * 86400)}y"

def message_has_spam(message):
    """Returns True if the message contains a URL or any attachment (file, image, video, gif)."""
    if message.attachments:
        return True
    if URL_RE.search(message.content or ""):
        return True
    return False

import itertools

_status_lang_cycle = itertools.cycle(["ru", "uk", "pl", "en", "es", "pt"])

def plural_ru(n, forms):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    return forms[2]

def plural_pl(n, forms):
    n = abs(int(n))
    if n == 1:
        return forms[0]
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    return forms[2]

def plural_en(n, forms):
    return forms[0] if n == 1 else forms[1]

def get_next_status_text(servers):
    """Cycle through the languages, declining 'communities' (localizations in i18n)."""
    code = next(_status_lang_cycle)
    template = _LOCALE_FLAT.get(code, {}).get("status_template") or _LOCALE_FLAT.get(DEFAULT_LANG, {}).get("status_template")
    forms = _LOCALE_FLAT.get(code, {}).get("status_communities_forms") or _LOCALE_FLAT.get(DEFAULT_LANG, {}).get("status_communities_forms")
    if not template:
        return f"Guarding {servers} communities"
    if not forms:
        forms = ["community", "communities", "communities"]
    if code in ("ru", "uk"):
        word = plural_ru(servers, forms)
    elif code == "pl":
        word = plural_pl(servers, forms)
    else:
        word = plural_en(servers, forms)
    return template.format(servers=servers, servers_word=word)
