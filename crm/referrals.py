"""Prepare referral drafts for verified first-degree connections. No sending API."""
import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from crm.collector import identity
from crm.status import effective_stage


def plain(value):
    return re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', value or '').replace('\\_', '_')


def company_key(value):
    return re.sub(r'[^\w]+', '', plain(value).casefold())


def person_url(value):
    p = urlsplit(value or '')
    if p.hostname not in {'linkedin.com', 'www.linkedin.com'} or p.username or p.password:
        raise ValueError('A verified LinkedIn profile URL is required')
    if not re.fullmatch(r'/in/[^/]+/?', p.path):
        raise ValueError('Use a person profile, not search or messaging URL')
    return 'https://www.linkedin.com' + p.path.rstrip('/')


def request_key(account, person, job):
    return hashlib.sha256(json.dumps([person_url(account), person_url(person), identity(job)],
        separators=(',', ':')).encode()).hexdigest()


def build_plan(jobs, contacts, history, profile, sources, today=None, limit=5):
    for snapshot in [jobs, history, sources]:
        if snapshot.get('has_more') is not False:
            raise ValueError('Complete fresh jobs, referral history and sources are required')
    source = [x for x in sources['results'] if x.get('Name') == 'Referral Connections']
    if len(source) != 1:
        raise ValueError('Missing or duplicate referral source')
    result = {'create': [], 'reuse': [], 'skipped': [], 'sending_enabled': False}
    if source[0].get('Collection') != 'On':
        result['skipped'].append({'reason': 'source_off'})
        return result
    account = person_url(profile['linkedin'])
    intro, evidence = profile.get('intro', '').strip(), profile.get('intro_evidence', '')
    if (profile.get('reviewed') is not True or not intro or not evidence or
            evidence not in profile.get('evidence_text', '') or len(intro.split()) > 40):
        raise ValueError('A short reviewed introduction and current profile evidence are required')
    today = today or date.today()
    known, active = {}, set()
    for row in history['results']:
        if person_url(row.get('Account')) != account:
            raise ValueError('Referral history belongs to another owner')
        key = row.get('Request key')
        if not key or key in known:
            raise ValueError('Missing or duplicate referral identity')
        known[key] = row
        if row.get('Status') != 'Closed':
            active.add(company_key(row.get('Company')))
    seen_jobs = set()
    for job in jobs['results']:
        job_url = plain(job.get('Employer job URL'))
        key = identity(job_url)
        if not key or not job.get('url'):
            continue
        if key in seen_jobs:
            raise ValueError('Duplicate employer requisition in jobs')
        seen_jobs.add(key)
        if effective_stage(job) not in {'CV match', 'Ready to apply'}:
            continue
        if job.get('Link check') not in {'Form available', 'Description available'}:
            continue
        company = plain(job.get('Company'))
        if not company_key(company):
            continue
        for contact in contacts:
            if company_key(contact.get('company')) != company_key(company) or company_key(contact.get('current_company')) != company_key(company):
                continue
            person = person_url(contact['linkedin'])
            req = request_key(account, person, job_url)
            checked = date.fromisoformat(contact.get('checked', ''))
            verified = (contact.get('first_degree') is True and contact.get('current_employer_verified') is True
                        and contact.get('connection_evidence') and contact.get('employer_evidence')
                        and 0 <= (today - checked).days <= 7)
            if not verified:
                result['skipped'].append({'person': person, 'reason': 'contact_unverified_or_stale'})
                continue
            if req in known:
                result['reuse'].append({'key': req, 'page_url': known[req]['url']})
                continue
            if company_key(company) in active or len(result['create']) >= limit:
                result['skipped'].append({'person': person, 'reason': 'company_active_or_batch_limit'})
                continue
            name, role = plain(contact['name']), plain(job['Role'])
            first = name.split()[0]
            message = (f'Hi {first}, I noticed the {role} opening at {company}. '
                       f'{intro} Would you be comfortable referring me for this role? '
                       f'Happy to share my CV. Job link: {job_url}')
            if len(message.split()) > 100:
                result['skipped'].append({'person': person, 'reason': 'draft_too_long'})
                continue
            blockers = 'Reconcile previous outreach and applications before sending.'
            if job.get('Input needed'):
                blockers += ' Job constraints: ' + plain(job['Input needed'])
            props = {'Request': name + ' · ' + company, 'Person': name, 'Company': company,
                'Contact role': contact.get('role', ''), 'LinkedIn': person, 'Job': [job['url']],
                'Status': 'Draft', 'Message draft': message,
                'Why this person': plain(contact['employer_evidence']), 'Input needed': blockers,
                'Next action': 'Review the draft and prior contact history. Sending is disabled.',
                'Evidence URL': person, 'date:Checked:start': today.isoformat(), 'date:Checked:is_datetime': 0,
                'Request key': req, 'Account': account, 'CV version': profile.get('cv_version', '')}
            content = ('## Message draft\n\n' + message + '\n\n## Before sending\n\n' + blockers +
                       '\n\nExisting first-degree connection and current employer were checked. No message has been sent.')
            result['create'].append({'properties': props, 'content': content})
            known[req] = {'url': 'pending:' + req}
            active.add(company_key(company))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['jobs', 'contacts', 'history', 'profile', 'sources', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    read = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
    result = build_plan(**{name: read(getattr(args, name)) for name in ['jobs', 'contacts', 'history', 'profile', 'sources']})
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: len(v) for k, v in result.items() if isinstance(v, list)}))


if __name__ == '__main__':
    main()
