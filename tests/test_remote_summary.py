import unittest
import copy
from remote_summary import summarize_runs
KEY = 'execution_status'
STATES = ['paused', 'failed', 'uncertain_operation']
class RemoteSummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]), dict(total=0, by_status={}, needs_attention=0))
    def test_each_selected_state(self):
        self.assertEqual(summarize_runs([{KEY:s} for s in STATES]),
                         dict(total=len(STATES), by_status=dict(sorted((s,1) for s in STATES)), needs_attention=len(STATES)))
    def test_repeat_and_unknown(self):
        s=STATES[0]
        self.assertEqual(summarize_runs([{KEY:s},{KEY:s},{}]),
                         dict(total=3, by_status=dict(sorted([(s,2),('unknown',1)])), needs_attention=2))
    def test_spaces_and_case(self):
        s=STATES[0]
        self.assertEqual(summarize_runs([{KEY:' '+s+' '},{KEY:s.upper()}])['needs_attention'],1)
    def test_unknown_values(self):
        rows=[{}, {KEY:None},{KEY:True},{KEY:[]},{KEY:''},{KEY:'  '}]
        self.assertEqual(summarize_runs(rows),dict(total=6,by_status={'unknown':6},needs_attention=0))
    def test_order_unicode_and_preservation(self):
        rows=[{KEY:' zeta ', 'nested':[1]},{KEY:'á'}, {KEY:'alpha'}]
        before=copy.deepcopy(rows); result=summarize_runs(rows)
        self.assertEqual(rows,before)
        self.assertEqual(list(result['by_status']),['alpha','zeta','á'])
    def test_wrong_alias(self):
        alias='status' if KEY!='status' else 'recorded_status'
        self.assertEqual(summarize_runs([{alias:STATES[0]}])['by_status'],{'unknown':1})
    def test_non_attention(self):
        self.assertEqual(summarize_runs([{KEY:'other_state_not_selected'}])['needs_attention'],0)
    def test_bad_container(self):
        for x in [None, {}, (), 3, 'text']:
            with self.subTest(x=x), self.assertRaises(TypeError): summarize_runs(x)
    def test_bad_record(self):
        for x in [None, [], 3, 'text']:
            with self.subTest(x=x), self.assertRaises(TypeError): summarize_runs([{},x])
