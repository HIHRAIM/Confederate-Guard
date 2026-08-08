# Confederate Guard

Confederate Guard is a self-hosted Discord moderation bot that keeps servers clean of spam and coordinates bans across a group of allied servers. It watches designated “guarded” channels and automatically bans anyone who posts links, invite URLs, or file attachments there, and adds temporary bans with automatic unban, a shared network ban list, per-server log channels, custom ban DMs and appeal text, auto-role assignment, activity-based cross-server verification, and language localization. It is the moderation companion to [Confederate](https://github.com/HIHRAIM/Confederate): it can mirror that bot's verification state, and together the two run a shared ban-appeal flow on a dedicated appeal server.

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
     - `VERIFIED` — Discord channel ID(s) where **Confederate** (or anyone else) publishes a Discord user ID, optionally followed by the ID of the server the consent was given on, after the user consents to forwarding (only Discord IDs are ever posted there). Confederate Guard reads these channels, adds the posted IDs to its cross-server verified database, grants the verify role on the servers where the user is present, and acknowledges each processed message with a ✅ reaction. Use the same ID in both bots' configs.
     - `UNVERIFIED` — Discord channel ID(s) where **Confederate** (or anyone else) publishes a bare Discord user ID when the user unverifies themselves. Confederate Guard reads these channels, removes the posted IDs from its verified database (without removing any already-granted role) and acknowledges each processed message with a ✅ reaction. Use the same ID in both bots' configs.
     - `PURGATORIUM_GUILD_ID` / `PURGATORIUM_INVITE_URL` — the shared appeal server and the invite link appended to ban DMs (see *Purgatorium appeals*).
     - `BRIDGE_BOT_ID` — Confederate's bot user ID: it is mentioned in the appeal-hint DM, and only messages from it (or from a bot admin) are honored in the sync channels below.
     - `APPEAL_PARDON_CHANNELS["discord"]` — channel(s) where **Confederate** posts the bare user ID of an appellant whose appeal the consuls granted; Guard lifts all of that user's bans.
     - `APPEAL_BANINFO_CHANNELS["discord"]` — channel(s) where **Confederate** posts `<user_id> <thread_id>` for every new appeal; Guard replies in the appeal thread with the user's ban summary (see *Purgatorium appeals*).
     - `CONSULS` — role ID(s) on the Purgatorium server handed to consuls (appointed via `/setconsul`) when they join.
     - `TRIBUNAL_CHANNELS["discord"]` — channel(s) where every automatic spam-guard ban is put up for review with **Global ban** / **Ignore** buttons (see *Tribunal*). Leave the set empty to turn the mechanic off.

   > The `VERIFIED`/`UNVERIFIED` sync is only needed when Guard runs alongside [Confederate](https://github.com/HIHRAIM/Confederate). If you don't run Confederate, leave the sets empty — activity-based verification (`/setverify`) still works on its own.

5. **Run the bot**
   ```bash
   python src/main.py
   ```

---

## Project structure

The code lives in `src/`, split into packages by domain. `ARCHITECTURE.md` describes how the pieces work together and carries a feature → file table.

```
src/
  main.py              entry point: the client plus the background loops
                       (backups, retention, the setup deadline)
  config.py            this deployment's ids (untracked; config.example.py is the template)
  env_loader.py        reads src/.env
  utils.py             localization runtime, duration parsing, link matching,
                       spam detection, service events
  setup_deadline.py    leaves servers nobody registered with /setup
  backup_crypto.py     encrypted database snapshots
  restore_backup.py    their restore tool

  db/                  SQLite layer: one connection, one module per domain
    __init__.py          connection (conn/cur), init(), the whole public API
    schema.py            every CREATE TABLE + additive migrations
    guilds.py   settings.py  admins.py  users.py
    bans.py     tribunal.py
    onboarding.py        the setup deadline and the retention sweep

  discord_bot/         the bot itself
    client.py            the client, its loops, persistent-view restore
    events.py            inbound events (messages, joins, server add/remove)
    verification.py      activity tracking, role grants, cross-bot sync
    bans.py              ban delivery, network enforcement, unban loop
    purgatorium.py       Guard's half of the shared appeal system
    tribunal.py          review of automatic bans, promotion to network bans
    commands/            slash commands: guilds, settings, bans, links,
                         admins, locale, user

  i18n/                the six localization files
```

---

## Commands

Permission roles used below:

- **Everyone** — any user in the server.
- **Server Admins** — per-server admins, granted by Bot Admins via `/setadmin` (stored in the database, scoped to one server).
- **Localizers** — users granted `/localizer-add`: they may edit this bot's localization through the [control panel](https://github.com/HIHRAIM/Confederate-Panel). Server Admins hold the status implicitly while they remain admins.
- **Bot Admins** — global admins defined in `config.py` (`ADMINS`); have Server Admin rights everywhere plus bot-wide commands.

> Notes:
> - Guard only bans on spam detection (URLs, invite links, attachments); plain text messages in guarded channels are deleted silently without a ban.
> - `/setverify` and `/autorole` require the server to be registered via `/setup`.

### Discord commands

| Command | Purpose | Everyone | Server Admins | Bot Admins |
|---|---|:---:|:---:|:---:|
| `/setup <lang> <channel_id> [network]` | Register server: set language, log channel, and optional network ID | ❌ | ✅ | ✅ |
| `/guard <duration> <reason>` | Enable spam guard on the current channel; detected spam triggers a ban with given duration and reason. Duration units: `30s`, `30m`, `2h`, `1d`, `1w`, `infinity` | ❌ | ✅ | ✅ |
| `/ban <user_id> <duration> <reason>` | Ban a user by ID (works even if the user is not on the server) and delete their messages from the last 24 hours. Duration units: `1h`, `1d`, `2m` (months), `3y` (years), `infinity` (10 years); max 10 years | ❌ | ✅ | ✅ |
| `/unban <user_id>` | Unban a user by ID on this server; also clears the scheduled-unban timer, network-ban enforcement marker and the prior-ban notice record | ❌ | ✅ | ✅ |
| `/dm <text>` | Set a custom DM message sent to users before a spam-guard ban (`{server}` — the server name, `{reason}` — the guard's ban reason) | ❌ | ✅ | ✅ |
| `/autorole <role_id>` | Set a role to be automatically assigned to all members on join; immediately assigns it to existing members | ❌ | ✅ | ✅ |
| `/setverify <role_id> [channel_id]` | Verify members who have chatted on more than one calendar day, give them a role, and announce it; the verified-user database is shared across all servers | ❌ | ✅ | ✅ |
| `/setgbans <enable\|disable>` | Enforce network bans on this server. On `enable`, globally-banned members present/joining are banned (with a DM, the remaining duration, and a localized reason suffix) and reported in the log channel; the reply shows the total network-banned count. On `disable`, only the bans this command applied are lifted | ❌ | ✅ | ✅ |
| `/setappeal <text>` | Set text appended on a new line to the spam-ban DM | ❌ | ✅ | ✅ |
| `/links` | Show the list of globally banned links/invites — numbered, 10 per page, with arrow buttons past the first page | ❌ | ✅ | ✅ |
| `/help` | Show the list of available commands | ✅ | ✅ | ✅ |
| `/locale [code]` | Show localization status (bar + verified %), or send a language's localization file (10-min per-server cooldown for the file) | ✅ | ✅ | ✅ |
| `/loc-compare <code>` | Compare a reply across all languages with status emoji | ✅ | ✅ | ✅ |
| `/loc-suggest <lang> <code> <text>` | Suggest a localization; sent to the support chat | ✅ | ✅ | ✅ |
| `/setadmin <user_id>` | Grant a user Server Admin rights on this server; DMs the user | ❌ | ❌ | ✅ |
| `/remadmin <user_id>` | Revoke a user's Server Admin rights on this server | ❌ | ❌ | ✅ |
| `/setconsul <user_id>` | Appoint an appeal-server consul: the Purgatorium gate lets them in and hands them the `CONSULS` role(s) (granted immediately if they are already there) | ❌ | ❌ | ✅ |
| `/remconsul <user_id>` | Dismiss a consul and take the `CONSULS` role(s) away | ❌ | ❌ | ✅ |
| `/localizer-add <user>` · `/localizer-rem <user>` | Grant/revoke Localizer status — localization editing in the control panel (ping, ID or username); DMs the user | ❌ | ❌ | ✅ |
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

Before an automatic spam ban (and before network-ban enforcement), the bot tries to DM the user. For spam bans the DM is either the custom text set with `/dm` (where `{server}` is replaced with the server name and `{reason}` with the guarded channel's ban reason) or a localized default, with the `/setappeal` text appended on its own line if one is set. Network-ban enforcement sends its own localized DM that includes the remaining ban duration. DMs are best-effort — a closed DM never blocks the ban.

### Purgatorium appeals

When run alongside [Confederate](https://github.com/HIHRAIM/Confederate), servers **without** a `/setappeal` text get a shared appeal flow through a dedicated appeal server (“Purgatorium”, configured via `PURGATORIUM_GUILD_ID` / `PURGATORIUM_INVITE_URL` in `config.py`):

- **Invitation.** Every ban DM (spam guard, `/ban`, `/globalban`, network-ban enforcement) gains a localized line inviting the user to join Purgatorium for a guaranteed line of communication with the appeal bot. `/ban`, which otherwise sends no DM, sends one only for this invitation. Servers with their own `/setappeal` text keep their local appeal flow, and bans issued on Purgatorium itself never carry the invitation.
- **Gate on join.** When someone joins Purgatorium, Guard checks its database. A user with an active ban (local on any server, or network-wide) gets a DM — in the language of the server that banned them — telling them to send `/appeal` to the Confederate bot (`BRIDGE_BOT_ID`). When the database knows nothing, the gate falls back to the **live ban list** of every server registered via `/setup`: a ban a moderator issued by hand leaves no database row, and someone genuinely banned that way should still be able to appeal rather than be locked out for a day. Everyone else — including users whose bans were already lifted — is silently banned on Purgatorium for 1 day, so only users with something to appeal can stay. The gate ban is **never written to the database**: the unban moment is encoded in the audit-log ban reason (`[gate-unban:<timestamp>]`) and a scan of the server's ban list every 15 minutes lifts expired gate bans, so the gate keeps no stored trace of the visitor and survives restarts. Bot admins, Purgatorium server admins (`/setadmin`) and consuls (`/setconsul`) are exempt; consuls are additionally handed the `CONSULS` role(s) on join.
- **Ban summary for consuls.** For every new appeal, Confederate posts `<user_id> <thread_id>` into the `APPEAL_BANINFO_CHANNELS` sync channel. Guard then looks the user up on **all** of its servers regardless of network — the live ban list of each server (reason), the audit log where readable (moderator and date) and its own database (ban history, scheduled unban, network-ban record) — posts the summary into the appeal thread as an interpunct-separated (`·`) list of servers with everything known about each ban (split across several messages when it does not fit one), and acknowledges the request with a ✅ reaction. Purgatorium's own bans are left out — they are not appealable. Only messages from `BRIDGE_BOT_ID` or bot admins are honored.
- **Pardon channel.** Confederate manages the appeal threads and posts a bare Discord user ID into the `APPEAL_PARDON_CHANNELS` sync channel when the consuls grant an appeal. Guard then lifts **all** of that user's bans — network bans (removing the records and unbanning on the origin and enforcing servers) *and* local per-server bans, including the ban-history records — and acknowledges with a ✅ reaction, so a pardoned user never re-enters Purgatorium looking “still banned”. Only messages from `BRIDGE_BOT_ID` or bot admins are honored.
- **Per-network unban buttons.** The verdict buttons decide the appeal as a whole; a user banned in several networks often deserves to come back to only one of them, so the ban summary itself carries the finer tool. Guard attaches one button per network the user is banned in — plus **Unban in every network** when there is more than one — to the last message of the summary. Pressing one lifts that network's ban exactly as `/globalunban` would (record removed, user unbanned on the origin server and everywhere the ban was enforced) and posts a note in the thread naming the consul. The appeal itself is untouched: the thread stays open until the consuls close it with a verdict. The buttons are rebuilt from what is actually banned each time they are drawn, so a network whose ban is already gone loses its button instead of offering a second, empty unban. Usable by holders of a `CONSULS` role, consuls appointed with `/setconsul`, and bot admins.

### Tribunal

An automatic ban is a decision a regex made. `TRIBUNAL_CHANNELS` is where a human looks at it: every spam-guard ban is posted there as a short case file with two buttons, **Global ban** and **Ignore**.

The case lists, one fact per line: the banned member's nickname with their ID and username in brackets (markdown in a name is escaped, so a username like `spam_bot*99` cannot format the message), when the account was registered, when the ban was issued, when it is due to be lifted (or *permanent*), the server that issued it with its ID and network number in brackets, and the ban reason. Dates are Discord timestamps, so every reader sees them in their own timezone. The message is written in the language of the server the tribunal channel sits on — Purgatorium is deliberately not registered with `/setup`, so a channel there reads English.

Only bans issued on a server that belongs to a **network** are posted: **Global ban** acts on that network, and without one there would be nothing for the button to do.

**Global ban** promotes the local ban to a network ban with the same reason and a term of 10 years — the same “forever” `/ban infinity` means. The origin server's own ban is stretched to match, so its local timer cannot quietly unban someone the network still bans, and the ban is then applied on every server of the network that has `/setgbans enable` and where the user is currently a member. Afterwards the log channels of three groups of servers are told that the user is now banned network-wide: the server that issued the local ban, every server where the user is banned locally — **including servers outside this network**, because a local unban there while network bans are off would otherwise let them walk back in unnoticed — and every server the ban has just reached.

**Ignore** simply retires the buttons. Either way the case message keeps its text, loses its buttons, and gains a line naming the consul who decided it; a case nobody touches retires itself after **7 days**. Discord would keep the buttons clickable indefinitely, which is exactly the problem — a button that bans someone months after anyone remembers the case is not a decision, it is an accident.

Pressing is limited to holders of a `CONSULS` role, consuls appointed with `/setconsul`, and bot admins. The case is claimed in the database before any banning happens, so two consuls pressing at the same moment produce one ban and one “already decided” reply. The buttons survive restarts.

### Auto-role

`/autorole <role_id>` assigns a role to every member automatically: the role is added when a member joins, and running the command also assigns it to all current members right away. This is independent of verification — it applies to everyone.

### Activity-based verification

`/setverify <role_id> [channel_id]` enables automatic verification. Once configured, the bot records the first calendar date (UTC) on which each member sends a **genuine chat message**. When a member's messages span **more than one date** (they wrote on one day and returned later), the bot assigns the configured role and posts a notification — in the channel given to `/setverify`, or, if none was given, in the `/setup` log channel.

Discord's automatic **“member joined” notice** (and other system notices such as boosts and pinned-message notifications) are authored by the member but are **not** counted as chat activity. This matters: counting the join as a first activity date used to let someone who merely joined get verified by posting a single time the next day — including a spammer who joins and then blasts the channels a day later. Only real messages establish and advance a member's activity dates, so genuine two-day participation is required.

### Cross-server verification

The verified-user database is **shared across all servers**. When a member becomes verified (including verifications synced from Confederate), the role is granted right away on **every** server they belong to that has `/setverify` configured — and again whenever they later join or write on such a server. For a verification carried over from elsewhere, the notification states that it came from another server; the server where the verification actually happened, and any server where the origin is unknown, get the plain notification instead. Each server announces a given member only once; on startup the bot silently backfills the role for already-verified members who are present but hadn't received it yet, without announcements.

### Cross-bot verification sync

When run alongside [Confederate](https://github.com/HIHRAIM/Confederate), Guard watches the configured `VERIFIED`/`UNVERIFIED` channels. Confederate posts a Discord user ID to `VERIFIED` when that user consents to message forwarding, and to `UNVERIFIED` when they unverify themselves — though anyone allowed to write in those channels can post an ID there. On `VERIFIED` the ID may be followed by the ID of the server where the consent was given; Guard remembers it as the verification's origin, so that server gets the plain notification and only the others are told the verification happened elsewhere. A line without the second ID is still accepted — the origin is then unknown and every server gets the plain notification. Guard mirrors these into its shared verified database: an ID posted to `VERIFIED` is added and the verify role is propagated (with announcements) to every `/setverify` server the user is on; an ID posted to `UNVERIFIED` is removed from the database (already-granted roles are left in place). Each processed message is acknowledged with a ✅ reaction. Only Discord IDs are exchanged — never message content.

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

### The seven-day setup deadline

The bot is invited far more often than it is put to use, and a moderation bot sitting in a server that never registered is a standing grant of ban and kick rights paying for nothing. A server it has just been added to therefore has **seven days** to be registered with `/setup`; if that never happens, the bot leaves on its own and says so in `SERVICE_CHATS`, where it also announces every server it is added to.

Nothing is said in the server itself, neither on arrival nor on the way out. `/setup` is a bot-admin command, so the people who could act on a warning are the operators — and `SERVICE_CHATS` is exactly where they read.

The rule is deliberately narrow:

- **It never touches a server the bot was already in.** The deadline counts from the join, and the moment the rule came into force is recorded once, on the first start of the version that introduced it; everything that joined before that instant is out of reach for good.
- **It fires at most once per server.** The first daily sweep that finds a server registered settles it permanently.
- **It skips the deployment's own servers.** A server holding any channel named in `config.py` — `SERVICE_CHATS`, `BACKUP_CHATS`, `SUPPORT_CHATS`, `VERIFIED`/`UNVERIFIED`, the appeal and tribunal channels, Purgatorium — is never left, however it is configured.

The week is measured from Discord's own record of when the bot joined the server, so a restart or a missed event cannot shorten it. Leaving is not a deletion: a server that invites the bot back starts a fresh seven days.

### Service events and automatic backups

Startup and shutdown notices, the servers the bot is added to and the ones it leaves on the setup deadline, go to the `SERVICE_CHATS` channels. Encrypted database backups (authenticated BLAKE2 keystream + tag, standard library only) are posted to `BACKUP_CHATS` every 12 hours; `/backup` returns one on demand, and `python src/restore_backup.py <input.db.enc> <output.db>` decrypts it with the `BACKUP_KEY` environment variable. The destination channels only ever store ciphertext.

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
  - Guild ID, banned user ID, scheduled unban timestamp (empty for permanent spam-guard bans, which are recorded so the Purgatorium gate can recognize them). The 1-day Purgatorium gate bans themselves are **not** stored — their unban moment lives only in the Discord audit-log reason.
- **Auto-role**
  - Per-guild role ID assigned to new members.
- **Server admins**
  - Guild ID, user ID of users granted Server Admin rights via `/setadmin`.
- **Consuls**
  - User ID of appeal-server consuls appointed via `/setconsul`, with who appointed them and when.
- **Localizers**
  - Platform and user ID of users granted Localizer status via `/localizer-add`, their username at delegation time, who delegated it and when.
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
- **Tribunal cases**
  - One row per spam-guard ban posted for review: the case message and channel, the banning guild and its network, the banned user ID, the ban reason, the message language, when it was posted, and how it was decided (`globalban`, `ignored`, `expired`, or open).
- **Ban-summary posts**
  - The message and channel of each ban summary posted into an appeal thread, with the user ID it is about — kept only so the per-network unban buttons can be re-armed after a restart.
- **Localization suggestions**
  - `/loc-suggest` dialog codes: submitter platform/ID/username, target language, reply code, suggested text.
- **Setup deadline**
  - One row per server added after the rule came into force: the server ID, when the bot joined and — once the server has been registered with `/setup` — when that was noticed. No user is named.

### Retention periods

- **Active bans**: removed automatically after the ban expires and the unban is processed (checked every 60 seconds). Expired network bans are cleaned in the same loop.
- **Guild settings, guarded channels, custom DM, auto-role, server admins, consuls, localizers, banned links, verification/appeal/`setgbans` settings**: kept until manually changed or removed (guild-scoped entries are deleted when the bot leaves a server via `/force_leave`).
- **Localization-suggestion codes**: up to **1 year**, and removed immediately once answered with `/loc-reply`.
- **Tribunal cases**: kept after the case is decided (they are the record of who decided what); an untouched case has its buttons retired **7 days** after it was posted, checked every 15 minutes.
- **Ban-summary posts**: the bookkeeping row is dropped **90 days** after the summary was posted — the summary itself stays in the appeal thread, it simply stops carrying live buttons.
- **Stored user IDs (verified users, verify grants, user activity, ban history)**: automatically deleted 10 years after they were added. The cleanup runs on startup and once every 24 hours.
- **Setup-deadline rows**: kept while the week runs, and afterwards as the note that the server was registered. Removed when the bot leaves the server, so that a re-invitation starts a fresh seven days.

### Data usage boundaries

- The bot uses stored data only to operate spam protection, bans, log delivery, auto-role assignment, and cross-server verification.
- It does not implement analytics or tracking pipelines in this repository.
- Database backups are encrypted (authenticated BLAKE2 keystream + tag, standard library only — no third-party crypto dependency) before leaving the runtime; the destination only ever stores ciphertext. The decryption key lives in the `BACKUP_KEY` environment variable and must be kept out of the repository.
