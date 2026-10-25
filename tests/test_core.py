import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from cacheweft import Cache,Spec,CacheError
from cacheweft.http import HTTPS
from .fixture import Origin,DATA


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.origin=Origin(Path(cls.tmp.name)/'tls')

    @classmethod
    def tearDownClass(cls):
        cls.origin.close();cls.tmp.cleanup()

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.root=Path(self.dir.name)/'cache'
        self.origin.mode='normal';self.origin.calls.clear();self.origin.sent=0
        self.origin.release.clear();self.origin.arrived.clear()
        self.digest=hashlib.sha256(DATA).hexdigest()
        self.spec=Spec('model-v1',self.origin.url,len(DATA),self.digest)
        self.caches=[]

    def tearDown(self):
        self.origin.release.set()
        for cache in self.caches:cache.close()
        self.dir.cleanup()

    def cache(self,**kw):
        c=Cache(self.root,[self.spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert),**kw)
        self.caches.append(c);return c

    def success(self,c,request='r'):
        c.submit(request,'model-v1');v=c.wait(request)
        self.assertEqual(v['request']['state'],'SUCCEEDED')
        with c.read(self.digest) as f:self.assertEqual(f.read(),DATA)
        return v

    def test_https_verified_content(self):
        self.success(self.cache());self.assertEqual(len(self.origin.calls),1)

    def test_tls_and_origin_restriction(self):
        t=HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert)
        with self.assertRaises(ValueError):t.get('https://example.com/file',{})
        with self.assertRaises(Exception):HTTPS(self.origin.origin,'127.0.0.1').get(self.origin.url,{})

    def test_resume_real_range(self):
        self.origin.mode='drop';self.success(self.cache())
        self.assertEqual([x['range'] for x in self.origin.calls],[None,'bytes=131072-'])
        self.assertEqual(self.origin.calls[1]['if_range'],'"v1"')
        self.assertTrue(all(x['encoding']=='identity' for x in self.origin.calls))

    def test_wrong_hash_not_published(self):
        self.origin.mode='corrupt';c=self.cache();c.submit('r','model-v1')
        self.assertEqual(c.wait('r')['request']['state'],'FAILED')
        with self.assertRaises(CacheError):
            with c.read(self.digest):pass

    def test_encoded_body_refused(self):
        self.origin.mode='encoded';c=self.cache();c.submit('r','model-v1')
        self.assertEqual(c.wait('r')['request']['state'],'FAILED')

    def test_wrong_length_refused(self):
        self.origin.mode='wrong_length';c=self.cache();c.submit('r','model-v1')
        self.assertEqual(c.wait('r')['request']['state'],'FAILED')

    def test_reader_and_job_limits(self):
        self.origin.mode='hold';c=self.cache(jobs=1,readers=1);c.submit('r','model-v1')
        self.assertTrue(self.origin.arrived.wait(3));c.specs['other']=Spec('other',self.origin.url,len(DATA),'0'*64)
        with self.assertRaises(CacheError):c.submit('second','other')
        self.origin.release.set();c.wait('r')
        with c.read(self.digest):
            with self.assertRaises(CacheError):
                with c.read(self.digest):pass

    def checkpoint_child(self,point):
        code='''import os,sys
from cacheweft import Cache,Spec
from cacheweft.http import HTTPS
root,url,origin,cert,digest,point=sys.argv[1:]
def hook(p,i):
 if p==point:os._exit(73)
c=Cache(root,[Spec('model-v1',url,1048576,digest)],HTTPS(origin,'127.0.0.1',cert),hook=hook)
c.submit('r','model-v1');c.wait('r');os._exit(74)
'''
        r=subprocess.run([sys.executable,'-c',code,str(self.root),self.origin.url,self.origin.origin,self.origin.cert,self.digest,point],timeout=15,capture_output=True,text=True)
        self.assertEqual(r.returncode,73,r.stderr)

    def test_crash_checkpoints_and_publish_windows(self):
        for point in ('after_file_sync','after_checkpoint','sealed','verified','publish_intent','after_link','after_unlink_stage','after_ready'):
            with self.subTest(point=point):
                self.root=Path(self.dir.name)/point;self.checkpoint_child(point)
                c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'SUCCEEDED')
                with c.read(self.digest) as f:self.assertEqual(f.read(),DATA)
                self.assertEqual(c.usage()['remaining_reserved'],0)

    def test_range_ignored_restarts(self):
        self.checkpoint_child('after_checkpoint');self.origin.calls.clear();self.origin.mode='ignore'
        c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'SUCCEEDED')
        self.assertEqual(self.origin.calls[0]['range'],'bytes=65536-')
        with c.read(self.digest) as f:self.assertEqual(f.read(),DATA)

    def test_changed_validator_restarts(self):
        self.checkpoint_child('after_checkpoint');self.origin.calls.clear();self.origin.mode='changed'
        c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'SUCCEEDED')

    def test_bad_range_refused(self):
        self.checkpoint_child('after_checkpoint');self.origin.mode='bad_range'
        c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'FAILED')

    def test_416_not_success(self):
        self.checkpoint_child('after_checkpoint');self.origin.mode='416'
        c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'FAILED')

    def test_weak_or_absent_etag_restarts(self):
        for mode in ('weak','none'):
            self.root=Path(self.dir.name)/mode;self.origin.mode=mode
            self.checkpoint_child('after_checkpoint');self.origin.calls.clear()
            c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'SUCCEEDED')
            self.assertIsNone(self.origin.calls[0]['range'])

    def test_shorter_than_checkpoint_restarts(self):
        self.checkpoint_child('after_checkpoint')
        for p in (self.root/'staging').iterdir():p.write_bytes(b'x')
        self.origin.calls.clear();c=self.cache();c.resume();self.assertEqual(c.wait('r')['request']['state'],'SUCCEEDED')
        self.assertIsNone(self.origin.calls[0]['range'])

    def test_coalescing_cancel_one(self):
        self.origin.mode='hold';c=self.cache();c.submit('a','model-v1')
        self.assertTrue(self.origin.arrived.wait(3));c.submit('b','model-v1')
        self.assertEqual(c.cancel('a'),'CANCELLED');self.origin.release.set()
        self.assertEqual(c.wait('b')['request']['state'],'SUCCEEDED');self.assertEqual(len(self.origin.calls),1)

    def test_late_chunk_after_last_cancel(self):
        received,release=threading.Event(),threading.Event()
        def hook(p,i):
            if p=='chunk_received':received.set();release.wait(4)
        c=self.cache(hook=hook);c.submit('a','model-v1')
        self.assertTrue(received.wait(3));self.assertEqual(c.cancel('a'),'CANCELLED')
        release.set();c.futures[self.digest].result(5)
        self.assertFalse(c.status('a')['available']);self.assertEqual(c.usage()['payload_bytes'],0)

    def test_retired_download_cannot_block_new_generation(self):
        received,release=threading.Event(),threading.Event();first=[True]
        def hook(p,i):
            if p=='chunk_received' and first[0]:
                first[0]=False;received.set();release.wait(4)
        c=self.cache(hook=hook);c.submit('old','model-v1');self.assertTrue(received.wait(3))
        old=c.futures[self.digest];c.cancel('old');c.gc(c.gc_plan())
        c.submit('new','model-v1');self.assertEqual(c.wait('new')['request']['state'],'SUCCEEDED')
        release.set();old.result(5)
        self.assertTrue(c.status('new')['available'])
        with c.read(self.digest) as f:self.assertEqual(f.read(),DATA)

    def test_pin_and_read_prevent_stale_gc_plan(self):
        c=self.cache();v=self.success(c);plan=c.gc_plan();gen=v['object']['generation']
        token=dict(epoch=v['epoch'],generation=gen,revision=v['object']['revision'])
        c.pin(self.digest,True,token);self.assertEqual(c.gc(plan),[])
        with self.assertRaises(CacheError):c.pin(self.digest,False,token)
        token['revision']+=1;c.pin(self.digest,False,token)
        self.assertEqual(c.gc(plan),[])
        plan=c.gc_plan()
        with c.read(self.digest) as f:
            self.assertEqual(c.gc(plan),[]);self.assertEqual(f.read(),DATA)
        self.assertEqual(c.gc(plan),[self.digest])
        self.assertFalse(c.status('r')['available'])
        self.assertEqual(c.submit('r','model-v1')['request']['state'],'SUCCEEDED')
        self.assertEqual(len(self.origin.calls),1)

    def test_gc_then_read_and_stale_generation(self):
        c=self.cache();v=self.success(c);plan=c.gc_plan();old=v['object']['generation'];c.gc(plan)
        with self.assertRaises(CacheError):
            with c.read(self.digest):pass
        v=self.success(c,'new');self.assertNotEqual(old,v['object']['generation'])
        self.assertEqual(c.gc(plan),[])
        with self.assertRaises(CacheError):c.pin(self.digest,True,dict(epoch=v['epoch'],generation=old,revision=1))

    def test_old_epoch_plan_and_pin_rejected(self):
        c=self.cache();v=self.success(c);plan=c.gc_plan()
        token=dict(epoch=v['epoch'],generation=v['object']['generation'],revision=v['object']['revision'])
        c.close();self.caches.remove(c);c=self.cache()
        self.assertEqual(c.gc(plan),[])
        with self.assertRaises(CacheError):c.pin(self.digest,True,token)

    def test_close_keeps_lock_until_reader_closed(self):
        c=self.cache();self.success(c)
        with c.read(self.digest) as f:
            closed=threading.Event()
            t=threading.Thread(target=lambda:(c.close(),closed.set()));t.start()
            time.sleep(.05);self.assertFalse(closed.is_set())
            with self.assertRaises(CacheError):self.cache()
            self.assertEqual(f.read(),DATA)
        t.join(3);self.assertTrue(closed.is_set());self.caches.remove(c)

    def test_capacity_refusal_deletes_nothing(self):
        c=self.cache(budget=len(DATA));self.success(c)
        spec=Spec('other',self.origin.url,len(DATA),'0'*64);c.specs['other']=spec
        with self.assertRaises(CacheError):c.submit('new','other')
        self.assertTrue(c.status('r')['available']);self.assertEqual(c.usage()['payload_bytes'],len(DATA))

    def test_reservation_not_oversold(self):
        self.origin.mode='hold';c=self.cache(budget=len(DATA));c.submit('r','model-v1')
        self.assertTrue(self.origin.arrived.wait(3))
        c.specs['other']=Spec('other',self.origin.url,len(DATA),'0'*64)
        with self.assertRaises(CacheError):c.submit('new','other')
        self.origin.release.set();c.wait('r')

    def test_request_budget_and_conflict(self):
        c=self.cache(records=1);self.success(c)
        with self.assertRaises(CacheError):c.submit('new','model-v1')
        c.specs['model-v1']=Spec('model-v1',self.origin.url,len(DATA),'0'*64)
        with self.assertRaises(CacheError):c.submit('r','model-v1')

    def test_lock_and_corrupt_on_restart(self):
        c=self.cache();self.success(c)
        with self.assertRaises(CacheError):self.cache()
        c.close();self.caches.remove(c)
        (self.root/'objects'/self.digest).write_bytes(b'x'*len(DATA))
        c=self.cache();self.assertFalse(c.status('r')['available'])

    def test_unknown_data_charged_not_deleted(self):
        c=self.cache(budget=len(DATA));p=self.root/'staging'/'unknown';p.write_bytes(DATA)
        with self.assertRaises(CacheError):c.submit('r','model-v1')
        c.gc(c.gc_plan());self.assertEqual(p.read_bytes(),DATA)

    def test_existing_target_not_overwritten(self):
        c=self.cache();target=self.root/'objects'/self.digest;target.write_bytes(b'foreign')
        c.submit('r','model-v1')
        with self.assertRaises(CacheError):c.wait('r')
        self.assertEqual(target.read_bytes(),b'foreign')

    def test_disk_error_retires_authority(self):
        def hook(p,i):
            if p=='before_write':raise OSError(errno.ENOSPC,'injected disk full')
        c=self.cache(hook=hook);c.submit('r','model-v1')
        with self.assertRaises(CacheError):c.wait('r')
        self.assertTrue(c.degraded)
        with self.assertRaises(CacheError):c.submit('new','model-v1')

    def test_readonly_database_retires_authority(self):
        c=self.cache();c.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.OperationalError):c.submit('r','model-v1')
        self.assertTrue(c.degraded)

    def test_gc_crash_recovery(self):
        c=self.cache();self.success(c);c.close();self.caches.remove(c)
        code='''import os,sys
from cacheweft import Cache
def hook(p,i):
 if p==sys.argv[2]:os._exit(73)
c=Cache(sys.argv[1],[],None,hook=hook);c.gc(c.gc_plan())
'''
        r=subprocess.run([sys.executable,'-c',code,str(self.root),'after_delete'],timeout=5)
        self.assertEqual(r.returncode,73);c=self.cache();self.assertFalse(c.status('r')['available'])
        self.assertEqual(c.usage()['payload_bytes'],0)

    def test_gc_crash_retains_partial_deletion_ownership(self):
        self.checkpoint_child('after_checkpoint')
        c=self.cache();self.assertEqual(c.cancel('r'),'CANCELLED')
        # Recreate an owned leftover to model an interrupted cancellation cleanup.
        row=c.status('r')['object'];(self.root/'staging'/row['generation']).write_bytes(b'owned-leftover')
        c.close();self.caches.remove(c)
        # Mark DELETING then crash before unlink; formal recovery must remove staging too.
        code='''import os,sys
from cacheweft import Cache
def hook(p,i):
 if p=='deleting':os._exit(73)
c=Cache(sys.argv[1],[],None,hook=hook)
# Startup cleans CANCELLED leftovers; introduce a new owned remainder before GC.
r=c.status('r')['object'];(c.root/'staging'/r['generation']).write_bytes(b'owned-leftover')
c.gc(c.gc_plan())
'''
        r=subprocess.run([sys.executable,'-c',code,str(self.root)],timeout=5)
        self.assertEqual(r.returncode,73);self.assertTrue(any((self.root/'staging').iterdir()))
        c=self.cache();self.assertEqual(c.usage()['payload_bytes'],0)
        self.assertIsNone(c.status('r')['object'])

    def test_persistent_attempt_budget(self):
        self.checkpoint_child('after_checkpoint')
        c=self.cache(attempts=1);c.resume();self.assertEqual(c.wait('r')['request']['state'],'FAILED')

    def test_publish_boundary_cancel_too_late(self):
        c=self.cache();self.success(c);self.assertEqual(c.cancel('r'),'TOO_LATE')

    def test_cancel_races_durable_publish_intent(self):
        intent,release=threading.Event(),threading.Event();answer=[]
        def hook(p,i):
            if p=='publish_intent':intent.set();release.wait(4)
        c=self.cache(hook=hook);c.submit('r','model-v1');self.assertTrue(intent.wait(3))
        thread=threading.Thread(target=lambda:answer.append(c.cancel('r')));thread.start()
        time.sleep(.03);self.assertEqual(answer,[])
        release.set();thread.join(3)
        self.assertEqual(answer,['TOO_LATE']);self.assertTrue(c.wait('r')['available'])

    def test_symlink_destination_never_overwritten(self):
        c=self.cache();outside=Path(self.dir.name)/'outside';outside.write_bytes(b'untouched')
        (self.root/'objects'/self.digest).symlink_to(outside)
        # Unknown symlink is rejected at admission, before any download.
        with self.assertRaises(CacheError):c.submit('r','model-v1')
        self.assertEqual(outside.read_bytes(),b'untouched')

    def test_cancel_sql_failure_no_success(self):
        self.origin.mode='hold';c=self.cache();c.submit('r','model-v1');self.assertTrue(self.origin.arrived.wait(3))
        c.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.Error):c.cancel('r')
        self.assertTrue(c.degraded);self.origin.release.set()
        c.futures[self.digest].result(5);self.assertFalse(c.status('r')['available'])

    def test_symlink_not_followed(self):
        c=self.cache();outside=Path(self.dir.name)/'outside';outside.write_bytes(b'safe')
        (self.root/'staging'/'foreign').symlink_to(outside)
        with self.assertRaises(CacheError):c.submit('r','model-v1')
        self.assertEqual(outside.read_bytes(),b'safe')
