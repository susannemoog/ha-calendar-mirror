"""Constants for the Calendar Mirror integration."""

DOMAIN = "calendar_mirror"

CONF_SOURCE_CALENDARS = "source_calendars"
CONF_TARGET_CALENDAR_ID = "target_calendar_id"
CONF_SYNC_WINDOW_DAYS = "sync_window_days"
CONF_SYNC_INTERVAL_MINUTES = "sync_interval_minutes"

# Which target backend a config entry uses. Stored in entry.data, chosen
# once during the config flow's first step and never changed afterwards
# (switching backends is a new integration entry, not an options-flow edit).
CONF_TARGET_TYPE = "target_type"
TARGET_TYPE_GOOGLE = "google"
TARGET_TYPE_CALDAV = "caldav"

# CalDAV target settings (entry.data only - CalDAV has no OAuth session to
# reuse, so credentials are stored directly, same as HA core's own caldav
# integration).
CONF_CALDAV_URL = "caldav_url"
CONF_CALDAV_USERNAME = "caldav_username"
CONF_CALDAV_PASSWORD = "caldav_password"
CONF_CALDAV_VERIFY_SSL = "caldav_verify_ssl"
# The specific target calendar's own URL, as returned by the server's
# calendar-home listing - lets us rebuild the Calendar object directly via
# `client.calendar(url=...)` on every setup without an extra principal
# round-trip. Doubles as this backend's value for CONF_TARGET_CALENDAR_ID.

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

# The redirect URI users must authorize on their Google Cloud OAuth
# client - HA's OAuth2 helper always proxies through this, never the
# instance's own domain (see notebook gotchas).
OAUTH2_REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"
