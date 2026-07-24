import os

from hikari_bot.core.config import settings

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
DECK_DIR = os.path.join(os.path.dirname(ROOT_DIR), "deck")
PDF_DIR = os.path.join(os.path.dirname(ROOT_DIR), "pdf")
DATA_DIR = os.path.join(os.path.dirname(ROOT_DIR), "data")
RESOURCES_DIR = os.path.join(os.path.dirname(ROOT_DIR), "resources")

PUBLIC_DECK_URL = "https://ygo.xyk.one/deck"

# Backward-compatible alias while plugins migrate to the settings object.
ADMIN = settings.superusers
