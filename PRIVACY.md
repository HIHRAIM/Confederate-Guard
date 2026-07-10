# Confederate Guard Privacy Policy

_Last updated: 2026-07-06_

Confederate Guard is a self-hosted, open-source Discord moderation bot that protects servers from spam and coordinates bans across a group of allied servers. This document describes what data the software processes, why, for how long, and what choices users have.

> **Who is responsible for your data.** Confederate Guard is software, not a service: anyone can run their own instance. The person or team operating a given instance (the **operator**) controls that instance's database, configuration and backups, and is the data controller for it. This document describes what the software itself does; a specific operator may add their own infrastructure (hosting, logging, backups) around it.

## What the bot processes

To moderate a server, the bot receives messages through the Discord API and inspects them **in memory** to decide whether to act: in guarded channels it deletes messages and bans authors who post links, invite URLs or attachments; elsewhere it deletes messages that contain a link from the operator's banned-link list. It also reads the **date** of each member's messages to run activity-based verification (granting a role to members who have chatted on more than one calendar day). Bans, unbans, role grants, deletions, DMs and log messages are all carried out through Discord's own API.

Message **content is not stored** — it is only examined transiently to make the above decisions. The only free text the bot keeps is operator/moderator configuration (ban reasons, the ban DM and appeal text, banned links) and localization suggestions that users explicitly submit.

## What the bot stores

All data lives in a local SQLite database (`guard.db`) on the operator's machine.

| Data | Contents | Retention |
|---|---|---|
| Message content | **Not stored** (inspected in memory only), except localization suggestions below | — |
| Guild settings | Guild ID, language, log channel ID, network ID | Until changed, or until the bot leaves the server |
| Guarded channels | Channel ID, guild ID, ban duration, ban reason | Until changed, or until the bot leaves the server |
| Ban DM & appeal text | Per-guild custom ban DM (`/dm`) and appeal text (`/setappeal`) | Until changed, or until the bot leaves the server |
| Auto-role & verification settings | Role IDs and optional announcement channel set via `/autorole` and `/setverify` | Until changed, or until the bot leaves the server |
| Server admins | Guild ID and user ID of users granted Server Admin via `/setadmin` | Until removed, or until the bot leaves the server |
| Banned links | Globally banned URLs and Discord invite codes | Until removed with `/unbanlink` |
| Active bans | Guild ID, banned user ID, scheduled unban timestamp | Until the ban expires and is lifted (checked every 60 s) |
| Verified users (shared) | User ID, originating guild ID, verification timestamp | **10 years** after being added |
| Verify grants | Guild ID and user ID recording the role was already granted/announced there | **10 years** (or until the bot leaves the server) |
| User activity | Guild ID, user ID, and the **first calendar date** a genuine (non-system) message was seen — used to detect activity spanning more than one date | **10 years** after being added |
| Network bans | Per-network user bans (reason, origin guild, issued/expiry timestamps), per-guild `/setgbans` toggle, and the set of bans applied by enforcement | Until lifted (`/globalunban`) or expired |
| Ban history | Guild ID, user ID and timestamp of every ban the bot issues (powers prior-ban alerts) | **10 years** after being added |
| Localization suggestions | Submitter's platform, ID and username, target language, reply code, suggested text | Until answered with `/loc-reply`, at most **1 year** |

Stored user IDs (verified users, verify grants, user activity, ban history) are swept automatically 10 years after they were added; the cleanup runs on startup and once every 24 hours.

## Where data goes

- **Discord.** Bans, unbans, role grants, message deletions, ban DMs, and log-channel and verification-announcement messages are all performed through the official Discord API and are subject to Discord's own privacy policy.
- **Cross-bot verification sync (optional).** When the operator runs Guard together with [Confederate](https://github.com/HIHRAIM/Confederate), Guard reads **Discord user IDs** that Confederate posts to the configured `VERIFIED`/`UNVERIFIED` channels and adds or removes them from its shared verified database. Only bare Discord IDs are exchanged — never message content.
- **Backups.** Every 12 hours (and on `/backup`) the database is sent to the operator-configured backup channels — **always encrypted** (authenticated BLAKE2 keystream + tag; the key never leaves the `BACKUP_KEY` environment variable on the operator's machine). The destination channels only ever store ciphertext.
- **Service notices.** Startup and shutdown events go to operator-configured service channels. They contain no user data.

The software contains **no analytics, tracking, advertising or data-sale pipelines**, and sends nothing to the developers of Confederate Guard.

## What other users can see

- **Log channel.** Server admins with access to a server's log channel see moderation events: bans the bot issues (the target's mention/ID, reason, duration and the issuing admin), network-ban enforcement, and prior-ban alerts that name the server where an earlier ban was issued.
- **Verification announcements.** When a member is verified, the bot posts a message naming them in the `/setverify` announcement channel (or the log channel), and the verify **role** itself is visible to everyone on the server as normal Discord role state.
- **Ban DMs.** A user being banned for spam receives a DM (the operator's custom text or a localized default, plus any appeal text); network-ban enforcement sends its own DM with the remaining duration.

## Your choices

- **Activity data is minimal.** For verification the bot stores only the **dates** you posted on a server, never the content of those messages, and Discord's automatic “member joined” notice does not count. Verification grants a role; it does not expose your messages.
- **Appeals.** If you were banned, the ban DM (where the operator configured `/setappeal`) explains how to appeal; appeals and questions are handled by the operator of that server.
- **Questions and erasure requests** (e.g. removal of verification records, activity dates, or ban-history entries) should go to the operator of your instance — they hold the database. For questions about the software itself, open an issue in the repository.

## Security

The database is a local file readable only by the operator's environment; backups leave the machine only encrypted; the bot token and the backup key are read from environment variables / a local `.env` file and are never written to the database or sent anywhere.

## Age requirements

Confederate Guard runs on top of Discord and inherits its minimum-age requirements; it performs no age verification of its own and is not directed at children.

## Changes

This document is versioned together with the source code. Material changes to what the software collects or shares will be reflected here and in the release notes.
