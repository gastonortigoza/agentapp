import unittest
import copy
from attention_summary import summarize_runs

class AttentionTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]), {'total':0,'by_status':{},'needs_attention':0})
    def test_all_attention_states(self):
        states=['failed','uncertain','blocked_evidence','blocked_budget','uncertain_operation']
        self.assertEqual(summarize_runs([{'recorded_status':s} for s in states]),
                         {'total':5,'by_status':dict(sorted((s,1) for s in states)),'needs_attention':5})
    def test_repeated_and_mixed(self):
        result=summarize_runs([{'recorded_status':s} for s in ['failed','failed','delivered','planning']])
        self.assertEqual(result,{'total':4,'by_status':{'delivered':1,'failed':2,'planning':1},'needs_attention':2})
    def test_normalization_and_case(self):
        self.assertEqual(summarize_runs([{'recorded_status':s} for s in [' failed ','Failed',' ']]),
                         {'total':3,'by_status':{'Failed':1,'failed':1,'unknown':1},'needs_attention':1})
    def test_unknown_types_and_alias(self):
        rows=[{}, {'recorded_status':None},{'recorded_status':True},{'recorded_status':[]},{'status':'failed'}]
        self.assertEqual(summarize_runs(rows),{'total':5,'by_status':{'unknown':5},'needs_attention':0})
    def test_sorted_unicode_and_input_preserved(self):
        rows=[{'recorded_status':' revisión ','nested':[1,2]},{'recorded_status':'failed'}]
        saved=copy.deepcopy(rows);result=summarize_runs(rows)
        self.assertEqual(rows,saved)
        self.assertEqual(list(result['by_status']),['failed','revisión'])
        self.assertEqual(result['needs_attention'],1)
    def test_bad_container(self):
        for value in [None,{},'text',(),42]:
            with self.subTest(value=value),self.assertRaises(TypeError): summarize_runs(value)
    def test_bad_record(self):
        for value in [None,[],3,'failed']:
            with self.subTest(value=value),self.assertRaises(TypeError): summarize_runs([{},value])
