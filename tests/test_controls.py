import unittest
from crm.controls import action_allowed, action_settings


class ControlsTests(unittest.TestCase):
    def test_missing_off_and_unimplemented_actions_are_blocked(self):
        self.assertFalse(action_allowed({}, "applications", implemented={"applications"}))
        setting = {"applications": {"enabled": True, "ready": True, "daily_limit": 5}}
        self.assertFalse(action_allowed(setting, "applications", implemented=set()))
        setting["applications"]["enabled"] = False
        self.assertFalse(action_allowed(setting, "applications", implemented={"applications"}))

    def test_total_daily_limit_and_independent_email_reply_switches(self):
        setting = {"applications": {"enabled": True, "ready": True, "daily_limit": 5},
                   "email_connect": {"enabled": True, "ready": True}, "email_reply": {"enabled": False, "ready": True}}
        self.assertTrue(action_allowed(setting, "applications", implemented={"applications"}, used_today=4))
        self.assertFalse(action_allowed(setting, "applications", implemented={"applications"}, used_today=5))
        self.assertTrue(action_allowed(setting, "email_connect", implemented={"email_connect"}))
        self.assertFalse(action_allowed(setting, "email_reply", implemented={"email_reply"}))

    def test_malformed_registry_cannot_enable_a_writer(self):
        with self.assertRaises(ValueError):
            action_settings({"has_more": True, "results": []})
        row = {"Ключ": "gmail\\_read", "Режим": "Включено", "Готовность": "Нет подключения"}
        parsed = action_settings({"has_more": False, "results": [row]})
        self.assertFalse(action_allowed(parsed, "email_connect", implemented={"email_connect"}))
        with self.assertRaises(ValueError):
            action_settings({"has_more": False, "results": [row, row]})

    def test_channel_application_needs_both_master_and_connection(self):
        on = {"enabled": True, "ready": True}
        settings = {"applications": dict(on, daily_limit=5), "telegram_connect": dict(on), "telegram_apply": dict(on)}
        self.assertTrue(action_allowed(settings, "telegram_apply", implemented={"telegram_apply"}))
        settings["telegram_connect"]["enabled"] = False
        self.assertFalse(action_allowed(settings, "telegram_apply", implemented={"telegram_apply"}))
        settings["telegram_connect"]["enabled"] = True
        settings["applications"]["enabled"] = False
        self.assertFalse(action_allowed(settings, "telegram_apply", implemented={"telegram_apply"}))

    def test_replies_can_be_off_while_connection_and_applications_are_on(self):
        on = {"enabled": True, "ready": True}
        settings = {"applications": dict(on, daily_limit=5), "email_connect": dict(on),
                    "email_apply": dict(on), "email_reply": {"enabled": False, "ready": True}}
        self.assertTrue(action_allowed(settings, "email_apply", implemented={"email_apply"}))
        self.assertFalse(action_allowed(settings, "email_reply", implemented={"email_reply"}))
        settings["email_reply"]["enabled"] = True
        settings["applications"]["enabled"] = False
        self.assertTrue(action_allowed(settings, "email_reply", implemented={"email_reply"}))

    def test_legacy_and_new_keys_cannot_silently_duplicate_permissions(self):
        a = {"Ключ": "gmail_read", "Режим": "Выключено", "Готовность": "Готово"}
        b = {"Ключ": "email_connect", "Режим": "Включено", "Готовность": "Готово"}
        with self.assertRaises(ValueError):
            action_settings({"has_more": False, "results": [a, b]})
