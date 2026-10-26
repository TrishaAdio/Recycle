"""
Configuration for the group anti-spam userbot.

Edit the values below (or set them as environment variables) before running.
Get API_ID / API_HASH from https://my.telegram.org -> API development tools
"""

import os

_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_env_file(path: str = _ENV_FILE) -> None:
    """Load KEY=VALUE lines from .env without needing python-dotenv.

    Real environment variables always win over the file.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_env_file()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


# ----------------------------------------------------------------------------
# Telegram credentials
# ----------------------------------------------------------------------------
API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "antispam")

# ----------------------------------------------------------------------------
# Spam rules
# ----------------------------------------------------------------------------

# Videos / GIFs / round videos SHORTER than this many seconds are spam.
# 4.9s -> spam, 5s -> allowed, 9s -> allowed (stays).
MIN_VIDEO_DURATION = float(os.getenv("MIN_VIDEO_DURATION", "5"))

# Treat every GIF / animation as spam, regardless of its duration.
# Leave False to judge GIFs by MIN_VIDEO_DURATION like any other video.
BAN_ALL_GIFS = _bool("BAN_ALL_GIFS", False)

# Any link posted by a non-whitelisted member counts as spam.
# If False, only links matching SPAM_LINK_PATTERNS below count.
BAN_ANY_LINK = _bool("BAN_ANY_LINK", True)

# Any PHOTO posted by a non-whitelisted member counts as spam (caption or not).
BAN_PHOTOS = _bool("BAN_PHOTOS", True)

# Extra link patterns that are ALWAYS spam (checked even when BAN_ANY_LINK=False)
SPAM_LINK_PATTERNS = [
    r"t\.me/joinchat/",
    r"t\.me/\+",
    r"telegram\.me/joinchat/",
    r"bit\.ly/",
    r"tinyurl\.com/",
    r"cutt\.ly/",
    r"is\.gd/",
    r"shorturl\.",
    r"\.onion\b",
    r"\b(?:free|earn|crypto|binary|invest|casino|betting|xxx|porn|18\+)\b.{0,40}https?://",
]

# Message flood: this many messages from one user inside FLOOD_WINDOW seconds.
# Set FLOOD_COUNT = 0 to disable flood detection.
FLOOD_COUNT = _int("FLOOD_COUNT", 8)
FLOOD_WINDOW = _int("FLOOD_WINDOW", 10)

# ----------------------------------------------------------------------------
# Scan behaviour
# ----------------------------------------------------------------------------

# How many recent messages per group to scan on `.start`.
SCAN_LIMIT = _int("SCAN_LIMIT", 2000)

# Only scan messages newer than this many days. 0 = no age limit.
SCAN_DAYS = _int("SCAN_DAYS", 30)

# Never touch these users. Group admins and you are always protected too.
# Raw numeric ids work even for DELETED accounts (they can't be looked up by
# name any more, so ids are the only reliable way to whitelist them).
WHITELIST = [
    6522530722,
    7450005478,
    6687627877,
]

# Never touch these groups (ids or @usernames).
GROUP_BLACKLIST = [
    # -1001234567890,
]

# ----------------------------------------------------------------------------
# `.close all` behaviour
# ----------------------------------------------------------------------------

# Delete messages that are nothing but a photo (no caption) during `.close all`.
DELETE_PHOTO_ONLY = _bool("DELETE_PHOTO_ONLY", True)

# How many recent messages to check for photo-only cleanup.
PHOTO_CLEAN_LIMIT = _int("PHOTO_CLEAN_LIMIT", 2000)

# ----------------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------------

# Seconds to sleep between heavy API calls. 0 = full speed.
# FloodWait is still caught and retried, so 0 is safe-ish; raise it to 0.3 if
# Telegram starts throttling you on very large group lists.
API_DELAY = float(os.getenv("API_DELAY", "0"))

# How many groups to process at the same time. Higher = faster, but more
# chance of hitting FloodWait. 1 = the old sequential behaviour.
CONCURRENCY = _int("CONCURRENCY", 6)

# How many delete/ban calls to fire at once inside one group.
BATCH_CONCURRENCY = _int("BATCH_CONCURRENCY", 4)

# Write every action to this file as JSON lines (used by `.undo`).
ACTION_LOG = os.getenv("ACTION_LOG", "actions.jsonl")
