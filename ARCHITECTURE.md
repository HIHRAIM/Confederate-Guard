# Architecture

This is the map of the code: what the moving parts are called, how a ban travels, and where to find the code behind each feature. The commands themselves are documented in [README.md](README.md); this file is about structure.

## The one idea

Confederate Guard watches *servers* and groups them into *networks*. A server becomes known to the bot when a Bot Admin runs `/setup` there, which writes the `guilds` row holding its language, its log channel and — optionally — a **network id**: a plain integer that several servers may share. Everything the bot does across server boundaries is scoped by that integer.

A **guarded channel** (`/guard`) is one where members are not expected to write. Every message posted there is deleted; a message that carries a link or an attachment additionally bans its author. That ban is local to the server, and it is a decision a regular expression made — which is why it is also posted to the **tribunal** for a human to look at.

A **global ban** is a ban recorded against a network rather than a server. It is issued by `/globalban` or promoted from a local ban by a consul pressing **Global ban** in the tribunal. It reaches the other servers of the network one at a time and only where the server opted in with `/setgbans enable`. "Global" therefore never means "everywhere the bot is" — it means "everywhere in this one network that agreed to enforce".

Unlike its two sibling bots, Guard has no Telegram half. It is a `discord.py` client and nothing else, running in one process on one asyncio loop with one SQLite database (`src/guard.db`, WAL mode, opened by relative path — the process must run with cwd = `src/`, which is what `main.py` and the control panel both do).

## Networks

The network id comes from the third argument of `/setup` and lives in `guilds.network`. It is not allocated by the bot, has no table of its own and no name — the operator picks a number and gives the same number to every server that should share a ban list. A server set up without one is a perfectly ordinary Guard server: it filters spam, bans and logs, but `/globalban` refuses to run there (`globalban_no_network`), the tribunal never posts its bans, and prior-ban alerts never fire.

Three things are keyed on the network rather than on the server:

* `global_bans` — one row per `(network, user_id)`: reason, origin server, issue and expiry timestamps.
* `gban_enforcements` — one row per `(guild_id, user_id)` written whenever the bot actually applied a network ban on that server. This is the ledger `/setgbans disable` reads: turning enforcement off lifts exactly the bans this mechanism applied, and leaves `/ban` and `/globalban` bans alone.
* the prior-ban alert, which joins `ban_history` against `guilds.network`.

Enforcement is opt-in per server (`gban_settings.enabled`, `/setgbans`) and deliberately separate from network membership: a server can join a network to contribute bans and read alerts without handing the network authority over its own member list.

## The path of a spam message

1. `discord_bot/events.py: on_message` fires. The sync channels are checked first and each returns immediately: `VERIFIED`/`UNVERIFIED` go to `verification.py: handle_verification_sync`, `APPEAL_PARDON_CHANNELS` to `purgatorium.py: handle_appeal_pardon`, `APPEAL_BANINFO_CHANNELS` to `purgatorium.py: handle_appeal_baninfo`. Bots, webhooks and DMs are dropped next.
2. `verification.py: handle_verification` records the activity date and may verify the author (see Verification).
3. If the channel is **not** guarded, the message survives unless it carries a link from the banned-link list and the author is not an admin — then it is deleted, and nothing else happens. No ban is ever issued for a banned link.
4. If the channel **is** guarded, the message is deleted unconditionally. `utils.py: message_has_spam` then decides: no URL and no attachment means the deletion was the whole punishment.
5. Spam means a ban. The user is DM'd first — the `/dm` custom text (with `{server}` and `{reason}` substituted) or the localized default, plus the `/setappeal` text, or, when the server has no appeal text of its own, the Purgatorium invitation line (`purgatorium.py: _purgatorium_invite_line`).
6. The ban is applied, recorded in `ban_history` and `active_bans` (with the guard's duration), and the author's last 24 hours of messages are purged server-wide (`bans.py: _purge_recent_messages`).
7. `tribunal.py: post_tribunal_case` puts the case up for review — but only if the server has a network.
8. The `/setup` log channel is told.

Steps 5–6 are the same sequence `/ban` and `/globalban` run, which is why the DM helper, the purge and the reason-suffix formatting all live in `discord_bot/bans.py` rather than next to any one command.

## The path of a global ban

1. `/globalban` (`commands/bans.py`) bans locally, writes `active_bans` + `ban_history`, and writes the `global_bans` row for the server's network. Or: a consul presses **Global ban** in the tribunal and `tribunal.py: _tribunal_apply_global_ban` writes the same row with a 10-year term.
2. Both then walk `db.get_network_guilds(network)`. A server is skipped unless `db.is_gbans_enabled(gid)` and the user is currently a member of it — Guard bans people who are *there*, it does not pre-ban an absentee across the network.
3. For each remaining server, `bans.py: enforce_global_ban` DMs the user, bans with a localized reason suffix carrying the bot name and the end date, writes `active_bans` (so the local unban is scheduled) and `gban_enforcements` (so `/setgbans disable` can revert it), and logs to that server's log channel.
4. A user who joins later is caught by `events.py: on_member_join`, which runs the same `enforce_global_ban`.
5. `/setgbans enable` catches the ones already present: it walks the current member list against the network's active global bans and enforces each.
6. Lifting happens in one place, `bans.py: _execute_global_unban`: delete the `global_bans` row, then unban on the origin server plus every server carrying a `gban_enforcements` row, clearing `active_bans`, `gban_enforcements` and `ban_history` for each. `/globalunban`, the per-network pardon buttons and the consul pardon channel all call it.

Expiry is separate and cheaper: `bans.py: unban_loop` lifts `active_bans` whose `unban_at` has passed and drops expired `global_bans` rows, without touching Discord for servers that never enforced.

## Roles

* **Bot Admin** — hard-coded in `config.py: ADMINS`. Everything: network bans, the banned-link list, admin and consul appointments, `/backup`, `/list_chats`, `/force_leave`. Holds Server Admin rights on every server implicitly (`utils.py: is_admin`).
* **Server Admin** — `/setadmin`, stored in `guild_admins`, scoped to one server. Runs `/setup`, `/guard`, `/ban`, `/unban`, `/dm`, `/autorole`, `/setverify`, `/setgbans`, `/setappeal`, `/links`.
* **Consul** — `/setconsul`, stored in `consuls`. Not a moderation role on ordinary servers: it is admission to Purgatorium plus the right to press the tribunal and pardon buttons. `purgatorium.py: _is_consul_or_admin` also accepts anyone holding one of the `config.CONSULS` roles where the interaction happened, so the roles and the table are two independent ways in.
* **Localizer** — `/localizer-add`, stored in `localizers`. Grants nothing inside Discord; it exists for the external control panel, which lets the holder edit this bot's localization files.

## Background loops

From `main.py`, started in `main()`:

| Loop                  | Period      | Job |
|-----------------------|-------------|-----|
| `backup_loop`         | every 12 h  | encrypted `guard.db` snapshot to `BACKUP_CHATS`; sleeps first, so a crash-looping bot cannot spam them |
| `retention_loop`      | every 24 h  | `cleanup_expired_user_data` + `cleanup_old_loc_suggestions` |
| `setup_deadline_loop` | every 24 h  | leaves servers added more than 7 days ago that no Bot Admin ever `/setup` |

From `discord_bot/client.py`, started in `GuardBot.setup_hook`:

| Loop                     | Period      | Job |
|--------------------------|-------------|-----|
| `unban_loop`             | every 60 s  | lifts `active_bans` whose time is up, drops expired `global_bans`; every `GATE_SCAN_INTERVAL` = 900 s also runs `_lift_expired_gate_bans`, `_retire_stale_tribunal_cases` and `cleanup_old_baninfo_posts` |
| `status_loop`            | every 60 s  | rotates the presence through the six languages with the live `/setup` server count |
| `backfill_verified_roles`| once at startup | silently grants the verify role to already-verified members who never got it |

## Verification

`/setverify <role_id> [channel_id]` turns on activity-based verification for one server. `verification.py: handle_verification` records the first calendar date (UTC) a member posts a genuine message — `message.is_system()` is excluded, so Discord's own "member joined" notice does not count as a first date — and grants the role the first time a member writes on a *second* date.

The verified set itself (`verified_users`) is **not** per server. Once a user is in it, `verification.py: _grant_verify` hands them the role on every `/setverify` server they belong to. Three paths lead there, and all three end in `_grant_verify`:

* writing on a server (`handle_verification`),
* joining one (`events.py: on_member_join`),
* the fan-out `verification.py: propagate_verified_roles`, which walks every guild at once and is what a freshly synced verification triggers.

`verify_grants` is the per-server "already done" marker. It is written even for silent grants, which is what makes `verification.py: backfill_verified_roles` — the one-shot startup sweep that hands the role to already-verified members who never received it — idempotent and silent across restarts.

The announcement wording turns on `origin_guild_id`: the server where the verification actually happened gets the plain text, everyone else gets the cross-server variant. An unknown origin means plain text everywhere, because on the server where it really happened "verified on another server" would simply be false.

### Cross-bot sync with bridge_bot

Guard and Confederate (`bridge_bot`) exchange verification state through two Discord channels and nothing else — no API, no shared database, no imports. `bridge_bot` writes a bare user id into a `config.VERIFIED` channel when the user consents to message forwarding, and into `config.UNVERIFIED` when they revoke it. On the `VERIFIED` line a second number may follow: the id of the server the consent was given on.

`verification.py: handle_verification_sync` reads those channels, mirrors the change into `verified_users`, fans the role out through `propagate_verified_roles`, and reacts ✅ to mark the line consumed. Anyone who can write in those channels is honored — the channels are the trust boundary, not the sender — and only user ids ever cross, never message content.

## Purgatorium

Purgatorium is the shared appeal server (`config.PURGATORIUM_GUILD_ID`). The appeal system is split between the two bots along a clean line: **bridge_bot owns the conversation, Guard owns the facts and the bans.** bridge_bot runs `/appeal`, opens the thread, bridges it to the appellant's DM, anonymizes the consuls and posts the verdict buttons. Guard never reads that thread.

The seam is four channels and one gate:

* **The invitation.** Every ban DM Guard sends gains a localized line pointing at Purgatorium — unless the server set its own `/setappeal` text, or the ban happened on Purgatorium itself. `purgatorium.py: _purgatorium_invite_line`.
* **The gate.** `purgatorium.py: handle_purgatorium_join` runs on join. Bot admins, Purgatorium's own server admins and consuls are let in (consuls are handed the `config.CONSULS` roles). Anyone with an active ban — local on any server, or global in any network — is DM'd in the language of the server that banned them and told to write `/appeal` to bridge_bot. When the database knows nothing, `bans.py: _find_registered_guild_ban` falls back to the live ban lists of every `/setup` server, because a ban a moderator issued by hand leaves no row and its victim still deserves an appeal. Everybody else is banned for one day. That gate ban is deliberately **not** stored: its expiry is encoded in the audit-log reason as `[gate-unban:<unix ts>]` (`bans.py: GATE_BAN_MARKER`) and `bans.py: _lift_expired_gate_bans` scans the ban list for expired markers every `GATE_SCAN_INTERVAL` = 900 s. The gate keeps no record of who visited.
* **The ban summary.** bridge_bot posts `<user_id> <thread_id>` into a `config.APPEAL_BANINFO_CHANNELS` channel; `purgatorium.py: handle_appeal_baninfo` answers *into the thread*. `_collect_user_ban_info` merges three sources per server — the live ban entry (reason), the audit log where readable (moderator, date), and the database (`active_bans`, `ban_history`, `global_bans`) — across every server the bot is on regardless of network. Purgatorium's own bans are excluded. The summary is split across messages at 1900 characters, and `baninfo_posts` remembers the last one so its buttons can be re-armed after a restart.
* **The pardon channel.** When the consuls grant an appeal, bridge_bot posts a bare user id into `config.APPEAL_PARDON_CHANNELS` and `purgatorium.py: handle_appeal_pardon` lifts everything: every network ban through `_execute_global_unban`, then every local ban on every server, then the whole `ban_history` for that user — otherwise a pardoned user would re-enter Purgatorium looking still-banned, or trip a prior-ban alert on their way back in. Only `config.BRIDGE_BOT_ID` and bot admins are honored here, unlike the verification channels.
* **The per-network buttons.** The consuls' verdict decides the appeal as a whole; `NetworkPardonView` under the ban summary decides one network at a time, which is the finer tool a user banned in several networks needs. Pressing one lifts that network's ban exactly as `/globalunban` would and posts a note naming the consul — the appeal itself stays open. The view is rebuilt from what is banned *now* every time it is drawn, so a ban already lifted loses its button rather than offering an empty second unban.

Both button families are **persistent views**: their `custom_id`s carry the user (and network) they act on, so `client.py: GuardBot._restore_persistent_views` re-registers them against their stored message ids at startup. `client.py` therefore imports `TribunalView` from `tribunal.py` and `NetworkPardonView` from `purgatorium.py`, never the other way round. The id formats are `gpardon:<uid>:<network|all>` and `tribunal:<action>:<uid>`; changing either by one character silently breaks every button already hanging in Discord.

## Tribunal

`config.TRIBUNAL_CHANNELS` is where a human reviews what the spam regex decided. Every automatic guard ban on a **networked** server is posted there as a case file with two buttons; a ban on a server without a network is not posted at all, because **Global ban** would have no network to act on. An empty set turns the mechanic off.

The case text is one fact per line, timestamps rendered as Discord markup so each reader sees their own timezone, and names escaped through `tribunal.py: _md` so a username like `spam_bot*99` cannot reformat the message. It is written in the language of the server the tribunal channel sits on — Purgatorium is deliberately not `/setup`-registered, so a channel there reads English, which is what the consuls share.

**Global ban** promotes the local ban to a network ban with the same reason and the 10-year term `/ban infinity` means, stretches the origin server's own ban to match so its local timer cannot quietly release someone the network still bans, enforces across the network, then announces to three groups of log channels: the origin server, every server where the user is banned locally (**including servers outside this network** — a local unban there while enforcement is off would otherwise let them walk back in unnoticed), and every server the ban has just reached.

**Ignore** only retires the buttons. Either way the case keeps its text, loses its buttons and gains a line naming the consul. The case is claimed in the database first (`db.resolve_tribunal_case` returns False if it was already claimed), so two consuls pressing at the same instant produce one ban and one "already decided" reply.

A case nobody touches expires after `TRIBUNAL_BUTTON_TTL` = 7 days, swept by `_retire_stale_tribunal_cases` on the 15-minute tick. Discord would keep the components clickable forever, which is exactly the problem: a button that bans someone months after anyone remembers the case is not a decision.

## The setup deadline

A server the bot was added to has seven days (`setup_deadline.py: SETUP_GRACE_SECONDS`) to be registered with `/setup`; otherwise the daily sweep leaves it and reports the departure to `SERVICE_CHATS`. Nothing is ever said in the server itself — `/setup` is a Bot Admin command, so the only people who could act on a warning are the operators, and the service chats are where they read.

Three guards keep the rule narrow, each answering a different failure:

* `bot_settings.setup_rule_since` (`db/onboarding.py: rule_since`), planted on the first start of the version that introduced the rule, is the grandfathering line. Every server the bot was already in joined before it and is never examined.
* `setup_deadlines.settled_at` makes the check one-shot: the first sweep that finds a server registered settles it, so a `guilds` row deleted years later cannot put the bot out of a door it was long since welcomed through.
* A server holding any channel named in `config.py` — service, backup, support, the verification channels, the appeal and tribunal channels, Purgatorium itself — is the operator's own infrastructure and is exempt outright (`setup_deadline.py: is_protected_guild`).

The clock is `Guild.me.joined_at`, Discord's own record, the same source the Purgatorium gate reads for members: a restart, a lost event or a cold cache cannot make the bot believe it has been somewhere longer than it has. `setup_deadlines.joined_at` is a record, not the clock. Leaving is not a deletion — `on_guild_remove` drops the deadline row, so a re-invitation is a fresh seven days, while the server's moderation data (log channel, bans, network) deliberately survives a kick-and-reinvite.

## Retention

`db/onboarding.py: cleanup_expired_user_data` runs daily and covers the tables that hold user ids for their own sake rather than for an ongoing action: `verified_users`, `verify_grants`, `user_activity` and `ban_history`, all at **10 years** from insertion (`USER_ID_RETENTION_SECONDS`). Operational state — `active_bans`, `guild_admins`, `consuls` — is untouched, because deleting it would silently undo a moderator's decision.

Three narrower sweeps: `cleanup_old_loc_suggestions` (1 year, daily), `cleanup_old_baninfo_posts` (90 days, on the 15-minute tick — the summaries stay in the thread, only the button bookkeeping is dropped), and `cleanup_expired_global_bans` (on expiry, every minute). `/force_leave` and nothing else calls `db.remove_guild_data`, which erases one server's rows across thirteen tables at once.

## Feature → file

Every mechanic listed in README.md, with its address. `discord_bot/` and `db/` below are packages, one file per domain.

| Feature (README section) | Where the code lives |
|---|---|
| Spam guard: detection | `utils.py: message_has_spam`, `URL_RE` |
| Spam guard: delete/ban/DM/log flow | `discord_bot/events.py: on_message` |
| Spam guard: enabling a channel (`/guard`) | `discord_bot/commands/settings.py`, storage in `db/guilds.py` |
| Manual and temporary bans (`/ban`, `/unban`) | `discord_bot/commands/bans.py` |
| Message purge after a ban | `discord_bot/bans.py: _purge_recent_messages` |
| Scheduled unban | `discord_bot/bans.py: unban_loop`, storage in `db/bans.py` |
| Duration parsing and formatting | `utils.py: parse_duration`, `parse_global_duration`, `format_duration` |
| Ban DMs and appeals (`/dm`, `/setappeal`) | `discord_bot/commands/settings.py`, storage in `db/settings.py` |
| Purgatorium: invitation line | `discord_bot/purgatorium.py: _purgatorium_invite_line` |
| Purgatorium: gate on join | `discord_bot/purgatorium.py: handle_purgatorium_join`, fallback in `discord_bot/bans.py: _find_registered_guild_ban` |
| Purgatorium: gate-ban expiry | `discord_bot/bans.py: GATE_BAN_MARKER`, `_lift_expired_gate_bans` |
| Purgatorium: ban summary for consuls | `discord_bot/purgatorium.py: handle_appeal_baninfo`, `_collect_user_ban_info` |
| Purgatorium: pardon channel | `discord_bot/purgatorium.py: handle_appeal_pardon` |
| Purgatorium: per-network unban buttons | `discord_bot/purgatorium.py: NetworkPardonView`, storage in `db/tribunal.py` (`baninfo_posts`) |
| Purgatorium: consul appointment (`/setconsul`, `/remconsul`) | `discord_bot/commands/admins.py`, storage in `db/admins.py` |
| Tribunal: case posting and text | `discord_bot/tribunal.py: post_tribunal_case`, `_tribunal_text` |
| Tribunal: buttons, verdicts, expiry | `discord_bot/tribunal.py`, storage in `db/tribunal.py` |
| Tribunal: promotion to a network ban | `discord_bot/tribunal.py: _tribunal_apply_global_ban`, `_tribunal_broadcast` |
| Auto-role (`/autorole`) | `discord_bot/commands/settings.py`, join handling in `discord_bot/events.py: on_member_join`, storage in `db/guilds.py` |
| Activity-based verification | `discord_bot/verification.py: handle_verification`, storage in `db/users.py` |
| Cross-server verification | `discord_bot/verification.py: propagate_verified_roles`, `_grant_verify`, `backfill_verified_roles` |
| Cross-bot verification sync | `discord_bot/verification.py: handle_verification_sync`, channels in `config.py: VERIFIED`/`UNVERIFIED` |
| Verification setup (`/setverify`) | `discord_bot/commands/settings.py` |
| Network bans (`/globalban`, `/globalunban`) | `discord_bot/commands/bans.py`, delivery in `discord_bot/bans.py: enforce_global_ban`, lifting in `_execute_global_unban` |
| Network-ban enforcement switch (`/setgbans`) | `discord_bot/commands/settings.py`, the toggle in `db/settings.py` (`gban_settings`), the ledger of applied bans in `db/bans.py` (`gban_enforcements`) |
| Prior-ban alerts | `discord_bot/bans.py: notify_prior_network_ban`, query in `db/bans.py: get_network_ban_history` |
| Banned-link list (`/banlink`, `/unbanlink`, `/links`) | `discord_bot/commands/links.py`, matching in `utils.py: classify_banned_link`, `message_has_banned_link`, storage in `db/settings.py` |
| Localization runtime | `utils.py: _load_i18n`, `localized`, `locale_stats`, files in `src/i18n/` |
| Localization commands and suggestions | `discord_bot/commands/locale.py`, storage in `db/settings.py` |
| Localizer status (`/localizer-add`, `/localizer-rem`) | `discord_bot/commands/admins.py`, storage in `db/admins.py` |
| Presence | `discord_bot/client.py: status_loop`, text in `utils.py: get_next_status_text` |
| Seven-day setup deadline | `setup_deadline.py`, storage in `db/onboarding.py`, loop in `main.py: setup_deadline_loop`, join/leave in `discord_bot/events.py` |
| Service events | `utils.py: send_service_event`, channels in `config.py: SERVICE_CHATS` |
| Automatic backups and `/backup` | `backup_crypto.py`, `main.py: backup_loop`, `/backup` in `discord_bot/commands/admins.py`, decryption in `restore_backup.py` |
| Server registration (`/setup`), `/list_chats`, `/force_leave` | `discord_bot/commands/guilds.py`, storage in `db/guilds.py` |
| `/help` | `discord_bot/commands/user.py` |
| Server admin roles (`/setadmin`, `/remadmin`) | `discord_bot/commands/admins.py`, storage in `db/admins.py` |
| Data retention | `db/onboarding.py: cleanup_expired_user_data`, loop in `main.py: retention_loop` |
| DB connection, schema, migrations | `db/__init__.py`, `db/schema.py` |
| Secrets loading | `env_loader.py`, read in `config.py` |

## Neighbours

The parent folder holds sibling projects this repo cooperates with but never imports. **Confederate** in `bridge_bot/` runs the other half of the appeal system and the verification sync; the two talk exclusively through Discord channels named in both configs (`VERIFIED`, `UNVERIFIED`, `APPEAL_PARDON_CHANNELS`, `APPEAL_BANINFO_CHANNELS`) and recognize each other by `BRIDGE_BOT_ID` / `GUARD_BOT_ID`. `panel` is the web control panel: it reads `src/config.py`, `src/.env`, `src/i18n/*.json` and `guard.db` directly from disk and launches `python main.py` with cwd `src/`. `clean_code.py` is a comment stripper run over the whole tree from time to time — it deletes every `#` comment and leaves docstrings alone, so anything worth keeping has to be a docstring, or, inside SQL, a `--` comment within the string literal.
