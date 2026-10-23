"""Plan read-only job collection writes; Notion MCP executes and verifies them."""
import argparse
import html
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from crm.schema import snapshot as legacy_snapshot, properties as ui_properties, is_english
from crm.status import public_status

SOURCE_NAMES = {"Telegram": "Telegram · Global Jobs", "HiringCafe": "HiringCafe", "LinkedIn Jobs": "LinkedIn · Jobs & Posts", "LinkedIn Post": "LinkedIn · Jobs & Posts", "Сайт компании": "Сайты компаний"}


def canonical_url(value):
    value = html.unescape(value or "").replace("\\_", "_").strip()
    match = re.search(r"\]\((https?://[^\s)]+)\)", value)
    if match:
        value = match.group(1)
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "gh_src", "trk", "trackingid"}]
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query)), ""))


def identity(value):
    url = canonical_url(value)
    if not url:
        return value.strip() if value and re.match(r"^(greenhouse|lever|revolut|bol|ashby|telegram):", value) else ""
    p = urlsplit(url)
    path = p.path.strip("/")
    bits = path.split("/")
    if p.hostname in {"t.me", "telegram.me"} and not (len(bits) >= 2 and bits[-1].isdigit()):
        return ""  # One recruiter can advertise many distinct jobs.
    gh = dict(parse_qsl(p.query)).get("gh_jid")
    if "greenhouse.io" in p.hostname and len(bits) >= 3 and bits[1] == "jobs":
        return f"greenhouse:{bits[0]}:{bits[2]}"
    if p.hostname == "careers.adyen.com":
        m = re.search(r"/vacancies/(\d+)", p.path)
        if m:
            return f"greenhouse:adyen:{m[1]}"
    if p.hostname == "careers.vinted.com":
        m = re.search(r"/jobs/(?:j/)?(\d+)", p.path)
        if m:
            return f"greenhouse:vinteden:{m[1]}"
    if p.hostname in {"www.databricks.com", "databricks.com"} and gh:
        return f"greenhouse:databricks:{gh}"
    if p.hostname == "jobs.lever.co" and len(bits) >= 2:
        return f"lever:{bits[0]}:{bits[1]}"
    if p.hostname in {"www.revolut.com", "revolut.com"}:
        m = re.search(r"careers/(?:apply|position)/(?:[a-z0-9-]+-)?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:/|$)", path)
        if m:
            return f"revolut:{m[1]}"
    if p.hostname == "careers.bol.com":
        m = re.search(r"/(\d{8,})$", p.path)
        if m:
            return f"bol:{m[1]}"
    if p.hostname == "jobs.ashbyhq.com" and len(bits) >= 2:
        return f"ashby:{bits[0].lower()}:{bits[1]}"
    return url


def row_keys(row):
    row = ui_properties(row, english=False)
    return {k for field in ("Ключ дубликата", "Вакансия компании", "Ссылка для отклика") if (k := identity(row.get(field, "")))}


def build_plan(snapshot, candidates, sources, today=None, limit=20):
    english = is_english(snapshot) or is_english(sources)
    snapshot, sources = legacy_snapshot(snapshot), legacy_snapshot(sources)
    candidates = [dict(c, properties=ui_properties(c['properties'], english=False)) for c in candidates]
    if snapshot.get("has_more") is not False:
        raise ValueError("A complete, freshly paginated Notion snapshot is required")
    if sources.get("has_more") is not False:
        raise ValueError("Source registry is incomplete")
    enabled = {r["Name"] for r in sources["results"] if r.get("Сбор") == "Включён"}
    today = today or date.today()
    rows = snapshot["results"]
    index = {}
    for row in rows:
        for key in row_keys(row):
            if key in index and index[key]["url"] != row["url"]:
                raise ValueError(f"Existing duplicate identity requires review: {key}")
            index[key] = row
    creates, updates, skipped = [], {}, []
    for candidate in candidates:
        prop = candidate["properties"]
        reason = None
        if SOURCE_NAMES.get(prop.get("Источник")) not in enabled:
            reason = "source_disabled_or_unknown"
        elif not candidate.get("reviewed"):
            reason = "not_reviewed"
        elif candidate.get("posted") and date.fromisoformat(candidate["posted"]) > today:
            reason = "future_date"
        elif prop.get("Источник") == "Telegram" and not candidate.get("posted"):
            reason = "telegram_post_date_missing"
        elif prop.get("Источник") == "Telegram" and date.fromisoformat(candidate["posted"]) < today - timedelta(days=21):
            reason = "telegram_older_than_21_days"
        elif prop.get("Источник") != "Telegram" and candidate.get("employer_open_verified") is not True:
            reason = "employer_opening_unverified"
        elif not candidate.get("direct_verified"):
            reason = "direct_route_unverified"
        keys = row_keys(prop)
        if not keys:
            reason = reason or "missing_identity"
        if reason:
            skipped.append({"title": prop.get("Вакансия"), "reason": reason})
            continue
        matches = {index[k]["url"] for k in keys if k in index}
        if len(matches) > 1:
            raise ValueError("Candidate matches multiple existing pages; do not merge blindly")
        links = set(candidate.get("sources", []))
        links.add(prop["Исходная публикация"])
        if matches:
            match = next(index[k] for k in keys if k in index)
            if match["url"].startswith("pending:"):
                match["candidate"]["sources"] = sorted(set(match["candidate"].get("sources", [])) | links)
            else:
                update = updates.setdefault(match["url"], {"page_id": match["url"].split("/")[-1], "properties": {"date:Последняя проверка:start": today.isoformat(), "date:Последняя проверка:is_datetime": 0, "Проверка ссылки": prop["Проверка ссылки"]}, "sources": set()})
                update["sources"].update(links)
                old = match.get("Все источники", "")
                additional = sorted(u for u in update["sources"] if u not in old)
                if additional:
                    update["properties"]["Все источники"] = old + ("\n" if old else "") + "\n".join(additional)
            continue
        if len(creates) >= limit:
            skipped.append({"title": prop.get("Вакансия"), "reason": "limit_deferred"})
            continue
        prop = dict(prop)
        prop["Ключ дубликата"] = identity(prop.get("Вакансия компании") or prop.get("Ключ дубликата") or prop["Ссылка для отклика"])
        prop["date:Последняя проверка:start"] = today.isoformat()
        prop["date:Последняя проверка:is_datetime"] = 0
        prop["Результат ответа"] = "Не откликался"
        candidate = dict(candidate, properties=prop, sources=sorted(links))
        creates.append(candidate)
        pending = {**prop, "url": f"pending:{len(creates)}", "candidate": candidate}
        for key in keys | row_keys(prop):
            index[key] = pending
    for candidate in creates:
        candidate["properties"]["Все источники"] = "\n".join(candidate["sources"])
    result = {"create": creates, "update": [{k: v for k, v in x.items() if k != "sources"} for x in updates.values()], "skipped": skipped}
    if english:
        for item in result['create'] + result['update']:
            item['properties'] = ui_properties(item['properties'])
        for item in result['create']:
            item['properties']['Status'] = public_status(item['properties'].get('Stage', 'New'))
    return result


class RunState:
    """Atomic lease and source checkpoints; failed/partial sources never advance."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT, expires REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS checkpoint (source TEXT PRIMARY KEY, cursor TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS progress (source TEXT PRIMARY KEY, cursor TEXT, complete INTEGER)")

    def acquire(self, ttl=1800):
        token = str(uuid.uuid4())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT token, expires FROM lease WHERE id=1").fetchone()
            if row and row[1] > time.time():
                raise RuntimeError("Another collection run holds the lease")
            self.db.execute("INSERT OR REPLACE INTO lease VALUES (1, ?, ?)", (token, time.time() + ttl))
        return token

    def checkpoint(self, token, source, cursor, *, complete, writes_verified):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT token, expires FROM lease WHERE id=1").fetchone()
            if not row or row[0] != token or row[1] <= time.time():
                raise RuntimeError("Lease lost; refresh Notion before resuming")
            if complete and writes_verified:
                self.db.execute("INSERT OR REPLACE INTO checkpoint VALUES (?,?)", (source, json.dumps(cursor, ensure_ascii=False)))
            if writes_verified:
                self.db.execute("INSERT OR REPLACE INTO progress VALUES (?,?,?)", (source, json.dumps(cursor, ensure_ascii=False), int(complete)))
            self.db.execute("UPDATE lease SET expires=? WHERE id=1", (time.time() + 1800,))

    def release(self, token):
        with self.db:
            self.db.execute("DELETE FROM lease WHERE id=1 AND token=?", (token,))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--candidates", type=Path, required=True)
    p.add_argument("--sources", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--today", type=date.fromisoformat)
    a = p.parse_args()
    plan = build_plan(json.loads(a.snapshot.read_text(encoding="utf-8-sig")), json.loads(a.candidates.read_text(encoding="utf-8-sig")), json.loads(a.sources.read_text(encoding="utf-8-sig")), a.today)
    a.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: len(v) for k, v in plan.items()}))


if __name__ == "__main__":
    main()
