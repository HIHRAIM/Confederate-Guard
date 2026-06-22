# Confederate Guard

Confederate Guard is a Discord moderation bot that protects channels from spam by automatically banning users who post links, invite URLs, or file attachments in designated guarded channels. It supports temporary bans with automatic unban, cross-server reporting via networks, per-server log channels, custom DM notifications, auto-role assignment, cross-server user verification, and language localization.

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
> - `/report` requires the server to be assigned to a network via `/setup`.
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
| `/report [message_id]` (also as a message context menu action) | Forward a report about a user to all log channels in the same network | ❌ | ✅ | ✅ |
| `/dm <text>` | Set a custom DM message sent to users before banning (use `{server}` for the server name) | ❌ | ✅ | ✅ |
| `/autorole <role_id>` | Set a role to be automatically assigned to all members on join; immediately assigns it to existing members | ❌ | ✅ | ✅ |
| `/setverify <role_id> [channel_id]` | Verify members who have chatted on more than one calendar day, give them a role, and announce it; the verified-user database is shared across all servers | ❌ | ✅ | ✅ |
| `/links` | Show the list of globally banned links/invites | ❌ | ✅ | ✅ |
| `/help` | Show the list of available commands | ✅ | ✅ | ✅ |
| `/setadmin <user_id>` | Grant a user Server Admin rights on this server | ❌ | ❌ | ✅ |
| `/remadmin <user_id>` | Revoke a user's Server Admin rights on this server | ❌ | ❌ | ✅ |
| `/banlink <link>` | Add a URL or Discord invite to the global banned-link list | ❌ | ❌ | ✅ |
| `/unbanlink <link_id>` | Remove a link from the global banned-link list by its number | ❌ | ❌ | ✅ |
| `/list_chats` | List all servers the bot is currently in | ❌ | ❌ | ✅ |
| `/force_leave <server_id>` | Force the bot to leave a server and remove its data | ❌ | ❌ | ✅ |
| `/backup` | Send current database backup file | ❌ | ❌ | ✅ |

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

### Retention periods

- **Active bans**: removed automatically after the ban expires and the unban is processed (checked every 60 seconds).
- **Guild settings, guarded channels, custom DM, auto-role, server admins, banned links, verification settings, verify grants, user activity**: kept until manually changed or removed (guild-scoped entries are deleted when the bot leaves a server via `/force_leave`).
- **Verified users (shared)**: kept globally so verification carries across servers; not removed when the bot leaves any single server.

### Data usage boundaries

- The bot uses stored data only to operate spam protection, bans, log delivery, cross-server reports, auto-role assignment, and cross-server verification.
- It does not implement analytics or tracking pipelines in this repository.
- Data is local to the bot runtime environment unless your deployment adds external backup or logging.
