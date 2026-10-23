import copy
import hashlib
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from crm.submissions import Ledger, Blocked, execute, digest
from crm.delivery import context
from crm.communication import sync_plan
from crm.email_transport import EmailTransport, read_email
from crm.schema import properties
from crm.controls import action_settings
from crm.collector import build_plan


def settings():
    return {k: {'enabled': True, 'ready': True, 'daily_limit': 5} for k in
            ['applications', 'web_apply', 'email_connect', 'email_apply', 'email_reply',
             'telegram_connect', 'telegram_apply', 'telegram_reply']}


def request(channel='web', job='1'):
    return {'channel': channel, 'job_url': 'https://jobs.lever.co/example/' + job,
            'notion_url': 'https://app.notion.com/p/' + job, 'company': 'Example',
            'recipient': 'https://jobs.lever.co/example/' + job + '/apply' if channel == 'web' else 'jobs@example.invalid',
            'account': 'candidate@example.invalid', 'cv_sha256': 'cv', 'profile_sha256': 'profile',
            'blockers': [], 'prior_application_check': 'clear', 'employer_verified': True,
            'contact_evidence': 'Verified on employer careers page', 'message_id': '<outbound@example.invalid>'}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.ledger = Ledger(self.path)
        self.switches = settings()
        self.ctx = dict(cv_hash='cv', profile_hash='profile', settings=self.switches,
                        settings_checked_at=time.time(), live_enabled=True)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def reserve(self, req=None):
        return self.ledger.reserve(req or request(), **self.ctx)

    def sent(self, req=None):
        req = req or request('email')
        ticket = self.reserve(req)
        self.ledger.begin_delivery(ticket['attempt_id'], ticket['token'], **self.ctx)
        self.ledger.finish(ticket['attempt_id'], ticket['token'],
            {'remote_id': req['message_id'], 'channel': req['channel'], 'recipient': req['recipient']})
        return ticket

    def event(self, timestamp=100):
        return {'channel': 'email', 'account': 'candidate@example.invalid', 'remote_id': '<inbound@example.invalid>',
                'sender': 'jobs@example.invalid', 'in_reply_to': ['<outbound@example.invalid>'],
                'text': 'Unfortunately we cannot sponsor a work visa.', 'occurred_at': timestamp}

    def classification(self):
        return {'outcome': 'Rejection', 'evidence': 'cannot sponsor a work visa',
                'reason': 'Work authorization / visa', 'reason_evidence': 'cannot sponsor a work visa'}

    def test_test_mode_never_calls_transport_or_reserves(self):
        transport = Mock()
        result = execute(self.ledger, request(), lambda: (self.switches, time.time()), transport,
                         live_enabled=False, cv_hash='cv', profile_hash='profile')
        self.assertEqual(result['state'], 'dry_run')
        transport.send.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    def test_duplicate_blocked_across_channels_even_when_uncertain(self):
        t = self.reserve()
        self.ledger.begin_delivery(t['attempt_id'], t['token'], **self.ctx)
        self.ledger.finish(t['attempt_id'], t['token'], error='TimeoutError')
        for channel in ['web', 'email', 'telegram']:
            with self.assertRaises(Blocked):
                self.reserve(request(channel))

    def test_same_crm_job_cannot_be_applied_twice_with_different_urls(self):
        self.reserve()
        with self.assertRaises(Blocked):
            self.reserve(dict(request('email','different-id'),notion_url=request()['notion_url']))

    def test_changed_cv_profile_unknown_history_and_unverified_route_block(self):
        for values in [{'cv_sha256': 'changed'}, {'profile_sha256': 'changed'},
                       {'prior_application_check': 'unknown'}, {'employer_verified': False},
                       {'blockers': ['Phone']}, {'recipient': 'https://other.invalid/job'}]:
            with self.assertRaises(Blocked):
                self.reserve(dict(request(), **values))

    def test_switch_off_after_reservation_prevents_delivery(self):
        t = self.reserve()
        self.switches['web_apply']['enabled'] = False
        with self.assertRaises(Blocked):
            self.ledger.begin_delivery(t['attempt_id'], t['token'], **self.ctx)

    def test_stale_settings_and_truthy_non_boolean_live_flag_are_safe(self):
        with self.assertRaises(Blocked):
            self.ledger.reserve(request(), **dict(self.ctx, settings_checked_at=time.time()-61))
        result = self.ledger.reserve(request(), **dict(self.ctx, live_enabled='false'))
        self.assertEqual(result['state'], 'dry_run')

    def test_recheck_before_form_submit_detects_disabled_channel(self):
        t = self.reserve()
        self.ledger.begin_delivery(t['attempt_id'], t['token'], **self.ctx)
        self.switches['applications']['enabled'] = False
        with self.assertRaises(Blocked):
            self.ledger.check_delivery(t['attempt_id'], t['token'], **self.ctx)

    def test_daily_limit_shared_across_channels(self):
        for i in range(5):
            self.reserve(request(['web', 'email', 'telegram'][i%3], str(i)))
        with self.assertRaises(Blocked):
            self.reserve(request('email', 'sixth'))

    def test_reservation_cannot_carry_yesterdays_slot_across_midnight(self):
        from datetime import datetime,timezone
        before=datetime(2026,10,23,23,59,30,tzinfo=timezone.utc).timestamp()
        t=self.ledger.reserve(request(),**dict(self.ctx,now=before,settings_checked_at=before))
        with self.assertRaisesRegex(Blocked,'different local day'):
            self.ledger.begin_delivery(t['attempt_id'],t['token'],**dict(self.ctx,now=before+45,settings_checked_at=before+45))

    def test_concurrent_workers_reserve_a_job_once(self):
        def worker(_):
            ledger = Ledger(self.path)
            try:
                try:
                    ledger.reserve(request(), **self.ctx)
                    return 'reserved'
                except Blocked:
                    return 'blocked'
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(worker, range(2))), ['blocked', 'reserved'])

    def test_timeout_is_uncertain_and_recovery_requires_stopped_executor(self):
        t = self.reserve()
        self.ledger.begin_delivery(t['attempt_id'], t['token'], **self.ctx)
        with self.assertRaises(Blocked):
            self.ledger.recover()
        self.assertEqual(self.ledger.recover(executor_stopped=True), 1)
        with self.assertRaises(Blocked):
            self.reserve()

    def test_wrong_recipient_receipt_does_not_mark_applied(self):
        t = self.reserve()
        self.ledger.begin_delivery(t['attempt_id'], t['token'], **self.ctx)
        self.assertEqual(self.ledger.finish(t['attempt_id'], t['token'],
            {'remote_id': 'x', 'channel': 'web', 'recipient': 'other'}), 'unknown')

    def test_response_exact_match_idempotent_and_reason_evidenced(self):
        self.sent()
        self.assertEqual(self.ledger.ingest(self.event(), self.classification()), 'matched')
        self.assertEqual(self.ledger.ingest(self.event(), self.classification()), 'duplicate')
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM incoming').fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.ledger.ingest(self.event(), dict(self.classification(), reason_evidence='not in message'))

    def test_untrusted_sender_account_and_unmatched_response_go_to_review(self):
        self.sent()
        for i, update in enumerate([{'sender': 'someone@example.invalid'}, {'account': 'other@example.invalid'}, {'in_reply_to': []}]):
            self.assertEqual(self.ledger.ingest(dict(self.event(), remote_id=str(i), **update), self.classification()), 'manual_review')

    def test_old_response_preserves_latest_summary_but_keeps_history(self):
        self.sent()
        self.ledger.ingest(self.event(200), self.classification())
        self.ledger.ingest(dict(self.event(100), remote_id='older'), self.classification())
        events = [r for r in self.ledger.pending_sync() if r['type']=='response']
        self.assertEqual([e['update_summary'] for e in events], [True, False])

    def test_reply_has_separate_switch_and_idempotency_key(self):
        self.sent()
        self.ledger.ingest(self.event(), self.classification())
        parent = self.ledger.db.execute('SELECT event_key FROM incoming').fetchone()[0]
        req = {'parent_event_key': parent, 'channel': 'email', 'account': 'candidate@example.invalid',
               'recipient': 'jobs@example.invalid', 'in_reply_to': '<inbound@example.invalid>', 'text': 'Thank you.', 'reviewed': True}
        self.switches['applications']['enabled'] = False
        ctx = dict(settings=self.switches, live_enabled=True, settings_checked_at=time.time())
        self.switches['email_reply']['enabled'] = False
        with self.assertRaises(Blocked):
            self.ledger.reserve_reply(req, **ctx)
        self.switches['email_reply']['enabled'] = True
        t = self.ledger.reserve_reply(req, **ctx)
        self.ledger.begin_reply(t, **ctx)
        self.ledger.finish_reply(t, {'channel': 'email', 'recipient': req['recipient'], 'remote_id': 'reply-id'})
        with self.assertRaises(Blocked):
            self.ledger.reserve_reply(req, **ctx)

    def test_sync_preserves_manual_stage_and_replays_without_duplicate_history(self):
        event = {'event_key': 'x', 'type': 'delivery', 'stage': 'Applied', 'notion_url': 'n', 'receipt': {'remote_id':'1'}}
        plan = sync_plan(event, {'Stage':'Interview'}, history_records={'results':[], 'has_more':False})
        self.assertNotIn('Stage', plan['properties'])
        history = dict(plan['history']['properties'], url='saved', **{'Sync state':'Applied'})
        self.assertEqual(sync_plan(event, {}, history_records={'results':[history], 'has_more':False})['action'], 'already_synced')


class IntegrationTests(unittest.TestCase):
    def test_english_switches_and_collection_replay(self):
        row = properties({'Ключ': 'web_apply', 'Режим': 'Включено', 'Готовность': 'Готово'})
        self.assertEqual(action_settings({'has_more':False,'results':[row]})['web_apply'],
                         {'enabled':True,'ready':True,'daily_limit':None})
        old = {'Вакансия':'Java Engineer','Источник':'Сайт компании','Вакансия компании':'https://jobs.lever.co/example/a',
               'Ссылка для отклика':'https://jobs.lever.co/example/a/apply','Исходная публикация':'https://example.invalid/a','Проверка ссылки':'Форма доступна'}
        candidate = {'properties':properties(old),'reviewed':True,'direct_verified':True,'employer_open_verified':True,'sources':[]}
        sources = {'has_more':False,'results':[{'Name':'Company websites','Collection':'On'}]}
        plan = build_plan({'has_more':False,'results':[]}, [candidate], sources)
        self.assertEqual(len(plan['create']),1)
        saved = dict(plan['create'][0]['properties'],url='https://notion.invalid/saved',Stage='Interview')
        self.assertEqual(build_plan({'has_more':False,'results':[saved]},[candidate],sources)['create'],[])

    def test_runtime_requires_workspace_freshness_and_actual_cv(self):
        snap = {'data_source_id':'own','checked_at':time.time(),'has_more':False,'results':[]}
        cfg = {'notion':{'actions_data_source_id':'own'},'delivery':{'live_sending':False}}
        result = context(cfg,snap,b'actual CV',{'name':'Example'})
        self.assertFalse(result['live_enabled'])
        self.assertEqual(result['cv_hash'],hashlib.sha256(b'actual CV').hexdigest())
        with self.assertRaises(Blocked):
            context(cfg,dict(snap,data_source_id='another'),b'cv',{})

    def test_smtp_builds_one_message_and_requires_persisted_id(self):
        client = Mock(); client.send_message.return_value = {}
        factory = Mock(); factory.return_value.__enter__ = Mock(return_value=client)
        factory.return_value.__exit__ = Mock(return_value=False)
        cfg = {'sender':'candidate@example.invalid','smtp_host':'smtp.example.invalid','username_env':'TEST_USER','password_env':'TEST_PASSWORD'}
        req = dict(request('email'), text='Short cover letter.', role='Java Engineer', candidate_name='Example Engineer',cv_url='https://example.invalid/cv')
        with patch.dict('os.environ',{'TEST_USER':'test','TEST_PASSWORD':'test'}):
            receipt = EmailTransport(cfg,factory).send(req)
        self.assertEqual(receipt['remote_id'],req['message_id'])
        self.assertEqual(client.send_message.call_count,1)
        del req['message_id']
        factory.reset_mock()
        with self.assertRaises(ValueError):
            EmailTransport(cfg,factory).send(req)
        factory.assert_not_called()

    def test_disabled_inbox_never_connects(self):
        factory=Mock()
        self.assertEqual(read_email({},enabled=False,imap_factory=factory),[])
        factory.assert_not_called()

    def test_imap_is_readonly_and_uidvalidity_resets_the_cursor(self):
        from email.message import EmailMessage
        mail=EmailMessage();mail['From']='jobs@example.invalid';mail['Message-ID']='<incoming@example.invalid>'
        mail['Date']='Fri, 23 Oct 2026 12:00:00 +0000';mail['In-Reply-To']='<outbound@example.invalid>'
        mail.set_content('<p>Interview invitation</p>',subtype='html')
        client=Mock();client.select.return_value=('OK',[]);client.response.return_value=('UIDVALIDITY',[b'new-validity'])
        client.uid.side_effect=lambda op,*args: ('OK',[b'1']) if op=='search' else ('OK',[(b'1',mail.as_bytes())])
        factory=Mock();factory.return_value.__enter__=Mock(return_value=client);factory.return_value.__exit__=Mock(return_value=False)
        cfg={'sender':'candidate@example.invalid','imap_host':'imap.example.invalid','username_env':'TEST_USER','password_env':'TEST_PASSWORD'}
        with patch.dict('os.environ',{'TEST_USER':'test','TEST_PASSWORD':'test'}):
            events=read_email(cfg,{'uid':99,'uidvalidity':'old-validity'},enabled=True,imap_factory=factory)
        client.select.assert_called_once_with('INBOX',readonly=True)
        self.assertEqual(client.uid.call_args_list[0].args,('search',None,'UID','1:*'))
        self.assertEqual(client.uid.call_args_list[1].args[-1],'(BODY.PEEK[])')
        self.assertEqual(events[0]['cursor'],{'uid':1,'uidvalidity':'new-validity'})
        self.assertEqual(events[0]['in_reply_to'],['<outbound@example.invalid>'])
        self.assertIn('Interview invitation',events[0]['text'])

    def test_unsupported_html_messages_are_kept_without_executing_links(self):
        from email.message import EmailMessage
        mail=EmailMessage();mail['From']='jobs@example.invalid';mail['Date']='Fri, 23 Oct 2026 12:00:00 +0000'
        mail.set_content('<a href="https://example.invalid">Click</a>',subtype='html')
        client=Mock();client.select.return_value=('OK',[]);client.response.return_value=('UIDVALIDITY',[b'v1'])
        client.uid.side_effect=lambda op,*args: ('OK',[b'2']) if op=='search' else ('OK',[(b'2',mail.as_bytes())])
        factory=Mock();factory.return_value.__enter__=Mock(return_value=client);factory.return_value.__exit__=Mock(return_value=False)
        cfg={'sender':'candidate@example.invalid','imap_host':'imap.example.invalid','username_env':'TEST_USER','password_env':'TEST_PASSWORD'}
        with patch.dict('os.environ',{'TEST_USER':'test','TEST_PASSWORD':'test'}):
            events=read_email(cfg,enabled=True,imap_factory=factory)
        self.assertEqual(events[0]['remote_id'],'INBOX:v1:2')
        self.assertIn('https://example.invalid',events[0]['text'])


if __name__ == '__main__':
    unittest.main()
