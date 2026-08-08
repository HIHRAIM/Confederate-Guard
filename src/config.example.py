import os

from env_loader import load_env
load_env()

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]

ADMINS = {ADMINISTRATOR_ID, ADMINISTRATOR_ID}

SERVICE_CHATS = {
    "discord": {
        CHAT_ID,
        CHAT_ID,
    },
}

BACKUP_CHATS = {
    "discord": {
        CHAT_ID,
        CHAT_ID,
    },
}

SUPPORT_CHATS = {
    "discord": {
        CHAT_ID,
    },
}

VERIFIED = {
    CHAT_ID,
}

UNVERIFIED = {
    CHAT_ID,
}

# PURGATORIUM — the shared appeal server (see README: Purgatorium appeals).
PURGATORIUM_GUILD_ID = GUILD_ID
PURGATORIUM_INVITE_URL = "https://discord.gg/INVITE_CODE"
# bridge_bot user id, mentioned in the appeal hint DM.
BRIDGE_BOT_ID = BOT_USER_ID
# Channels where bridge_bot posts user IDs whose bans the consuls lifted.
APPEAL_PARDON_CHANNELS = {
    "discord": {
        CHAT_ID,
    },
}
# Channels where bridge_bot posts "<user_id> <thread_id>" for every new appeal;
# Confederate Guard answers in the thread with the user's ban summary.
APPEAL_BANINFO_CHANNELS = {
    "discord": {
        CHAT_ID,
    },
}
# Role IDs on PURGATORIUM handed to consuls (appointed via /setconsul) on join.
CONSULS = {
    ROLE_ID,
}
# TRIBUNAL_CHANNELS — where every spam-guard ban is put up for review with
# "Global ban" / "Ignore" buttons (see README: Tribunal). Only bans issued on a
# server that belongs to a network are posted, since the buttons act on that
# network. Leave the set empty to turn the mechanic off.
TRIBUNAL_CHANNELS = {
    "discord": {
        CHAT_ID,
    },
}
