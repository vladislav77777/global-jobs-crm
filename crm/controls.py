"""Read action switches. A saved switch is separate from an implemented executor."""
import re
from crm.schema import snapshot as legacy_snapshot

ACTION_KEYS = {"applications", "web_apply", "telegram_connect", "telegram_apply", "telegram_reply",
               "email_connect", "email_apply", "email_reply", "follow_up", "referrals"}
LEGACY_KEYS = {"gmail_read": "email_connect", "auto_replies": "email_reply"}


def action_settings(snapshot):
    snapshot = legacy_snapshot(snapshot)
    if snapshot.get("has_more") is not False:
        raise ValueError("Complete action settings required")
    settings = {}
    for row in snapshot["results"]:
        key = re.sub(r"\\_", "_", row.get("Ключ", ""))
        key = LEGACY_KEYS.get(key, key)
        if key not in ACTION_KEYS:
            continue
        if key in settings:
            raise ValueError("Duplicate action setting: " + key)
        settings[key] = {"enabled": row.get("Режим") == "Включено",
                         "ready": row.get("Готовность") == "Готово",
                         "daily_limit": row.get("Лимит в день")}
    return settings


def action_allowed(settings, key, *, implemented, used_today=0, channel=None):
    setting = settings.get(key, {})
    if key not in ACTION_KEYS or key not in implemented or not setting.get("enabled") or not setting.get("ready"):
        return False
    def ready(dependency):
        value = settings.get(dependency, {})
        return value.get("enabled") is True and value.get("ready") is True

    if key in {"applications", "web_apply", "telegram_apply", "email_apply", "follow_up"}:
        if not ready("applications"):
            return False
        limit = settings["applications"].get("daily_limit")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not float(limit).is_integer() or limit <= 0:
            return False
        if not 0 <= used_today < limit:
            return False
    if key.startswith("telegram_") and key != "telegram_connect" and not ready("telegram_connect"):
        return False
    if key.startswith("email_") and key != "email_connect" and not ready("email_connect"):
        return False
    if key == "follow_up":
        return channel in {"telegram", "email"} and ready(channel + "_connect")
    return True
