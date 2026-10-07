import unittest
from src.crypto_ledger import reconcile

def row(party='42', address='0x' + '0' * 40):
    return dict(party_id=party, symbol='ETH', network='ethereum', address=address,
                normalized_address=address, entity_name='EXAMPLE', program='TEST',
                feature_id='77', version_id='78', validation='FORMAT_ONLY', review_reason='')

class LedgerTests(unittest.TestCase):
    def test_validation_updates_are_audited_separately_from_official_changes(self):
        original=row()
        rows,_=reconcile([], [original], '2026-10-07T00:00:00Z','a'*64,'1')
        original_event=rows[0]['last_event_id']
        verified=dict(original,validation='CHECKSUM_VALID',review_reason='',validation_version='2',validation_method='EIP55')
        updated,events=reconcile(rows,[verified],'2026-10-07T01:00:00Z','a'*64,'1')
        self.assertEqual([e['kind'] for e in events],['REVALIDATED'])
        self.assertEqual(updated[0]['relation_id'],rows[0]['relation_id'])
        self.assertEqual(updated[0]['first_seen'],rows[0]['first_seen'])
        self.assertEqual(updated[0]['last_event_id'],original_event)
        self.assertEqual(events[0]['before_validation'],'FORMAT_ONLY')
        self.assertEqual(events[0]['after_validation'],'CHECKSUM_VALID')
        _,again=reconcile(updated,[verified],'2026-10-07T02:00:00Z','a'*64,'1')
        self.assertEqual(again,[])

    def test_candidate_only_update_is_audited_and_replay_does_not_duplicate(self):
        original=dict(row(),validation_version='3',network_candidates=[],network_resolution='UNRESOLVED')
        rows,_=reconcile([], [original], '2026-10-07T00:00:00Z','a'*64,'1')
        incoming=dict(original,network_candidates=['ethereum'],network_resolution='SYMBOL_AND_FORMAT')
        updated,events=reconcile(rows,[incoming],'2026-10-07T01:00:00Z','a'*64,'1')
        self.assertEqual(events[0]['kind'],'REVALIDATED')
        self.assertEqual(events[0]['before_candidates'],[])
        self.assertEqual(events[0]['after_candidates'],['ethereum'])
        self.assertEqual(updated[0]['last_event_id'],rows[0]['last_event_id'])
        self.assertEqual(reconcile(updated,[incoming],'2026-10-07T02:00:00Z','a'*64,'1')[1],[])

    def test_official_name_change_is_not_hidden_by_validation_update(self):
        original=row()
        rows,_=reconcile([], [original], '2026-10-07T00:00:00Z','a'*64,'1')
        incoming=dict(original,entity_name='NEW NAME',validation='CHECKSUM_VALID',validation_version='2')
        _,events=reconcile(rows,[incoming],'2026-10-07T01:00:00Z','b'*64,'1')
        self.assertEqual(events[0]['kind'],'CHANGED')
    def test_initial_is_baseline_and_repeat_does_not_duplicate(self):
        rows, events = reconcile([], [row()], '2026-10-07T00:00:00Z', 'a'*64, '1')
        self.assertEqual(events[0]['kind'], 'BASELINED')
        repeat, again = reconcile(rows, [row()], '2026-10-07T01:00:00Z', 'a'*64, '1')
        self.assertEqual(again, [])
        self.assertEqual(repeat[0]['first_seen'], '2026-10-07T00:00:00Z')

    def test_removal_retained_and_reappearance_is_new_event(self):
        rows, _ = reconcile([], [row()], '2026-10-07T00:00:00Z', 'a'*64, '1')
        removed, events = reconcile(rows, [], '2026-10-07T01:00:00Z', 'b'*64, '1')
        self.assertEqual(removed[0]['listing_status'], 'REMOVAL_REVIEW')
        self.assertEqual(events[0]['kind'], 'REMOVAL_CANDIDATE')
        _, again = reconcile(removed, [], '2026-10-07T02:00:00Z', 'b'*64, '1')
        self.assertEqual(again, [])
        restored, events2 = reconcile(removed, [row()], '2026-10-07T03:00:00Z', 'c'*64, '1')
        self.assertEqual(restored[0]['listing_status'], 'LISTED')
        self.assertEqual(events2[0]['kind'], 'RELISTED')
        self.assertNotEqual(events[0]['event_id'], events2[0]['event_id'])

    def test_multiple_parties_are_distinct_relations(self):
        rows, _ = reconcile([], [row('42'), row('43')], '2026-10-07T00:00:00Z', 'a'*64, '1')
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['relation_id'], rows[1]['relation_id'])

    def test_parser_upgrade_classifies_new_rows_as_backfill(self):
        rows, _ = reconcile([], [row()], '2026-10-07T00:00:00Z', 'a'*64, '1')
        _, events = reconcile(rows, [row(), row('43')], '2026-10-07T01:00:00Z', 'a'*64, '2')
        self.assertEqual(events[0]['kind'], 'BACKFILLED')

    def test_removed_old_parser_row_does_not_hide_later_real_addition(self):
        rows,_=reconcile([], [row('42'),row('43')], '2026-10-07T00:00:00Z','a'*64,'1')
        removed,_=reconcile(rows,[row('42')],'2026-10-07T01:00:00Z','b'*64,'1')
        upgraded,_=reconcile(removed,[row('42')],'2026-10-07T02:00:00Z','b'*64,'2')
        _,events=reconcile(upgraded,[row('42'),row('44')],'2026-10-07T03:00:00Z','c'*64,'2')
        self.assertEqual(events[0]['kind'],'ADDED')
