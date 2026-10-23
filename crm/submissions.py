"""Durable application ledger. Live delivery is off unless explicitly configured."""
import argparse
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crm.collector import identity
from crm.controls import action_allowed, action_settings


class Blocked(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, job_key TEXT NOT NULL UNIQUE, job_url TEXT NOT NULL,
                notion_url TEXT NOT NULL, company TEXT NOT NULL, channel TEXT NOT NULL,
                recipient TEXT NOT NULL, state TEXT NOT NULL, local_day TEXT NOT NULL,
                cv_sha256 TEXT NOT NULL, profile_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
                token TEXT NOT NULL, created REAL NOT NULL, remote_id TEXT, receipt_json TEXT,
                error TEXT, last_response REAL);
            CREATE TABLE IF NOT EXISTS incoming (
                event_key TEXT PRIMARY KEY, attempt_id TEXT, channel TEXT NOT NULL,
                account TEXT NOT NULL, remote_id TEXT NOT NULL, occurred_at REAL NOT NULL,
                payload_json TEXT NOT NULL, state TEXT NOT NULL,
                FOREIGN KEY(attempt_id) REFERENCES attempts(id));
            CREATE TABLE IF NOT EXISTS outbox (
                event_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS replies (
                id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, channel TEXT NOT NULL,
                recipient TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, token TEXT NOT NULL, receipt_json TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_application_per_crm_job ON attempts(notion_url);
        ''')

    def close(self):
        self.db.close()

    def reserve(self, request, cv_hash, profile_hash, settings, *, live_enabled=False,
                timezone_name="UTC", settings_checked_at=0, now=None):
        now = time.time() if now is None else now
        if request.get("cv_sha256") != cv_hash or request.get("profile_sha256") != profile_hash:
            raise Blocked("CV or profile changed; regenerate this application")
        if request.get("blockers"):
            raise Blocked("Unresolved required fields or manual actions")
        if request.get("prior_application_check") != "clear":
            raise Blocked("Previous application history has not been checked")
        if request.get("channel") not in {"web", "telegram", "email"}:
            raise Blocked("Unsupported channel")
        job_key = identity(request.get("job_url", ""))
        if not job_key or not request.get("notion_url") or not request.get("company"):
            raise Blocked("A verified job identity and CRM record are required")
        if request.get("employer_verified") is not True or not request.get("contact_evidence"):
            raise Blocked("Employer application route must be verified")
        if not request.get("recipient"):
            raise Blocked("Missing recipient or application URL")
        if not request.get('account'):
            raise Blocked('Verified sending account is required')
        if request['channel'] == 'email' and not request.get('message_id'):
            raise Blocked('Persist the email Message-ID before delivery')
        if request["channel"] == "web" and identity(request["recipient"]) != job_key:
            raise Blocked("Web application route belongs to a different job")
        if self.db.execute("SELECT 1 FROM attempts WHERE job_key=? OR notion_url=?", (job_key, request['notion_url'])).fetchone():
            raise Blocked("Application already reserved, submitted or uncertain; never resend automatically")
        if live_enabled is not True:
            return {"state": "dry_run", "job_key": job_key, "request_hash": digest(request),
                    "delivery_performed": False}
        if not 0 <= now - settings_checked_at <= 60:
            raise Blocked("Refresh channel settings immediately before delivery")
        action = request["channel"] + "_apply"
        day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date().isoformat()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM attempts WHERE job_key=? OR notion_url=?", (job_key, request['notion_url'])).fetchone():
                raise Blocked("Application already reserved, submitted or uncertain; never resend automatically")
            used = self.db.execute("SELECT COUNT(*) FROM attempts WHERE local_day=?", (day,)).fetchone()[0]
            if not action_allowed(settings, action, implemented={action}, used_today=used):
                raise Blocked("Channel disabled, disconnected, unavailable or daily limit reached")
            attempt_id, token = str(uuid.uuid4()), str(uuid.uuid4())
            self.db.execute('''INSERT INTO attempts
                (id,job_key,job_url,notion_url,company,channel,recipient,state,local_day,cv_sha256,
                 profile_sha256,request_json,token,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (attempt_id, job_key, request["job_url"], request["notion_url"], request["company"],
                 request["channel"], request["recipient"], "reserved", day, cv_hash, profile_hash,
                 json.dumps(request, ensure_ascii=False), token, now))
            self.enqueue("attempt:" + attempt_id, {"type": "attempt", "attempt_id": attempt_id,
                "notion_url": request["notion_url"], "stage": "Submitting", "job_key": job_key})
        return {"state": "reserved", "attempt_id": attempt_id, "token": token}

    def enqueue(self, event_key, payload):
        self.db.execute("INSERT OR IGNORE INTO outbox(event_key,payload_json) VALUES (?,?)",
                        (event_key, json.dumps(payload, ensure_ascii=False)))

    def check_delivery(self, attempt_id, token, settings, *, live_enabled, settings_checked_at,
                       cv_hash, profile_hash, now=None, timezone_name='UTC'):
        now = time.time() if now is None else now
        row = self.db.execute('SELECT * FROM attempts WHERE id=? AND token=?', (attempt_id, token)).fetchone()
        if not row or row['state'] != 'submitting':
            raise Blocked('No active delivery')
        if datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date().isoformat() != row['local_day']:
            raise Blocked('Reservation belongs to a different local day; reconcile before sending')
        action = row['channel'] + '_apply'
        if live_enabled is not True or not 0 <= now-settings_checked_at <= 60:
            raise Blocked('Live sending is off or settings are stale')
        if row['cv_sha256'] != cv_hash or row['profile_sha256'] != profile_hash:
            raise Blocked('CV/profile changed during delivery')
        used = self.db.execute('SELECT COUNT(*) FROM attempts WHERE local_day=?', (row['local_day'],)).fetchone()[0]-1
        if not action_allowed(settings, action, implemented={action}, used_today=used):
            raise Blocked('Channel was disabled during delivery')
        return {'allowed': True}

    def begin_delivery(self, attempt_id, token, settings, *, live_enabled, settings_checked_at,
                       cv_hash, profile_hash, now=None, timezone_name='UTC'):
        now = time.time() if now is None else now
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM attempts WHERE id=? AND token=?", (attempt_id, token)).fetchone()
            if not row or row["state"] != "reserved":
                raise Blocked("Delivery already started or reservation is invalid")
            if not 0 <= now - row['created'] <= 300:
                raise Blocked('Reservation expired; reconcile it before creating another attempt')
            if datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date().isoformat() != row['local_day']:
                raise Blocked('Reservation belongs to a different local day; reconcile before sending')
            action = row["channel"] + "_apply"
            if live_enabled is not True or not 0 <= now - settings_checked_at <= 60:
                raise Blocked("Live sending is off or settings are stale")
            if row["cv_sha256"] != cv_hash or row["profile_sha256"] != profile_hash:
                raise Blocked("CV/profile changed after reservation")
            # The reservation already consumed a slot. Recheck permissions and the limit excluding itself.
            used = self.db.execute("SELECT COUNT(*) FROM attempts WHERE local_day=?", (row["local_day"],)).fetchone()[0] - 1
            if not action_allowed(settings, action, implemented={action}, used_today=used):
                raise Blocked("Settings changed before delivery")
            request = json.loads(row['request_json'])
            self.db.execute("UPDATE attempts SET state='submitting',remote_id=? WHERE id=?",
                            (request.get('message_id'), attempt_id))
        return json.loads(row["request_json"])

    def finish(self, attempt_id, token, receipt=None, error=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM attempts WHERE id=? AND token=?", (attempt_id, token)).fetchone()
            if not row or row["state"] not in {"submitting", "unknown"}:
                raise Blocked("No active delivery to confirm")
            confirmed = bool(receipt and receipt.get("remote_id") and receipt.get("channel") == row["channel"]
                             and receipt.get("recipient") == row["recipient"])
            state = "sent" if confirmed else "unknown"
            self.db.execute("UPDATE attempts SET state=?,remote_id=?,receipt_json=?,error=? WHERE id=?",
                (state, receipt.get("remote_id") if confirmed else row['remote_id'],
                 json.dumps(receipt, ensure_ascii=False) if receipt else None, error, attempt_id))
            self.enqueue("result:" + attempt_id + ":" + state, {"type": "delivery", "attempt_id": attempt_id,
                "notion_url": row["notion_url"], "stage": "Applied" if confirmed else "Delivery uncertain",
                "receipt": receipt if confirmed else None, "error": error, 'occurred_at': time.time()})
        return state

    def recover(self, *, executor_stopped=False):
        """A crashed in-flight delivery is uncertain, never a retryable reservation."""
        if executor_stopped is not True:
            raise Blocked('Stop the executor before crash recovery')
        with self.db:
            rows = self.db.execute("SELECT id,notion_url FROM attempts WHERE state='submitting'").fetchall()
            replies = self.db.execute("SELECT id,event_key FROM replies WHERE state='submitting'").fetchall()
            self.db.execute("UPDATE attempts SET state='unknown',error='Executor interrupted' WHERE state='submitting'")
            self.db.execute("UPDATE replies SET state='unknown' WHERE state='submitting'")
            for row in rows:
                self.enqueue("result:" + row["id"] + ":unknown", {"type": "delivery", "attempt_id": row["id"],
                    "notion_url": row["notion_url"], "stage": "Delivery uncertain", "error": "Executor interrupted"})
            for row in replies:
                self.enqueue('reply:' + row['id'] + ':unknown', {'type':'reply','state':'unknown',
                    'parent_event_key':row['event_key'],'error':'Executor interrupted'})
        return len(rows) + len(replies)

    def match_response(self, event):
        if event.get("application_id"):
            rows = self.db.execute("SELECT * FROM attempts WHERE id=? AND channel=? AND state IN ('sent','unknown')",
                (event["application_id"], event["channel"])).fetchall()
        elif event.get("in_reply_to"):
            refs = event["in_reply_to"]
            if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                raise ValueError('Reply references must be a list of message IDs')
            rows = self.db.execute("SELECT * FROM attempts WHERE channel=? AND remote_id IN (" +
                ",".join("?" for _ in refs) + ") AND state IN ('sent','unknown')", (event["channel"], *refs)).fetchall()
        elif event.get("job_url"):
            rows = self.db.execute("SELECT * FROM attempts WHERE job_key=? AND channel=? AND state IN ('sent','unknown')",
                (identity(event["job_url"]), event["channel"])).fetchall()
        else:
            rows = []  # A company name or recruiter alone cannot identify one vacancy.
        if len(rows) != 1:
            return None
        row = rows[0]
        # Email sender / Telegram chat must match a reviewed address or peer; no fuzzy subject matching.
        request = json.loads(row["request_json"])
        trusted = {row["recipient"], *request.get("response_senders", [])}
        return row if event.get("sender") in trusted and event.get('account') == request['account'] else None

    def reserve_reply(self, request, settings, *, live_enabled=False, settings_checked_at=0, now=None, daily_limit=5):
        now = time.time() if now is None else now
        parent = self.db.execute('SELECT * FROM incoming WHERE event_key=? AND state=?',
                                 (request.get('parent_event_key'), 'matched')).fetchone()
        if not parent or not request.get('text'):
            raise Blocked('Reply requires a matched incoming message and reviewed text')
        event = json.loads(parent['payload_json'])['event']
        if request.get('channel') != event['channel'] or request.get('recipient') != event.get('sender') or request.get('account') != event['account']:
            raise Blocked('Reply account and recipient must match the actual conversation')
        if str(request.get('in_reply_to')) != str(event['remote_id']):
            raise Blocked('Reply must reference the exact incoming message')
        if request.get('reviewed') is not True:
            raise Blocked('Unreviewed replies cannot be delivered')
        if live_enabled is not True:
            return {'state': 'dry_run', 'delivery_performed': False}
        action = request['channel'] + '_reply'
        if not 0 <= now - settings_checked_at <= 60 or not action_allowed(settings, action, implemented={action}):
            raise Blocked('Reply channel disabled or settings stale')
        if isinstance(daily_limit, bool) or not isinstance(daily_limit, int) or daily_limit <= 0:
            raise Blocked('A positive reply limit is required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM replies WHERE event_key=?', (parent['event_key'],)).fetchone():
                raise Blocked('This message already has a reserved, sent or uncertain reply')
            used = self.db.execute('SELECT COUNT(*) FROM replies WHERE created>=?', (now - 86400,)).fetchone()[0]
            if used >= daily_limit:
                raise Blocked('Reply limit reached')
            ticket = {'attempt_id': str(uuid.uuid4()), 'token': str(uuid.uuid4()), 'state': 'reserved'}
            self.db.execute('INSERT INTO replies VALUES (?,?,?,?,?,?,?,?,NULL)',
                (ticket['attempt_id'], parent['event_key'], request['channel'], request['recipient'],
                 json.dumps(request, ensure_ascii=False), 'reserved', now, ticket['token']))
        return ticket

    def begin_reply(self, ticket, settings, *, live_enabled=False, settings_checked_at=0):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM replies WHERE id=? AND token=?',
                                  (ticket['attempt_id'], ticket['token'])).fetchone()
            if not row or row['state'] != 'reserved':
                raise Blocked('Invalid or already used reply reservation')
            action = row['channel'] + '_reply'
            if live_enabled is not True or not 0 <= time.time()-settings_checked_at <= 60 or not action_allowed(settings, action, implemented={action}):
                raise Blocked('Reply sending is off')
            self.db.execute("UPDATE replies SET state='submitting' WHERE id=?", (row['id'],))
        return json.loads(row['request_json'])

    def check_reply(self, ticket, settings, *, live_enabled=False, settings_checked_at=0):
        row = self.db.execute('SELECT * FROM replies WHERE id=? AND token=?',
                              (ticket['attempt_id'], ticket['token'])).fetchone()
        if not row or row['state'] != 'submitting':
            raise Blocked('No active reply')
        action = row['channel'] + '_reply'
        if live_enabled is not True or not 0 <= time.time()-settings_checked_at <= 60 or not action_allowed(settings, action, implemented={action}):
            raise Blocked('Reply sending is off')
        return {'allowed': True}

    def finish_reply(self, ticket, receipt=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM replies WHERE id=? AND token=?',
                                  (ticket['attempt_id'], ticket['token'])).fetchone()
            if not row or row['state'] not in {'submitting', 'unknown'}:
                raise Blocked('No active reply delivery')
            confirmed = bool(receipt and receipt.get('remote_id') and receipt.get('channel') == row['channel'] and receipt.get('recipient') == row['recipient'])
            state = 'sent' if confirmed else 'unknown'
            self.db.execute('UPDATE replies SET state=?,receipt_json=? WHERE id=?',
                            (state, json.dumps(receipt), row['id']))
            self.enqueue('reply:' + row['id'] + ':' + state,
                         {'type': 'reply', 'state': state, 'parent_event_key': row['event_key'], 'receipt': receipt})
        return state

    def ingest(self, event, classification):
        if not all(event.get(k) for k in ("channel", "account", "remote_id", "text", "occurred_at")):
            raise ValueError("A real message, timestamp and stable account-scoped ID are required")
        allowed = {"Acknowledgement", "Questions", "Interview", "Rejection", "Offer", "Other"}
        if classification.get("outcome") not in allowed:
            raise ValueError("Unknown response classification")
        evidence = classification.get("evidence", "")
        if not evidence or evidence not in event["text"]:
            raise ValueError("Classification requires an exact message quote")
        reason = classification.get("reason", "Not specified")
        if reason not in {'Not specified', 'Location', 'Work authorization / visa', 'Experience', 'Skills / stack',
                          'Level', 'Salary', 'Language', 'Position closed', 'Another candidate', 'Other'}:
            raise ValueError('Unknown rejection reason; preserve details in the quoted evidence')
        if reason != "Not specified":
            quote = classification.get("reason_evidence", "")
            if classification["outcome"] != "Rejection" or not quote or quote not in event["text"]:
                raise ValueError("A rejection reason requires explicit evidence in the actual reply")
        key = digest([event["channel"], event["account"], str(event["remote_id"])])
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM incoming WHERE event_key=?", (key,)).fetchone():
                return "duplicate"
            match = self.match_response(event)
            state = "matched" if match else "manual_review"
            payload = {"event": event, "classification": classification}
            self.db.execute("INSERT INTO incoming VALUES (?,?,?,?,?,?,?,?)",
                (key, match["id"] if match else None, event["channel"], event["account"], str(event["remote_id"]),
                 event["occurred_at"], json.dumps(payload, ensure_ascii=False), state))
            if match:
                latest = match["last_response"] is None or event["occurred_at"] >= match["last_response"]
                if latest:
                    self.db.execute("UPDATE attempts SET last_response=? WHERE id=?", (event["occurred_at"], match["id"]))
                self.enqueue("response:" + key, {"type": "response", "attempt_id": match["id"],
                    "notion_url": match["notion_url"], "message": event, "classification": classification,
                    "update_summary": latest})
            else:
                self.enqueue("response:" + key, {"type": "manual_review", "message": event,
                    "classification": classification, "reason": "No unique trusted application match"})
        return state

    def pending_sync(self):
        return [{"event_key": r["event_key"], **json.loads(r["payload_json"])}
                for r in self.db.execute("SELECT * FROM outbox WHERE delivered=0 ORDER BY rowid")]

    def acknowledge_sync(self, event_key):
        with self.db:
            self.db.execute("UPDATE outbox SET delivered=1 WHERE event_key=?", (event_key,))


def execute(ledger, request, settings_provider, transport, *, live_enabled=False,
            cv_hash, profile_hash, timezone_name="UTC"):
    settings, checked = settings_provider()
    ticket = ledger.reserve(request, cv_hash, profile_hash, settings, live_enabled=live_enabled,
                            timezone_name=timezone_name, settings_checked_at=checked)
    if ticket["state"] == "dry_run":
        return ticket
    settings, checked = settings_provider()  # Fresh gate immediately before the side effect.
    approved = ledger.begin_delivery(ticket["attempt_id"], ticket["token"], settings,
        live_enabled=live_enabled, settings_checked_at=checked, cv_hash=cv_hash, profile_hash=profile_hash, timezone_name=timezone_name)
    try:
        receipt = transport.send(approved)
    except Exception as exc:
        state = ledger.finish(ticket["attempt_id"], ticket["token"], error=type(exc).__name__)
    else:
        state = ledger.finish(ticket["attempt_id"], ticket["token"], receipt=receipt)
    return {"state": state, "attempt_id": ticket["attempt_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "recover", "outbox", "ingest", "ack"])
    parser.add_argument("--db", default=".local/applications.db")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--cv", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--executor-stopped", action="store_true")
    parser.add_argument("--event", type=Path)
    parser.add_argument("--classification", type=Path)
    parser.add_argument("--event-key")
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    ledger = Ledger(args.db)
    try:
        if args.action == "preview":
            request = read(args.request)
            # No CLI switch enables production sending. The agent bridge owns that separate boundary.
            result = ledger.reserve(request, hashlib.sha256(args.cv.read_bytes()).hexdigest(), digest(read(args.profile)), {}, live_enabled=False)
        elif args.action == "recover":
            result = {"uncertain": ledger.recover(executor_stopped=args.executor_stopped)}
        elif args.action == "ingest":
            result = {"result": ledger.ingest(read(args.event), read(args.classification))}
        elif args.action == "ack":
            ledger.acknowledge_sync(args.event_key)
            result = {"acknowledged": args.event_key}
        else:
            result = ledger.pending_sync()
        print(json.dumps(result, ensure_ascii=True))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
