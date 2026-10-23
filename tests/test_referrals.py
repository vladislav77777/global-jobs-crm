import copy
import unittest
from datetime import date
from crm.referrals import build_plan, request_key


class ReferralTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 23)
        self.profile = {'linkedin': 'https://www.linkedin.com/in/owner', 'reviewed': True,
            'intro': 'I build payment services.', 'intro_evidence': 'Payment services',
            'evidence_text': 'Payment services', 'cv_version': 'test'}
        self.jobs = {'has_more': False, 'results': [{'url': 'https://notion.so/job',
            'Company': 'Example', 'Role': 'Backend Engineer', 'Stage': 'CV match',
            'Employer job URL': 'https://job-boards.greenhouse.io/example/jobs/123',
            'Link check': 'Form available'}]}
        self.contacts = [{'name': 'Alex Example', 'linkedin': 'https://linkedin.com/in/alex?trk=search',
            'company': 'Example', 'current_company': 'Example', 'role': 'Engineer', 'first_degree': True,
            'current_employer_verified': True, 'connection_evidence': '1st',
            'employer_evidence': 'Engineer at Example (present)', 'checked': '2026-10-23'}]
        self.history = {'has_more': False, 'results': []}
        self.sources = {'has_more': False, 'results': [{'Name': 'Referral Connections', 'Collection': 'On'}]}

    def plan(self):
        return build_plan(self.jobs, self.contacts, self.history, self.profile, self.sources, self.today)

    def test_off_source_and_incomplete_inventory_block_preparation(self):
        self.sources['results'][0]['Collection'] = 'Off'
        self.assertFalse(self.plan()['create'])
        self.history['has_more'] = True
        with self.assertRaises(ValueError): self.plan()

    def test_replay_reuses_draft_without_changing_manual_status_or_text(self):
        first = self.plan()['create'][0]
        row = dict(first['properties'], url='https://notion.so/referral', Status='Sent', **{'Message draft': 'Manual text'})
        self.history['results'] = [row]
        replay = self.plan()
        self.assertFalse(replay['create'])
        self.assertEqual(replay['reuse'][0]['page_url'], row['url'])
        self.assertEqual(row['Message draft'], 'Manual text')
        self.assertFalse(replay['sending_enabled'])

    def test_one_company_outreach_and_canonical_contact_identity(self):
        self.contacts.append(dict(self.contacts[0], name='Another Person', linkedin='https://linkedin.com/in/another'))
        self.assertEqual(len(self.plan()['create']), 1)
        self.assertEqual(request_key(self.profile['linkedin'], self.contacts[0]['linkedin'], self.jobs['results'][0]['Employer job URL']),
            request_key(self.profile['linkedin'], 'https://www.linkedin.com/in/alex/', 'https://job-boards.eu.greenhouse.io/example/jobs/123?utm_source=test'))

    def test_unverified_or_stale_connection_is_not_drafted(self):
        self.contacts[0]['current_employer_verified'] = False
        self.assertFalse(self.plan()['create'])
        self.contacts[0]['current_employer_verified'] = True
        self.contacts[0]['checked'] = '2026-09-01'
        self.assertFalse(self.plan()['create'])

    def test_sent_application_or_unreviewed_claims_are_not_used(self):
        self.jobs['results'][0]['Stage'] = 'Applied'
        self.assertFalse(self.plan()['create'])
        self.profile['intro_evidence'] = 'Invented achievement'
        with self.assertRaises(ValueError): self.plan()

    def test_other_owner_history_and_duplicate_requisitions_block_writes(self):
        first = self.plan()['create'][0]
        self.history['results'] = [dict(first['properties'], Account='https://linkedin.com/in/other', url='https://notion.so/referral')]
        with self.assertRaises(ValueError): self.plan()
        self.history['results'] = []
        self.jobs['results'].append(copy.deepcopy(self.jobs['results'][0]))
        with self.assertRaises(ValueError): self.plan()

    def test_former_employee_or_wrong_current_company_is_not_drafted(self):
        self.contacts[0]['current_company'] = 'Former Employer'
        self.assertFalse(self.plan()['create'])
        self.contacts[0]['current_company'] = ''
        self.assertFalse(self.plan()['create'])

    def test_manually_closed_job_is_not_used_for_referrals(self):
        self.jobs['results'][0]['Status'] = 'Closed'
        self.assertFalse(self.plan()['create'])
