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
