import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

ADMINS = [int(x) for x in os.environ.get("ADMINS", "").split(",") if x.strip()]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0"))

PORT = int(os.environ.get("PORT", "8080"))
