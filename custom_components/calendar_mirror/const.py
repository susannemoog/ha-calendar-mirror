"""Constants for the Calendar Mirror integration."""

DOMAIN = "calendar_mirror"

CONF_SOURCE_CALENDARS = "source_calendars"
CONF_TARGET_CALENDAR_ID = "target_calendar_id"
CONF_SYNC_WINDOW_DAYS = "sync_window_days"
CONF_SYNC_INTERVAL_MINUTES = "sync_interval_minutes"

DEFAULT_SYNC_WINDOW_DAYS = 30
DEFAULT_SYNC_INTERVAL_MINUTES = 20

# Tag used in synced event descriptions so we only ever touch events we
# created ourselves, never anything the user added by hand in the target
# calendar.
SYNC_TAG = "[calendar-mirror]"

OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"
