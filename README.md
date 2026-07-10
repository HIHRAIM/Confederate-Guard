# Confederate Guard

Confederate Guard is a self-hosted Discord moderation bot that keeps servers clean of spam and coordinates bans across a group of allied servers. It watches designated “guarded” channels and automatically bans anyone who posts links, invite URLs, or file attachments there, and adds temporary bans with automatic unban, a shared network ban list, per-server log channels, custom ban DMs and appeal text, auto-role assignment, activity-based cross-server verification, and language localization. It is the moderation companion to [Confederate](https://github.com/HIHRAIM/Confederate) and can mirror that bot's verification state.

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
   - Copy `config.example.py` to `src/config.py` and edit it:
     - Set the `DISCORD_BOT_TOKEN` environment variable, or copy `src/.env.example` to `src/.env` and fill it in — the config loads it automatically (already-set environment variables take precedence).
     - `ADMINS` — set of numeric Discord user IDs with global bot-admin rights.
     - `SERVICE_CHATS["discord"]` — channel IDs where the bot sends startup/shutdown events.
     - `BACKUP_CHATS["discord"]` — channel IDs (numeric) where the bot sends automatic database backups every 12 hours.
     - `SUPPORT_CHATS["discord"]` — channel(s) that receive localization suggestions from `/loc-suggest` (as embeds). Confederate Guard only posts to the Discord chat(s).
     - `VERIFIED` — Discord channel ID(s) where **Confederate** publishes a Discord user's ID after they consent to forwarding (only Discord IDs are ever posted there). Confederate Guard reads these channels and adds the posted IDs to its cross-server verified database, granting the verify role on the servers where the user is present. Use the same ID in both bots' configs.
     - `UNVERIFIED` — Discord channel ID(s) where **Confederate** publishes a Discord user's ID when they unverify themselves. Confederate Guard reads these channels and removes the posted IDs from its verified database (without removing any already-granted role). Use the same ID in both bots' configs.

   > The `VERIFIED`/`UNVERIFIED` sync is only needed when Guard runs alongside [Confederate](https://github.com/HIHRAIM/Confederate). If you don't run Confederate, leave the sets empty — activity-based verification (`/setverify`) still works on its own.

5. **Run the bot**
   ```bash
   python src/main.py
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

### Discord commands

| Command | Purpose | Everyone | Server Admins | Bot Admins |
|---|---|:---:|:---:|:---:|
| `/setup <lang> <channel_id> [network]` | Register server: set language, log channel, and optional network ID | ❌ | ✅ | ✅ |
| `/guard <duration> <reason>` | Enable spam guard on the current channel; detected spam triggers a ban with given duration and reason | ❌ | ✅ | ✅ |
| `/ban <user_id> <duration> <reason>` | Ban a user by ID (works even if the user is not on the server) and delete their messages from the last 24 hours. Duration units: `1h`, `1d`, `2m` (months), `3y` (years), `infinity` (10 years); max 10 years | ❌ | ✅ | ✅ |
| `/unban <user_id>` | Unban a user by ID on this server; also clears the scheduled-unban timer, network-ban enforcement marker and the prior-ban notice record | ❌ | ✅ | ✅ |
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
| `/globalban <user_id> <reason> <duration>` | Ban on this server (deleting the user's messages from the last 24 hours) and add to the network ban list. Duration units: `1h`, `2m` (months), `3y` (years), `infinity` (10 years); max 10 years | ❌ | ❌ | ✅ |
| `/globalunban <user_id>` | Lift a network ban: remove it from the database and unban on every network server where it was applied | ❌ | ❌ | ✅ |
| `/loc-reply <code> <text>` | Reply (via DM) to a user's localization suggestion | ❌ | ❌ | ✅ |
| `/banlink <link>` | Add a URL or Discord invite to the global banned-link list | ❌ | ❌ | ✅ |
| `/unbanlink <link_id>` | Remove a link from the global banned-link list by its number | ❌ | ❌ | ✅ |
| `/list_chats` | List all servers the bot is currently in | ❌ | ❌ | ✅ |
| `/force_leave <server_id>` | Force the bot to leave a server and remove its data | ❌ | ❌ | ✅ |
| `/backup` | Send current database backup file | ❌ | ❌ | ✅ |

---

## Mechanics

Every user-facing mechanic of Confederate Guard, in one place.

### Spam guard

`/guard <duration> <reason>` turns the current channel into a **guarded** channel — one where members aren't expected to post. Every message written there is deleted; if it contained **spam** (any URL/invite link or any attachment — file, image, video, gif), the author is additionally DM'd and banned with the configured duration and reason, their messages from the last 24 hours are purged across the server, and the ban is logged to the `/setup` log channel. Plain-text messages with no link or attachment are just deleted silently, without a ban. Guarding requires the server to be registered with `/setup` first.

In channels that are **not** guarded, the bot still deletes any message that contains a link from the global banned-link list (unless the author is an admin), but never bans for it.

### Manual and temporary bans

`/ban <user_id> <duration> <reason>` bans a user by ID — it works even if the user isn't currently on the server — records the ban in the server's history and deletes the user's messages from the last 24 hours. If the duration is finite, an automatic unban is scheduled; a background loop checks every 60 seconds and lifts bans whose time is up (and cleans expired network bans in the same pass). `/unban <user_id>` unbans immediately and clears the scheduled-unban timer, the network-ban enforcement marker, and the prior-ban notice record for that server. Duration units are `1h`, `1d`, `2m` (months), `3y` (years) and `infinity` (= 10 years), capped at 10 years.

### Ban DMs and appeals

Before an automatic spam ban (and before network-ban enforcement), the bot tries to DM the user. For spam bans the DM is either the custom text set with `/dm` (where `{server}` is replaced with the server name) or a localized default, with the `/setappeal` text appended on its own line if one is set. Network-ban enforcement sends its own localized DM that includes the remaining ban duration. DMs are best-effort — a closed DM never blocks the ban.

### Auto-role

`/autorole <role_id>` assigns a role to every member automatically: the role is added when a member joins, and running the command also assigns it to all current members right away. This is independent of verification — it applies to everyone.

### Activity-based verification

`/setverify <role_id> [channel_id]` enables automatic verification. Once configured, the bot records the first calendar date (UTC) on which each member sends a **genuine chat message**. When a member's messages span **more than one date** (they wrote on one day and returned later), the bot assigns the configured role and posts a notification — in the channel given to `/setverify`, or, if none was given, in the `/setup` log channel.

Discord's automatic **“member joined” notice** (and other system notices such as boosts and pinned-message notifications) are authored by the member but are **not** counted as chat activity. This matters: counting the join as a first activity date used to let someone who merely joined get verified by posting a single time the next day — including a spammer who joins and then blasts the channels a day later. Only real messages establish and advance a member's activity dates, so genuine two-day participation is required.

### Cross-server verification

The verified-user database is **shared across all servers**. When a member becomes verified (including verifications synced from Confederate), the role is granted right away on **every** server they belong to that has `/setverify` configured — and again whenever they later join or write on such a server. For a verification carried over from elsewhere, the notification states that it came from another server. Each server announces a given member only once; on startup the bot silently backfills the role for already-verified members who are present but hadn't received it yet, without announcements.

### Cross-bot verification sync

When run alongside [Confederate](https://github.com/HIHRAIM/Confederate), Guard watches the configured `VERIFIED`/`UNVERIFIED` channels. Confederate posts a bare Discord user ID to `VERIFIED` when that user consents to message forwarding, and to `UNVERIFIED` when they unverify themselves. Guard mirrors these into its shared verified database: an ID posted to `VERIFIED` is added and the verify role is propagated (with announcements) to every `/setverify` server the user is on; an ID posted to `UNVERIFIED` is removed from the database (already-granted roles are left in place). Only bare Discord IDs are exchanged — never message content.

### Network bans

Servers grouped under the same `network` (set in `/setup`) share a ban list:

- `/globalban` bans the user on the current server, deletes their messages from the last 24 hours there, and records a network ban (with reason and expiry). It is also pushed to other network servers that have `/setgbans enable` if the user is present there.
- `/setgbans enable` bans globally-banned members who are present or who later join; `/setgbans disable` reverts only the bans this enforcement applied (not `/ban` or `/globalban` bans).
- `/globalunban` removes the network ban and unbans the user everywhere it was applied, also clearing the ban-history records on those servers so the prior-ban alert stops firing for the lifted ban. A local `/unban` clears the same records for one server.

### Prior-ban alerts

When a member who was **ever** banned on any server in the network joins another network server, the bot posts an alert in that server's log channel, naming the server where the earlier ban was issued. This is only a notice — no action is taken automatically.

### Banned-link list

Bot Admins maintain a global list of forbidden URLs and Discord invites with `/banlink` and `/unbanlink`, and `/links` shows the numbered list. Messages containing a listed link are deleted anywhere the bot can see them (guarded channels delete and possibly ban on any link; non-guarded channels delete only listed links, and never from an admin).

### Localization

All bot-facing strings live in per-language JSON files under `src/i18n/` (`ru`, `uk`, `pl`, `en`, `es`, `pt`), each with a translation **status**: `verified` (🟩), `unverified` (🟧) or `untranslated` (🟥, a key missing relative to the reference language). `/locale` shows each language with an emoji bar and its percentage of verified strings; `/locale <code>` sends that language's JSON file. `/loc-compare <code>` compares one reply across all languages with status emoji. `/loc-suggest <lang> <code> <text>` forwards a translation suggestion to `SUPPORT_CHATS`, tagged with a unique dialog code; `/loc-reply <code> <text>` (Bot Admins) DMs the original suggester and removes the code. Suggestion codes are kept at most **1 year**.

### Presence

The bot's Discord presence rotates through all six languages every 60 seconds, showing “Guarding N communities” with the correct plural form for each language; **N** is the number of servers registered via `/setup`.

### Service events and automatic backups

Startup and shutdown notices go to the `SERVICE_CHATS` channels. Encrypted database backups (authenticated BLAKE2 keystream + tag, standard library only) are posted to `BACKUP_CHATS` every 12 hours; `/backup` returns one on demand, and `python src/restore_backup.py <input.db.enc> <output.db>` decrypts it with the `BACKUP_KEY` environment variable. The destination channels only ever store ciphertext.

---

## Data collection and retention

The bot stores operational data in local SQLite (`guard.db`) to provide spam protection, moderation, and automation features. The full privacy policy lives in [PRIVACY.md](PRIVACY.md).

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
  - Guild ID, user ID, and the first calendar date a member was seen sending a genuine (non-system) message — used to detect activity spanning more than one date.
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
