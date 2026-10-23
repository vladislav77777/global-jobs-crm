import unittest
from crm.status import effective_stage, public_status
from crm.communication import sync_plan


class StatusTests(unittest.TestCase):
    def test_manual_closed_status_is_not_regressed_by_delivery(self):
        event = {'type': 'delivery', 'event_key': 'delivery:1', 'stage': 'Applied',
                 'receipt': {'remote_id': '1'}, 'occurred_at': 100}
        p = sync_plan(event, {'Stage': 'CV match', 'Status': 'Closed'}, history_records={'results': [], 'has_more': False})
        self.assertNotIn('Status', p['properties'])
        self.assertNotIn('Stage', p['properties'])

    def test_automatic_status_follows_verified_response(self):
        event = {'type': 'response', 'event_key': 'reply:1', 'update_summary': True,
                 'message': {'account': 'example', 'remote_id': '1', 'occurred_at': 100},
                 'classification': {'outcome': 'Rejection', 'evidence': 'Position closed'}}
        p = sync_plan(event, {'Stage': 'Applied', 'Status': 'Applied'}, history_records={'results': [], 'has_more': False})
        self.assertEqual(p['properties']['Status'], 'Closed')
        self.assertEqual(p['properties']['Stage'], 'Rejection')

    def test_manual_in_progress_survives_receipt_and_legacy_snapshots_work(self):
        self.assertEqual(effective_stage({'Stage': 'Applied', 'Status': 'In progress'}), 'Conversation')
        self.assertEqual(effective_stage({'Stage': 'Interview'}), 'Interview')
        self.assertEqual(public_status('Delivery uncertain'), 'Needs attention')
