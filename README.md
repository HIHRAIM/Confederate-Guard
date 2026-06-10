# Confederate Guard

Confederate Guard is a Discord moderation bot that protects channels from spam by automatically banning users who post links, invite URLs, or file attachments in designated guarded channels. It supports temporary bans with automatic unban, cross-server reporting via networks, per-server log channels, custom DM notifications, auto-role assignment, and language localization.

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

### Discord commands

| Command | Purpose | Everyone | Server Admins | Bot Admins |
|---|---|:---:|:---:|:---:|
| `/setup <lang> <channel_id> [network]` | Register server: set language, log channel, and optional network ID | ❌ | ✅ | ✅ |
| `/guard <duration> <reason>` | Enable spam guard on the current channel; detected spam triggers a ban with given duration and reason | ❌ | ✅ | ✅ |
| `/ban <user_id> <duration> <reason>` | Ban a user by ID (works even if the user is not on the server) | ❌ | ✅ | ✅ |
| `/report [message_id]` (also as a message context menu action) | Forward a report about a user to all log channels in the same network | ❌ | ✅ | ✅ |
| `/dm <text>` | Set a custom DM message sent to users before banning (use `{server}` for the server name) | ❌ | ✅ | ✅ |
| `/autorole <role_id>` | Set a role to be automatically assigned to all members on join; immediately assigns it to existing members | ❌ | ✅ | ✅ |
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

### Retention periods

- **Active bans**: removed automatically after the ban expires and the unban is processed (checked every 60 seconds).
- **Guild settings, guarded channels, custom DM, auto-role, server admins, banned links**: kept until manually changed or removed.

### Data usage boundaries

- The bot uses stored data only to operate spam protection, bans, log delivery, cross-server reports, and auto-role assignment.
- It does not implement analytics or tracking pipelines in this repository.
- Data is local to the bot runtime environment unless your deployment adds external backup or logging.
