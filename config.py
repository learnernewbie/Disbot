import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
INITIAL_EXTENSIONS = [
    'cogs.events',     
    'cogs.tickets',
    'cogs.moderation',
    'cogs.logging',
    'cogs.utility',
    'cogs.fun',
    'cogs.automod',
    'cogs.analytics',
    'cogs.reputation',
    'cogs.help'
]

# Configuration constants
MAX_WARNINGS = 3
MUTE_DURATION = 300  # 5 minutes
LOG_RETENTION_DAYS = 30
COOLDOWN_DURATION = 5

# Auto-moderation settings
SPAM_THRESHOLD = 5  # messages
SPAM_TIMEFRAME = 5  # seconds
CAPS_THRESHOLD = 0.7  # percentage
MAX_MENTIONS = 5  # mentions per message

# Analytics settings
ANALYTICS_UPDATE_INTERVAL = 300  # 5 minutes
ANALYTICS_HISTORY_DAYS = 30  # Keep 30 days of history

# Events settings
EVENTS_REMINDER_INTERVAL = 300  # 5 minutes

# Logging settings
MAX_LOGS_PER_CHANNEL = 5000  # Maximum number of logs per channel
MAX_LOG_SIZE = 4096  # Maximum size for log messages
LOG_CLEANUP_INTERVAL = 3600  # Cleanup interval in seconds (1 hour)
MAX_AUDIT_CACHE_SIZE = 1000  # Maximum number of audit log entries to cache per guild