"""Adversarial recorded-data checks; these do not rerun model inference."""
import copy
import json
from pathlib import Path
import unittest
from examples.report import verify,controls

RECORD=Path(__file__).resolve().parents[1]/'evidence/model-demo.json'

class ModelReportReviewTests(unittest.TestCase):
    def setUp(self):self.value=json.loads(RECORD.read_text())
    def reject(self,change):
        value=copy.deepcopy(self.value);change(value)
        with self.assertRaises((ValueError,KeyError,TypeError)):verify(value)
    def test_original_record(self):verify(self.value)
    def test_matched_negative_process_groups(self):
        self.reject(lambda v:v['native_model']['cases'][0]['memory'].update(daemon_pgid=-1,observer_pgid=-1))
    def test_boolean_identity(self):self.reject(lambda v:v['native_model']['cases'][0]['memory'].update(pid=True))
    def test_nonfinite_rss(self):self.reject(lambda v:v['native_model']['cases'][0]['memory'].update(sampled_max_bytes=float('inf')))
    def test_negative_disk_allocation(self):self.reject(lambda v:v['linux_model']['cases'][0]['disk'].update(root_regular_unique_inode_allocated_bytes=-1))
    def test_unknown_matching_output_digests(self):
        self.reject(lambda v:[c.update(output_sha256='UNKNOWN') for c in v['native_model']['inference']['cases']])
    def test_boolean_error_is_not_a_measurement(self):self.reject(lambda v:v['native_model']['inference']['cases'][1].update(max_abs_error=False))
    def test_unknown_provenance_digest(self):self.reject(lambda v:v['source'].update({'examples/model.json':'UNKNOWN'}))
    def test_unknown_address(self):self.reject(lambda v:v['wan'].update(provisioned_addresses=['UNKNOWN'],pinned_address='UNKNOWN'))
    def test_corpus_controls(self):self.assertTrue(controls(self.value)['all_rejected'])

if __name__=='__main__':unittest.main(verbosity=2)
