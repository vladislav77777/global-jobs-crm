"""Idempotent Notion write plans for delivery receipts and incoming messages."""
from datetime import datetime, timezone
import hashlib
import json
from crm.status import effective_stage, public_status


def history_key(event_key):
    return hashlib.sha256(event_key.encode('utf-8')).hexdigest()


def plain_markdown(value):
    return ''.join('\\' + c if c in '\\*~`$[]<>{}|^' else c for c in str(value))


def sync_plan(event, current_properties, current_content='', history_records=None):
    # Compatibility with old records only. New writes never put markers in page bodies.
    marker = '[communication:' + event['event_key'] + ']'
    if marker in current_content:
        return {'action': 'already_synced', 'event_key': event['event_key']}
    if not isinstance(history_records, dict) or history_records.get('has_more') is not False:
        raise ValueError('A complete fresh response-history snapshot is required')
    key = history_key(event['event_key'])
    matches = [r for r in history_records['results'] if r.get('Event key') == key]
    if len(matches) > 1:
        raise ValueError('Duplicate response-history identities; review before writing')
    existing = matches[0] if matches else None
    message, classification = event.get('message', {}), event.get('classification', {})
    manual = event['type'] == 'manual_review'
    final_state = 'Review only' if manual else 'Applied'
    if existing:
        if message and (existing.get('Account') != message.get('account') or
                        existing.get('Message ID') != str(message.get('remote_id'))):
            raise ValueError('Response-history identity does not match the actual message')
        if existing.get('Review') == 'Ignored' or existing.get('Sync state') == final_state:
            return {'action': 'already_synced', 'event_key': event['event_key'], 'history_url': existing['url']}
    props, stage = {}, effective_stage(current_properties)
    if event['type'] == 'delivery':
        if stage in {'CV match', 'Ready to apply', 'Submitting', 'Delivery uncertain', 'New'}:
            props['Stage'] = event['stage']
        if event.get('receipt'):
            props['Delivery receipt'] = json.dumps(event['receipt'], ensure_ascii=False)
            if event.get('occurred_at'):
                props['date:Applied date:start'] = datetime.fromtimestamp(event['occurred_at'], timezone.utc).isoformat()
                props['date:Applied date:is_datetime'] = 1
            if current_properties.get('Response outcome') in {None, '', 'Not applied'}:
                props['Response outcome'] = 'Awaiting response'
    elif event['type'] == 'response' and event.get('update_summary'):
        current_date = current_properties.get('date:Response date:start')
        current_time = datetime.fromisoformat(current_date.replace('Z', '+00:00')) if current_date else None
        if current_time and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        newer = current_time is None or current_time.timestamp() <= message['occurred_at']
        if not newer:
            return {**sync_plan(dict(event, update_summary=False), current_properties, '', history_records), 'summary_skipped': 'A newer response is already saved'}
        props.update({'Latest reply': classification['evidence'], 'Response outcome':
            {'Interview': 'Interview invitation'}.get(classification['outcome'], classification['outcome']),
            'date:Response date:start': datetime.fromtimestamp(message['occurred_at'], timezone.utc).isoformat(),
            'date:Response date:is_datetime': 1})
        props['Response URL'] = message.get('url', '')
        if classification['outcome'] == 'Rejection':
            props['Rejection reason'] = classification.get('reason', 'Not specified')
            props['Response notes'] = classification.get('reason_evidence', '')
        # Acknowledgements are not recruiter conversations. Never regress a manual/advanced stage.
        if stage in {'Applied', 'Conversation'}:
            target = {'Questions': 'Conversation', 'Interview': 'Interview', 'Rejection': 'Rejection', 'Offer': 'Offer'}.get(classification['outcome'])
            if target:
                props['Stage'] = target
    if 'Stage' in props:
        props['Status'] = public_status(props['Stage'])
    outcome = {'Interview': 'Interview invitation'}.get(classification.get('outcome'), classification.get('outcome', 'Other'))
    title = event.get('title') or message.get('subject') or 'Application update'
    history_props = {'Response': title, 'Company': event.get('company') or message.get('company', ''),
        'Outcome': outcome, 'Review': 'Needs matching' if manual else 'Matched',
        'Event key': key, 'Account': message.get('account', ''),
        'Message ID': str(message.get('remote_id', '')), 'Original email': message.get('url', ''),
        'Reason': classification.get('reason_evidence') or ('Not specified' if outcome == 'Rejection' else ''),
        'Sync state': final_state if manual else 'Pending'}
    if message.get('occurred_at'):
        history_props.update({'date:Received:start': datetime.fromtimestamp(message['occurred_at'], timezone.utc).isoformat(),
                              'date:Received:is_datetime': 1})
    if event.get('notion_url'):
        history_props['Job'] = [event['notion_url']]
    evidence = ' '.join(classification.get('evidence', '').split())
    content = ('## Message\n\n> ' + plain_markdown(evidence) + '\n\n' if evidence else '')
    content += ('Match this message to the original application. Current job statuses are unchanged.' if manual
                else 'Saved with the original application.')
    return {'action': 'sync', 'event_key': event['event_key'], 'page_url': event.get('notion_url'),
            'properties': props, 'history': {'existing_url': existing.get('url') if existing else None,
            'properties': history_props, 'content': content, 'final_state': final_state}}


def main():
    import argparse
    from pathlib import Path
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--pages', type=Path, required=True, help='Fresh pages keyed by URL with properties and content')
    p.add_argument('--history', type=Path, required=True, help='Complete fresh Email responses snapshot')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    events = json.loads(args.events.read_text(encoding='utf-8-sig'))
    pages = json.loads(args.pages.read_text(encoding='utf-8-sig'))
    history = json.loads(args.history.read_text(encoding='utf-8-sig'))
    plans = []
    for event in events:
        page = pages[event['notion_url']] if event.get('notion_url') else {'properties': {}, 'content': ''}
        plans.append(sync_plan(event, page['properties'], page['content'], history))
    args.output.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
