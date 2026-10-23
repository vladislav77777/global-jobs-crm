"""Create a private workspace config or check it without contacting external services."""
import argparse
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NOTION_FIELDS = {"root_page_id", "profile_page_id", "rules_page_id",
                 "jobs_data_source_id", "sources_data_source_id", "answers_data_source_id", "actions_data_source_id"}


def check_config(config, require_notion=False):
    errors = []
    try:
        ZoneInfo(config.get("timezone", ""))
    except (ZoneInfoNotFoundError, ValueError):
        errors.append("Unknown timezone; install tzdata on Windows if needed")
    notion = config.get("notion", {})
    for field in NOTION_FIELDS | {"preparation_page_id", "responses_data_source_id", "referrals_data_source_id"}:
        value = notion.get(field, "")
        if require_notion and not value and field in NOTION_FIELDS:
            errors.append("Missing Notion ID: " + field)
        elif value and not re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            errors.append("Use the bare Notion UUID for " + field)
    collection = config.get("collection", {})
    # These limits currently belong to the collection implementation, not an arbitrary config knob.
    for name, expected in (("lookback_days", 14), ("overlap_hours", 72), ("max_new_jobs", 20)):
        if collection.get(name) != expected:
            errors.append(name + " must match the supported default " + str(expected))
    if collection.get('telegram_max_age_days', 21) != 21:
        errors.append('telegram_max_age_days must be 21')
    paths = config.get("paths", {})
    delivery = config.get('delivery', {})
    if type(delivery.get('live_sending', False)) is not bool:
        errors.append('live_sending must be a JSON boolean')
    for name in ("private_dir", "output_dir"):
        value = paths.get(name, "")
        if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
            errors.append("Use a workspace-relative directory for " + name)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "check"])
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    parser.add_argument("--template", type=Path, default=Path("config.example.json"))
    parser.add_argument("--require-notion", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        template = json.loads(args.template.read_text(encoding="utf-8"))
        errors = check_config(template)
        if errors:
            raise ValueError("Invalid setup template: " + "; ".join(errors))
        with args.config.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(template, ensure_ascii=False, indent=2) + "\n")
        for name in ("private_dir", "output_dir"):
            Path(template["paths"][name]).mkdir(parents=True, exist_ok=True)
        print("Created private config; complete it after MCP setup")
        return
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    errors = check_config(config, args.require_notion)
    print(json.dumps({"valid": not errors, "errors": errors, "external_connections_checked": False}))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
