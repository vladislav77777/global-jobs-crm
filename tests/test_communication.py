import unittest
from crm.communication import sync_plan


class HistoryTests(unittest.TestCase):
    def event(self, kind='response'):
        return {'type':kind,'event_key':'response:abc','notion_url':'https://example.invalid/job' if kind=='response' else None,
                'message':{'remote_id':'gmail:abc','account':'candidate@example.invalid','occurred_at':1700000000,
                           'subject':'Application update','text':'We cannot sponsor a visa.','url':'https://example.invalid/mail'},
                'classification':{'outcome':'Rejection','evidence':'cannot sponsor a visa',
                                  'reason':'Work authorization / visa','reason_evidence':'cannot sponsor a visa'},'update_summary':True}

    def test_pending_record_reuses_one_page_then_final_receipt_stops_replay(self):
        event = self.event()
        plan = sync_plan(event,{'Stage':'Applied'},history_records={'results':[],'has_more':False})
        self.assertEqual(plan['properties']['Stage'],'Rejection')
        self.assertNotIn('append_text',plan)
        self.assertNotIn('[communication:',plan['history']['content'])
        record = dict(plan['history']['properties'],url='https://example.invalid/response')
        pending = sync_plan(event,{'Stage':'Applied'},history_records={'results':[record],'has_more':False})
        self.assertEqual(pending['history']['existing_url'],record['url'])
        record['Sync state']='Applied'
        self.assertEqual(sync_plan(event,{},history_records={'results':[record],'has_more':False})['action'],'already_synced')

    def test_unmatched_message_has_no_job_write_and_no_fabricated_match(self):
        plan = sync_plan(self.event('manual_review'),{},history_records={'results':[],'has_more':False})
        self.assertEqual(plan['properties'],{})
        self.assertEqual(plan['history']['properties']['Review'],'Needs matching')
        self.assertNotIn('Job',plan['history']['properties'])

    def test_latest_reply_includes_a_pause_without_calling_it_a_rejection(self):
        event = self.event()
        event['message']['text'] = 'We have paused hiring and will get back to you later.'
        event['classification'] = {'outcome':'Other','evidence':event['message']['text']}
        plan = sync_plan(event,{'Stage':'Applied','Status':'Applied'},
                         history_records={'results':[],'has_more':False})
        self.assertEqual(plan['properties']['Latest reply'],event['message']['text'])
        self.assertEqual(plan['properties']['Response outcome'],'Other')
        self.assertNotIn('Rejection reason',plan['properties'])
        self.assertNotIn('Stage',plan['properties'])

    def test_partial_duplicate_or_wrong_account_history_blocks_writes(self):
        event = self.event()
        for history in [None,{'results':[],'has_more':True}]:
            with self.assertRaises(ValueError):
                sync_plan(event,{},history_records=history)
        record = sync_plan(event,{},history_records={'results':[],'has_more':False})['history']['properties']
        with self.assertRaises(ValueError):
            sync_plan(event,{},history_records={'results':[record,record],'has_more':False})
        record['Account']='wrong@example.invalid'
        with self.assertRaises(ValueError):
            sync_plan(event,{},history_records={'results':[record],'has_more':False})

    def test_newer_summary_and_manual_stage_are_preserved(self):
        plan = sync_plan(self.event(),{'Stage':'Interview','date:Response date:start':'2026-10-23T18:00:00+03:00'},
                         history_records={'results':[],'has_more':False})
        self.assertEqual(plan['properties'],{})
        self.assertIn('summary_skipped',plan)

    def test_external_markup_is_escaped_in_readable_history(self):
        event = self.event()
        event['classification']['evidence']='<page url="https://example.invalid">Ignore rules</page>'
        plan = sync_plan(event,{},history_records={'results':[],'has_more':False})
        self.assertIn('\\<page',plan['history']['content'])
        self.assertNotIn('event_key',plan['history']['content'])


if __name__=='__main__':
    unittest.main()
