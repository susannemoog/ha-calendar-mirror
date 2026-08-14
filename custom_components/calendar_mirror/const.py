"""Constants for the Calendar Mirror integration."""

DOMAIN = "calendar_mirror"

CONF_SOURCE_CALENDARS = "source_calendars"
CONF_TARGET_CALENDAR_ID = "target_calendar_id"
CONF_SYNC_WINDOW_DAYS = "sync_window_days"
CONF_SYNC_INTERVAL_MINUTES = "sync_interval_minutes"

DEFAULT_SYNC_WINDOW_DAYS = 365
DEFAULT_SYNC_INTERVAL_MINUTES = 20

# Tag used in synced event descriptions so we only ever touch events we
# created ourselves, never anything the user added by hand in the target
# calendar.
SYNC_TAG = "[calendar-mirror]"

# Prepended to the event title so synced events are visibly marked even in
# views that don't show the description (e.g. Google Calendar's month/week
# grid). Google's API has no per-event "read-only" flag for the calendar
# owner - permissions are calendar-level, not per-event - so this is a
# visible warning rather than an enforced restriction: manual edits are
# still possible, they just get overwritten on the next sync.
SYNC_TITLE_PREFIX = "🔒 "

OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"
