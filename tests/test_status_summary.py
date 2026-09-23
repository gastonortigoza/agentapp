import unittest
import copy
from status_summary import summarize_runs

class SummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_runs([]),{'total':0,'by_status':{}})
    def test_counts(self):
        records=[{'recorded_status':'pass'},{'recorded_status':'error'},{'recorded_status':'pass'}]
        self.assertEqual(summarize_runs(records),{'total':3,'by_status':{'error':1,'pass':2}})
    def test_unknown(self):
        records=[{}, {'recorded_status':None},{'recorded_status':[]},{'recorded_status':23}]
        self.assertEqual(summarize_runs(records),{'total':4,'by_status':{'unknown':4}})
    def test_whitespace(self):
        self.assertEqual(summarize_runs([{'recorded_status':'  pass  '},{'recorded_status':' '}]),{'total':2,'by_status':{'pass':1,'unknown':1}})
    def test_sorted(self):
        result=summarize_runs([{'recorded_status':'z'},{'recorded_status':'a'}])
        self.assertEqual(list(result['by_status']),['a','z'])
    def test_unchanged(self):
        records=[{'recorded_status':' pass ','nested':[1,2]},{}]
        previous=copy.deepcopy(records)
        summarize_runs(records)
        self.assertEqual(records,previous)
    def test_bad_container(self):
        for records in [None,{},'text',(),42]:
            with self.subTest(records=records),self.assertRaises(TypeError): summarize_runs(records)
    def test_bad_entry(self):
        for record in [None,[],23,'pass']:
            with self.subTest(record=record),self.assertRaises(TypeError): summarize_runs([record])
    def test_mixed_bad_entry(self):
        with self.assertRaises(TypeError):summarize_runs([{},7])
    def test_does_not_use_status_alias(self):
        self.assertEqual(summarize_runs([{'status':'pass'}]),{'total':1,'by_status':{'unknown':1}})
    def test_boolean_status(self):
        self.assertEqual(summarize_runs([{'recorded_status':True}]),{'total':1,'by_status':{'unknown':1}})
    def test_unicode_and_extra_fields(self):
        self.assertEqual(summarize_runs([{'recorded_status':' revisión ','ignored':'x'}]),{'total':1,'by_status':{'revisión':1}})
