# Confederate Guard

Confederate Guard is a Discord moderation bot that protects channels from spam by automatically banning users who post links, invite URLs, or file attachments in designated guarded channels. It supports temporary bans with automatic unban, per-server log channels, custom DM notifications, auto-role assignment, cross-server user verification, and language localization.

## Requirements

- Python **3.10+** (recommended 3.11+)
- A Discord bot token
- SQLite (uses local `guard.db`, no external DB required)
- Python packages used by the project:
  - `discord.py`

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/HIHRAIM/Confederate-Guard
   cd Confederate-Guard
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install discord.py
   ```

4. **Create config file**
   - Edit `config.py`:
     - Set `DISCORD_BOT_TOKEN` environment variable, or paste the token directly.
     - `ADMINS` — set of numeric Discord user IDs with global bot-admin rights.
     - `SERVICE_CHATS["discord"]` — channel IDs where the bot sends startup/shutdown events.
     - `BACKUP_CHATS["discord"]` — channel IDs (numeric) where the bot sends automatic database backups every 12 hours.
     - `SUPPORT_CHATS["discord"]` — channel(s) that receive localization suggestions from `/loc-suggest` (as embeds). guard_bot only posts to the Discord chat(s).
     - `VERIFIED` — Discord channel ID(s) where **bridge_bot** publishes a user's ID after they consent to forwarding. guard_bot reads these channels and adds the posted IDs to its cross-server verified database (announcing in the log channel). Use the same ID in both bots' configs.
     - `UNVERIFIED` — Discord channel ID(s) where **bridge_bot** publishes a user's ID when they unverify themselves. guard_bot reads these channels and removes the posted IDs from its verified database (without removing any already-granted role). Use the same ID in both bots' configs.

5. **Run the bot**
   ```bash
   python main.py
   ```

---

## Commands

Permission roles used below:

- **Everyone** — any user in the server.
- **Server Admins** — per-server admins, granted by Bot Admins via `/setadmin` (stored in the database, scoped to one server).
- **Bot Admins** — global admins defined in `config.py` (`ADMINS`); have Server Admin rights everywhere plus bot-wide commands.

> Notes:
> - Guard only bans on spam detection (URLs, invite links, attachments); plain text messages in guarded channels are deleted silently without a ban.
> - `/setverify` and `/autorole` require the server to be registered via `/setup`.

### Verification

`/setverify` enables automatic verification on a server. After it is configured, the bot tracks the first calendar date (UTC) on which each member sends a message. Once a member's messages span **more than one date** (e.g. they wrote on one day and returned later), the bot:

1. Assigns the configured role to the member.
2. Posts a notification in the configured channel — or, if no channel was given, in the `/setup` log channel.

The verified-user database is **shared across all servers**. If a member was verified on one server and later joins (or writes in) another server that also has `/setverify` configured, they are verified there automatically as well. In that case the notification states that the verification carried over from another server. Each server announces a given member only once.

### Discord commands

| Command | Purpose | Everyone | Server Admins | Bot Admins |
|---|---|:---:|:---:|:---:|
| `/setup <lang> <channel_id> [network]` | Register server: set language, log channel, and optional network ID | ❌ | ✅ | ✅ |
| `/guard <duration> <reason>` | Enable spam guard on the current channel; detected spam triggers a ban with given duration and reason | ❌ | ✅ | ✅ |
| `/ban <user_id> <duration> <reason>` | Ban a user by ID (works even if the user is not on the server) | ❌ | ✅ | ✅ |
| `/dm <text>` | Set a custom DM message sent to users before banning (use `{server}` for the server name) | ❌ | ✅ | ✅ |
| `/autorole <role_id>` | Set a role to be automatically assigned to all members on join; immediately assigns it to existing members | ❌ | ✅ | ✅ |
| `/setverify <role_id> [channel_id]` | Verify members who have chatted on more than one calendar day, give them a role, and announce it; the verified-user database is shared across all servers | ❌ | ✅ | ✅ |
| `/setgbans <enable\|disable>` | Enforce network bans on this server. On `enable`, globally-banned members present/joining are banned (with a DM, the remaining duration, and a localized reason suffix) and reported in the log channel; the reply shows the total network-banned count. On `disable`, only the bans this command applied are lifted | ❌ | ✅ | ✅ |
| `/setappeal <text>` | Set text appended on a new line to the spam-ban DM | ❌ | ✅ | ✅ |
| `/links` | Show the list of globally banned links/invites | ❌ | ✅ | ✅ |
| `/help` | Show the list of available commands | ✅ | ✅ | ✅ |
| `/locale [code]` | Show localization status (bar + verified %), or send a language's localization file (10-min per-server cooldown for the file) | ✅ | ✅ | ✅ |
| `/loc-compare <code>` | Compare a reply across all languages with status emoji | ✅ | ✅ | ✅ |
| `/loc-suggest <lang> <code> <text>` | Suggest a localization; sent to the support chat | ✅ | ✅ | ✅ |
| `/setadmin <user_id>` | Grant a user Server Admin rights on this server; DMs the user | ❌ | ❌ | ✅ |
| `/remadmin <user_id>` | Revoke a user's Server Admin rights on this server | ❌ | ❌ | ✅ |
| `/globanban <user_id> <reason> <duration>` | Ban on this server and add to the network ban list. Duration units: `1h`, `2m` (months), `3y` (years), `infinity` (10 years); max 10 years | ❌ | ❌ | ✅ |
| `/globalunban <user_id>` | Lift a network ban: remove it from the database and unban on every network server where it was applied | ❌ | ❌ | ✅ |
| `/loc-reply <code> <text>` | Reply (via DM) to a user's localization suggestion | ❌ | ❌ | ✅ |
| `/banlink <link>` | Add a URL or Discord invite to the global banned-link list | ❌ | ❌ | ✅ |
| `/unbanlink <link_id>` | Remove a link from the global banned-link list by its number | ❌ | ❌ | ✅ |
| `/list_chats` | List all servers the bot is currently in | ❌ | ❌ | ✅ |
| `/force_leave <server_id>` | Force the bot to leave a server and remove its data | ❌ | ❌ | ✅ |
| `/backup` | Send current database backup file | ❌ | ❌ | ✅ |

---

## Network bans

Servers grouped under the same `network` (set in `/setup`) share a ban list:

- `/globanban` bans the user on the current server and records a network ban (with reason and expiry). It is also pushed to other network servers that have `/setgbans enable` if the user is present there.
- `/setgbans enable` bans globally-banned members who are present or who later join; `/setgbans disable` reverts only the bans this enforcement applied (not `/ban` or `/globanban` bans).
- `/globalunban` removes the network ban and unbans the user everywhere it was applied.
- When a member who was **ever** banned on any server in the network joins another network server, the bot posts an alert in that server's log channel.

## Localization

All bot-facing strings live in per-language JSON files under `src/i18n/` (`ru`, `uk`, `pl`, `en`, `es`, `pt`), each with a translation **status**: `verified` (🟩), `unverified` (🟧) or `untranslated` (🟥). `/locale`, `/loc-compare`, `/loc-suggest` and `/loc-reply` work as in bridge_bot; suggestion dialog codes are kept at most **1 year** and removed once answered. The bot's presence rotates through the languages, showing "Guarding N communities" with correct plural forms.

---

## Data collection and retention

The bot stores operational data in local SQLite (`guard.db`) to provide spam protection, moderation, and automation features.

### What data is stored

- **Guild settings**
  - Guild ID, language, log channel ID, network ID.
- **Guarded channels**
  - Channel ID, parent guild ID, ban duration, ban reason.
- **Custom DM**
  - Per-guild custom ban notification message.
- **Active bans**
  - Guild ID, banned user ID, scheduled unban timestamp.
- **Auto-role**
  - Per-guild role ID assigned to new members.
- **Server admins**
  - Guild ID, user ID of users granted Server Admin rights via `/setadmin`.
- **Banned links**
  - Globally banned URLs and Discord invite codes/links.
- **Verification settings**
  - Per-guild role ID and optional announcement channel ID set via `/setverify`.
- **Verified users (shared)**
  - Global, cross-server set of verified user IDs, with the originating guild ID and verification timestamp.
- **Verify grants**
  - Guild ID and user ID recording that the verify role was already granted and announced on that server (prevents duplicate announcements).
- **User activity**
  - Guild ID, user ID, and the first calendar date a member was seen messaging (used to detect activity spanning more than one date).
- **Network bans**
  - Per-network user bans (reason, origin guild, issued/expiry timestamps), per-guild `/setgbans` toggle, and the set of bans applied by enforcement (so they can be reverted).
- **Ban history**
  - Guild ID, user ID and timestamp of every ban the bot issues (used to alert when a previously-banned user joins a network server).
- **Appeal text**
  - Per-guild text appended to the spam-ban DM, set via `/setappeal`.
- **Localization suggestions**
  - `/loc-suggest` dialog codes: submitter platform/ID/username, target language, reply code, suggested text.

### Retention periods

- **Active bans**: removed automatically after the ban expires and the unban is processed (checked every 60 seconds). Expired network bans are cleaned in the same loop.
- **Guild settings, guarded channels, custom DM, auto-role, server admins, banned links, verification/appeal/`setgbans` settings**: kept until manually changed or removed (guild-scoped entries are deleted when the bot leaves a server via `/force_leave`).
- **Localization-suggestion codes**: up to **1 year**, and removed immediately once answered with `/loc-reply`.
- **Stored user IDs (verified users, verify grants, user activity, ban history)**: automatically deleted 10 years after they were added. The cleanup runs on startup and once every 24 hours.

### Data usage boundaries

- The bot uses stored data only to operate spam protection, bans, log delivery, auto-role assignment, and cross-server verification.
- It does not implement analytics or tracking pipelines in this repository.
- Database backups are encrypted (authenticated BLAKE2 keystream + tag, standard library only — no third-party crypto dependency) before leaving the runtime; the destination only ever stores ciphertext. The decryption key lives in the `BACKUP_KEY` environment variable and must be kept out of the repository.
