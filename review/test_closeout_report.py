"""Validator regression checks against the unchanged measured closeout record.

These tests are separate from the historical 74-test runtime cohort.
"""
import copy
import json
from pathlib import Path
import statistics
import unittest

from closeout.report import POLICY, controls, verify


class CloseoutReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=json.loads((Path(__file__).resolve().parents[1]/'evidence/closeout.json').read_text())

    def setUp(self):
        self.value=copy.deepcopy(self.original)
        self.closeout=self.value['closeout']

    def rejected(self):
        with self.assertRaises((ValueError,KeyError,TypeError)):
            verify(self.value)

    def summarize(self):
        samples=self.closeout['samples'];soak=self.closeout['soak']
        for name,window in [('early_medians',POLICY['early_window']),('late_medians',POLICY['late_window'])]:
            soak[name]={f:statistics.median(x[f] for x in samples if window[0]<=x['seconds']<window[1]) for f in ('rss','fd','threads')}
        windows=[statistics.median(x['rss'] for x in samples if a<=x['seconds']<a+30) for a in range(30,180,30)]
        streak=peak=0
        for a,b in zip(windows,windows[1:]):
            streak=streak+1 if b-a>POLICY['rss_window_step_bytes'] else 0
            peak=max(peak,streak)
        soak.update(rss_window_medians=windows,largest_growing_streak=peak)

    def test_original_measurement_and_controls(self):
        verify(self.value)
        result=controls(self.value)
        self.assertTrue(result['all_rejected'])
        self.assertEqual(self.value,self.original)

    def test_sparse_five_window_trace_with_consistent_summaries(self):
        samples=self.closeout['samples']
        self.closeout['samples']=[dict(copy.deepcopy(samples[0]),seconds=t) for t in (35,65,95,125,155) for _ in range(120)]
        self.summarize()
        self.rejected()

    def test_nonfinite_times_and_duration(self):
        for value in (float('nan'),float('inf'),-float('inf')):
            for target in ('sample','duration'):
                with self.subTest(value=value,target=target):
                    self.setUp()
                    if target=='sample':self.closeout['samples'][0]['seconds']=value
                    else:self.closeout['soak']['seconds']=value
                    self.rejected()

    def test_duplicate_and_reversed_times(self):
        for delta in (0,-0.01):
            with self.subTest(delta=delta):
                self.setUp()
                self.closeout['samples'][1]['seconds']=self.closeout['samples'][0]['seconds']+delta
                self.rejected()

    def test_missing_start_end_and_interior_coverage(self):
        for gap in ('start','end','interior'):
            with self.subTest(gap=gap):
                self.setUp()
                self.closeout['samples']=[x for x in self.closeout['samples'] if
                    not (gap=='start' and x['seconds']<2 or
                         gap=='end' and x['seconds']>178 or
                         gap=='interior' and 80<x['seconds']<82)]
                self.assertGreaterEqual(len(self.closeout['samples']),600)
                self.summarize()
                self.rejected()

    def test_missing_or_malformed_epoch(self):
        for epoch in (None,'invalid','00000000-0000-0000-0000-000000000000'):
            with self.subTest(epoch=epoch):
                self.setUp()
                if epoch is None:self.closeout['soak'].pop('epoch')
                else:self.closeout['soak']['epoch']=epoch
                self.rejected()

    def test_invalid_or_changed_invocation(self):
        for invocation in ('','UNKNOWN','0'*32,'f'*32):
            with self.subTest(invocation=invocation):
                self.setUp()
                self.closeout['soak']['after']['InvocationID']=invocation
                self.rejected()
        self.setUp()
        for side in ('before','after'):self.closeout['soak'][side]['InvocationID']='invalid'
        self.rejected()

    def test_only_runtime_unfinished_request_state_is_accepted(self):
        for state in ('UNKNOWN','SUCCEEDED','FAILED','INTERRUPTED','DOWNLOADING','QUEUED',None):
            with self.subTest(state=state):
                self.closeout['disk_full']['state_under_fault']=state
                self.rejected()

    def test_negative_resources_with_consistent_summaries(self):
        for field in ('rss','fd','threads'):
            with self.subTest(field=field):
                self.setUp()
                for sample in self.closeout['samples']:sample[field]=-1
                self.summarize()
                self.rejected()

    def test_resource_and_process_domains(self):
        for field,value in [('rss',0),('threads',0),('fd',0),('fd',-1),('fd',True),('rss',float('inf')),('threads',1.5),('start_ticks','0'),('start_ticks','-1'),('start_ticks','NaN'),('pid',True)]:
            with self.subTest(field=field,value=value):
                self.setUp()
                self.closeout['samples'][0][field]=value
                self.rejected()
        self.setUp()
        self.closeout['soak']['pid']=0
        for sample in self.closeout['samples']:sample['pid']=0
        for side in ('before','after'):self.closeout['soak'][side]['MainPID']='0'
        self.rejected()

    def test_minimal_positive_fd_is_valid(self):
        for sample in self.closeout['samples']:sample['fd']=1
        self.summarize()
        verify(self.value)


if __name__=='__main__':
    unittest.main()
