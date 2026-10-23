"""Agent bridge: reserve -> begin -> one native action -> finish. Default: test mode."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from crm.controls import action_settings
from crm.submissions import Ledger, Blocked, digest


def context(config, snapshot, cv, profile):
    checked = snapshot.get('checked_at', 0)
    if snapshot.get('data_source_id', '').replace('-', '') != config['notion']['actions_data_source_id'].replace('-', ''):
        raise Blocked('Settings belong to another workspace')
    if not 0 <= time.time() - checked <= 60:
        raise Blocked('Read all channel settings again; cached settings expire after 60 seconds')
    if snapshot.get('has_more') is not False:
        raise Blocked('Incomplete settings snapshot')
    return dict(settings=action_settings(snapshot), settings_checked_at=checked,
                live_enabled=config.get('delivery', {}).get('live_sending') is True,
                cv_hash=hashlib.sha256(cv).hexdigest(), profile_hash=digest(profile))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['preview', 'reserve', 'begin', 'check', 'finish', 'reserve-reply', 'begin-reply', 'check-reply', 'finish-reply'])
    for name in ['config', 'settings', 'cv', 'profile', 'request', 'ticket', 'receipt']:
        p.add_argument('--' + name, type=Path, required=name == 'config')
    p.add_argument('--error', help='Short error category only; never credentials or raw transport output')
    args = p.parse_args()
    read = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
    config = read(args.config)
    ledger = Ledger(Path(config['paths']['private_dir']) / 'applications.db')
    try:
        if args.action in {'finish', 'finish-reply'}:
            ticket = read(args.ticket)
            receipt = read(args.receipt) if args.receipt else None
            if args.action == 'finish':
                result = ledger.finish(ticket['attempt_id'], ticket['token'], receipt, args.error)
            else:
                result = ledger.finish_reply(ticket, receipt)
        else:
            ctx = context(config, read(args.settings), args.cv.read_bytes(), read(args.profile))
            if args.action == 'preview':
                ctx['live_enabled'] = False
            if args.action in {'reserve', 'preview'}:
                result = ledger.reserve(read(args.request), **ctx, timezone_name=config['timezone'])
            elif args.action in {'begin', 'check'}:
                ticket = read(args.ticket)
                method = ledger.begin_delivery if args.action == 'begin' else ledger.check_delivery
                result = method(ticket['attempt_id'], ticket['token'], **ctx, timezone_name=config['timezone'])
            else:
                reply_ctx = {k: v for k, v in ctx.items() if k not in {'cv_hash', 'profile_hash'}}
                if args.action == 'reserve-reply':
                    result = ledger.reserve_reply(read(args.request), **reply_ctx,
                        daily_limit=config.get('delivery', {}).get('max_replies_per_24h', 5))
                else:
                    method = ledger.check_reply if args.action == 'check-reply' else ledger.begin_reply
                    result = method(read(args.ticket), **reply_ctx)
        print(json.dumps(result, ensure_ascii=True))
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
