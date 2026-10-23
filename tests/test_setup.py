import json
import unittest
from pathlib import Path
from crm.setup import NOTION_FIELDS, check_config


ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

    def test_new_owner_cannot_pass_completed_setup_without_own_notion_ids(self):
        config = self.config()
        self.assertFalse(check_config(config))
        self.assertEqual(len(check_config(config, require_notion=True)), len(NOTION_FIELDS))
        for field in NOTION_FIELDS:
            config["notion"][field] = "11111111-2222-4333-8444-555555555555"
        self.assertFalse(check_config(config, require_notion=True))

    def test_invalid_ids_timezone_and_parent_directory_are_rejected(self):
        config = self.config()
        config["notion"]["root_page_id"] = "https://notion.so/wrong"
        config["timezone"] = "Invalid/Unknown"
        config["paths"]["private_dir"] = "../another-owner"
        self.assertEqual(len(check_config(config)), 3)

    def test_bootstrap_is_empty_and_all_permissions_are_off(self):
        data = json.loads((ROOT / "examples/notion-bootstrap.json").read_text(encoding="utf-8"))
        self.assertTrue(all(r["Enabled"] == "Off" for r in data["actions"]))
        self.assertTrue(all(r["Collection"] == "Off" for r in data["sources"]))
        self.assertEqual(len({r["Key"] for r in data["actions"]}), 10)
        self.assertEqual(set(data["databases"]), {"jobs", "sources", "answers", "actions", "responses", "referrals"})
        self.assertTrue(data["databases"]["referrals"]["optional"])
        self.assertIn('"Status" SELECT', data["databases"]["jobs"]["schema"])
        self.assertTrue(data["databases"]["responses"]["optional"])
        self.assertIn("{{jobs_data_source_id}}", data["databases"]["responses"]["schema"])
