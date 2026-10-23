"""Optional SMTP/IMAP transport; account secrets are environment variables only."""
import email
import imaplib
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default
from email.utils import make_msgid, parsedate_to_datetime
from pathlib import Path


class EmailTransport:
    def __init__(self, config, smtp_factory=smtplib.SMTP_SSL):
        self.config, self.smtp_factory = config, smtp_factory

    def send(self, request):
        config = self.config
        if config['sender'] != request.get('account'):
            raise ValueError('SMTP sender does not match the reviewed application account')
        if email.utils.parseaddr(request['recipient'])[1] != request['recipient']:
            raise ValueError('Use exactly one verified plain employer email address')
        if not request.get('in_reply_to') and not request.get('attachments') and not request.get('cv_url'):
            raise ValueError('An application requires a verified CV attachment or link')
        message = EmailMessage()
        message["From"] = config["sender"]
        message["To"] = request["recipient"]
        message["Subject"] = request.get("subject") or request["role"] + " — " + request["candidate_name"]
        if not request.get('message_id'):
            raise ValueError('Persist a unique Message-ID in the request before reserving delivery')
        message["Message-ID"] = request['message_id']
        if request.get("in_reply_to"):
            message["In-Reply-To"] = request["in_reply_to"]
            message["References"] = " ".join(request.get("references", [request["in_reply_to"]]))
        message.set_content(request["text"] + ('\n\nCV: ' + request['cv_url'] if request.get('cv_url') else ''))
        for attachment in request.get("attachments", []):
            path = Path(attachment["path"])
            data = path.read_bytes()
            import hashlib
            if hashlib.sha256(data).hexdigest() != attachment["sha256"]:
                raise ValueError("CV attachment changed")
            message.add_attachment(data, maintype="application", subtype="pdf", filename=path.name)
        with self.smtp_factory(config["smtp_host"], config.get("smtp_port", 465),
                               context=ssl.create_default_context(), timeout=30) as client:
            client.login(os.environ[config["username_env"]], os.environ[config["password_env"]])
            refused = client.send_message(message)
        if refused:
            raise RuntimeError("Recipient was refused; review delivery state")
        return {"channel": "email", "recipient": request["recipient"], "remote_id": message["Message-ID"],
                "confirmation": "SMTP server accepted the message; employer response is not implied"}


def read_email(config, cursor=None, *, enabled=False, imap_factory=imaplib.IMAP4_SSL, limit=100):
    """Return read-only messages; commit their cursor only after durable ledger ingestion."""
    if enabled is not True:
        return []
    cursor = cursor or {}
    events = []
    with imap_factory(config["imap_host"], config.get("imap_port", 993), ssl_context=ssl.create_default_context(), timeout=30) as client:
        client.login(os.environ[config["username_env"]], os.environ[config["password_env"]])
        status, _ = client.select(config.get("mailbox", "INBOX"), readonly=True)
        if status != "OK":
            raise RuntimeError("Cannot read mailbox")
        status, validity = client.response('UIDVALIDITY')
        if not validity or not validity[0]:
            raise RuntimeError('Mailbox UIDVALIDITY missing; cannot maintain a safe cursor')
        validity = validity[0].decode()
        since_uid = cursor.get('uid', 0) if cursor.get('uidvalidity') == validity else 0
        status, data = client.uid("search", None, "UID", str(int(since_uid) + 1) + ":*")
        if status != "OK":
            raise RuntimeError("Cannot list incoming messages")
        for uid in sorted(data[0].split(), key=int)[:limit]:
            if int(uid) <= int(since_uid):
                continue
            status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError("Cannot read message; cursor must not advance")
            msg = email.message_from_bytes(next(p[1] for p in parts if isinstance(p, tuple)), policy=default)
            body = msg.get_body(preferencelist=("plain",))
            text = body.get_content() if body else ""
            if not text:
                body = msg.get_body(preferencelist=('html',))
                # Preserve HTML as evidence, do not execute or follow anything in it.
                text = body.get_content() if body else '[Message has no readable text; inspect original]'
            if not msg.get('Date'):
                raise RuntimeError('Message date missing; review before advancing mailbox cursor')
            refs = (str(msg.get("References", "")) + " " + str(msg.get("In-Reply-To", ""))).split()
            events.append({"channel": "email", "account": config["sender"],
                "remote_id": str(msg.get("Message-ID") or (config.get('mailbox', 'INBOX') + ':' + validity + ':' + uid.decode())),
                "cursor": {'uid': int(uid), 'uidvalidity': validity},
                "sender": email.utils.parseaddr(str(msg.get("From", "")))[1], "in_reply_to": refs,
                "text": text, "occurred_at": parsedate_to_datetime(msg["Date"]).timestamp()})
    return events
