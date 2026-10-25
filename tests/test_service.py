import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from cacheweft import Cache,Spec,CacheError
from cacheweft.http import HTTPS,Lease
from cacheweft.service import token,send,receive
from .fixture import Origin,DATA
from .daemon_fixture import Daemon,require


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cw-',dir='/tmp');self.base=Path(self.temp.name);os.chmod(self.base,0o755)
        self.origin=Origin(self.base/'tls');self.d=Daemon(self.base/'user',self.origin)
    def tearDown(self):
        self.origin.release.set()
        try:self.d.stop()
        finally:self.origin.close();self.temp.cleanup()

    def test_cli_flow_and_read(self):
        info=self.d.start();self.assertEqual(info['manifest_revision'],1)
        r=json.loads(self.d.cli('fetch','r','fixture-v1','--seconds','3').stdout)
        self.assertIn(r['request']['state'],('WAITING','SUCCEEDED'))
        self.assertEqual(self.d.terminal('r')['request']['state'],'SUCCEEDED')
        output=self.base/'result';self.d.cli('read',self.d.digest,'--output',str(output));self.assertEqual(output.read_bytes(),DATA)
        self.d.cli('pin',self.d.digest,'--value','true','--request','r');self.assertEqual(json.loads(self.d.cli('gc-plan').stdout),[])
        self.d.cli('pin',self.d.digest,'--value','false','--request','r')
        plan=self.base/'plan';plan.write_text(self.d.cli('gc-plan').stdout);self.d.cli('gc-run','--plan',str(plan))
        self.assertFalse(self.d.api('status',id='r')['available'])
        self.d.identity();self.assertEqual(self.d.socket.stat().st_mode&0o777,0o600)

    def test_slow_headers_total_deadline(self):self.slow('slow_headers')
    def test_slow_body_total_deadline(self):self.slow('slow_body')
    def slow(self,mode):
        self.origin.mode=mode;self.d.start();start=time.monotonic();self.d.submit('slow',.25)
        status=self.d.terminal('slow',2)
        self.assertEqual(status['request']['state'],'TIMED_OUT');self.assertFalse(status['available'])
        self.assertLess(time.monotonic()-start,2)
        until=time.monotonic()+2
        while time.monotonic()<until:
            try:
                self.origin.mode='normal';self.d.api('resume',id='new',source='slow',artifact='fixture-v1',seconds=3,epoch=status['epoch'],token=token(status));break
            except CacheError as exc:
                if 'worker still exiting' not in str(exc):raise
                time.sleep(.02)
        self.assertEqual(self.d.terminal('new')['request']['state'],'SUCCEEDED')
        self.assertEqual(self.d.api('status',id='slow')['request']['deadline'],status['request']['deadline'])

    def test_slow_source_cancel_and_stale_token(self):
        self.origin.mode='slow_headers';self.d.start();v=self.d.submit('r',3)
        self.assertTrue(self.origin.arrived.wait(2));old=token(v)
        self.assertEqual(self.d.api('cancel',id='r',epoch=v['epoch'],token=old),'CANCELLED')
        with self.assertRaises(CacheError):self.d.api('cancel',id='r',epoch=v['epoch'],token=old)
        self.assertFalse(self.d.api('status',id='r')['available'])
        start=time.monotonic();self.d.stop();self.assertLess(time.monotonic()-start,2)

    def test_coalesced_old_deadline_not_extended(self):
        self.origin.mode='hold';self.d.start();old=self.d.submit('short',.2)
        self.assertTrue(self.origin.arrived.wait(2));self.d.submit('long',3)
        self.assertEqual(self.d.terminal('short')['request']['state'],'TIMED_OUT')
        self.origin.release.set();self.assertEqual(self.d.terminal('long')['request']['state'],'SUCCEEDED')
        now=self.d.api('status',id='short')['request'];self.assertEqual(now['deadline'],old['request']['deadline']);self.assertEqual(now['state'],'TIMED_OUT')

    def test_restart_interrupts_old_deadline_and_requires_explicit_resume(self):
        self.origin.mode='partial_hold';first=self.d.start();self.d.submit('old',5)
        self.assertTrue(self.origin.arrived.wait(2));until=time.monotonic()+2
        while time.monotonic()<until:
            old=self.d.api('status',id='old')
            if old['object']['offset']>=65536:break
            time.sleep(.01)
        self.assertGreaterEqual(old['object']['offset'],65536);self.d.stop(crash=True)
        second=self.d.start();self.assertNotEqual(first['epoch'],second['epoch'])
        paused=self.d.api('status',id='old');self.assertEqual(paused['request']['state'],'INTERRUPTED');self.assertEqual(paused['object']['state'],'PAUSED')
        self.assertEqual(old['request']['deadline'],paused['request']['deadline'])
        with self.assertRaises(CacheError):self.d.api('fetch',id='stale',artifact='fixture-v1',seconds=3,epoch=first['epoch'])
        with self.assertRaises(CacheError):self.d.api('cancel',id='old',epoch=second['epoch'],token=token(old))
        self.origin.release.set();self.origin.mode='normal'
        self.d.api('resume',id='fresh',source='old',artifact='fixture-v1',seconds=3,epoch=second['epoch'],token=token(paused))
        self.assertEqual(self.d.terminal('fresh')['request']['state'],'SUCCEEDED')
        self.assertEqual(self.d.api('status',id='old')['request']['state'],'INTERRUPTED')

    def test_manifest_immutable_and_revision_rollback(self):
        self.d.start();self.d.stop()
        self.d.value['entries'][0]['sha256']='0'*64;self.d.write()
        r=subprocess.run(self.d.argv(),capture_output=True,timeout=5);self.assertNotEqual(r.returncode,0)
        self.d.value['revision']=2;self.d.write()
        r=subprocess.run(self.d.argv(),capture_output=True,timeout=5);self.assertNotEqual(r.returncode,0)
        self.d.value['entries'][0]['sha256']=self.d.digest;self.d.write();self.d.start();self.d.stop()
        self.d.value['revision']=1;self.d.write()
        r=subprocess.run(self.d.argv(),capture_output=True,timeout=5);self.assertNotEqual(r.returncode,0)

    def test_control_connection_limit(self):
        self.d.start();held=[]
        try:
            for _ in range(16):
                s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);held.append(s);s.settimeout(1);s.connect(str(self.d.socket));time.sleep(.02)
            time.sleep(.1)
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
                s.connect(str(self.d.socket));value=receive(s,time.monotonic()+1)
                self.assertEqual(value,dict(ok=False,error='OVERLOADED'))
        finally:
            for s in held:s.close()
        time.sleep(.05);self.assertTrue(self.d.api('info')['epoch'])

    def test_duplicate_id_cannot_extend_duration(self):
        self.origin.mode='hold';self.d.start();v=self.d.submit('r',.2)
        with self.assertRaises(CacheError):self.d.submit('r',3)
        self.assertEqual(self.d.terminal('r')['request']['state'],'TIMED_OUT')
        self.assertEqual(self.d.api('status',id='r')['request']['deadline'],v['request']['deadline'])

    def test_bad_frames_and_config_permissions(self):
        self.d.start()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.connect(str(self.d.socket));s.sendall(struct.pack('!I',16385));r=receive(s,time.monotonic()+2);self.assertFalse(r['ok'])
        self.assertTrue(self.d.api('info')['epoch']);self.d.stop()
        os.chmod(self.d.configuration,0o644)
        r=subprocess.run(self.d.argv(),capture_output=True,timeout=5);self.assertNotEqual(r.returncode,0)

    def test_deadline_rechecked_after_verification(self):
        self.origin.mode='hold';self.d.start();self.d.submit('a',3)
        # Distinct IDs sharing one job are not queued work; direct formal core
        # uses a delayed seal to prove expiry is rechecked before publish intent.
        self.origin.release.set();self.d.terminal('a')
        from cacheweft import Cache,Spec
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest)
        def hook(p,i):
            if p=='verified':time.sleep(.25)
        with Cache(self.base/'local',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert),hook=hook) as c:
            c.submit('late','x',seconds=.1);self.assertEqual(c.wait('late')['request']['state'],'TIMED_OUT');self.assertFalse(c.status('late')['available'])

    def test_queued_job_expires_without_source_request(self):
        self.d.value['entries'].extend([dict(id='b',url=self.origin.url,size=len(DATA),sha256='0'*64),dict(id='c',url=self.origin.url,size=len(DATA),sha256='1'*64)])
        self.d.write();self.origin.mode='hold';info=self.d.start();self.d.submit('a',3)
        self.d.api('fetch',id='b',artifact='b',seconds=3,epoch=info['epoch'])
        until=time.monotonic()+2
        while len(self.origin.calls)<2 and time.monotonic()<until:time.sleep(.01)
        self.assertEqual(len(self.origin.calls),2)
        self.d.api('fetch',id='c',artifact='c',seconds=.2,epoch=info['epoch'])
        status=self.d.terminal('c');self.assertEqual(status['request']['state'],'TIMED_OUT')
        self.assertEqual(status['object']['attempts'],0);self.assertEqual(len(self.origin.calls),2)

    def test_authority_wait_cannot_turn_expired_fetch_into_hit(self):
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest)
        with Cache(self.base/'local',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert)) as c:
            c.submit('ready','x');c.wait('ready');errors=[];started=threading.Event()
            def fetch():
                started.set()
                try:c.submit('expired','x',seconds=.1)
                except CacheError as exc:errors.append(str(exc))
            with c.lock:
                t=threading.Thread(target=fetch);t.start();self.assertTrue(started.wait(1));time.sleep(.25)
            t.join(2);self.assertEqual(errors,['Submission deadline expired before admission'])
            with self.assertRaises(CacheError):c.status('expired')

    def test_committed_publication_recovery_preserves_original_deadline(self):
        code='''import os,sys
from cacheweft import Cache,Spec
from cacheweft.http import HTTPS
def hook(p,i):
 if p=='publish_intent':os._exit(73)
c=Cache(sys.argv[1],[Spec('x',sys.argv[2],1048576,sys.argv[5])],HTTPS(sys.argv[3],'127.0.0.1',sys.argv[4]),hook=hook)
c.submit('committed','x',seconds=3);c.wait('committed')
'''
        root=self.base/'intent'
        r=subprocess.run([sys.executable,'-c',code,str(root),self.origin.url,self.origin.origin,self.origin.cert,self.d.digest],timeout=5)
        self.assertEqual(r.returncode,73)
        db=sqlite3.connect(root/'catalog.db');deadline=db.execute("SELECT deadline FROM requests WHERE id='committed'").fetchone()[0];db.close()
        self.origin.calls.clear()
        with Cache(root,[],None) as c:
            status=c.status('committed');self.assertEqual(status['request']['state'],'SUCCEEDED')
            self.assertEqual(status['request']['deadline'],deadline);self.assertEqual(self.origin.calls,[])

    def test_deadline_check_survives_delayed_sweeper(self):
        self.origin.mode='slow_headers';spec=Spec('x',self.origin.url,len(DATA),self.d.digest)
        with Cache(self.base/'local',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert)) as c:
            # Simulate a late sweep; the official socket watchdog and error path
            # must still resolve the original deadline rather than report FAILED.
            c.sweeper_stop.set();c.sweeper.join()
            c.submit('late_parser','x',seconds=.1)
            self.assertEqual(c.wait('late_parser')['request']['state'],'TIMED_OUT')
            self.assertFalse(c.status('late_parser')['available'])

    def test_managed_core_cannot_import_or_create_unbounded_interest(self):
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest)
        transport=HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert)
        with Cache(self.base/'managed',[spec],transport,manifest_revision=1) as c:
            with self.assertRaisesRegex(CacheError,'requires a deadline'):c.submit('unbounded','x')
        self.origin.mode='hold'
        with Cache(self.base/'legacy',[spec],transport) as c:
            c.submit('old_unbounded','x');self.assertTrue(self.origin.arrived.wait(2))
        with self.assertRaisesRegex(CacheError,'Unversioned exploration catalog'):
            Cache(self.base/'legacy',[spec],transport,manifest_revision=1)
        with Cache(self.base/'legacy',[spec],transport) as c:
            self.assertEqual(c.status('old_unbounded')['request']['state'],'WAITING')

    def test_shutdown_rejects_preaccepted_incomplete_request(self):
        info=self.d.start();q=dict(op='fetch',id='after_stop',artifact='fixture-v1',seconds=3,epoch=info['epoch'])
        data=json.dumps(q).encode()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.d.socket));sock.sendall(struct.pack('!I',len(data))+data[:1]);time.sleep(.03)
            self.d.proc.send_signal(signal.SIGTERM);until=time.monotonic()+1
            while (self.d.root/'ready.json').exists() and time.monotonic()<until:time.sleep(.01)
            self.assertFalse((self.d.root/'ready.json').exists());sock.sendall(data[1:])
            response=receive(sock,time.monotonic()+2);self.assertFalse(response['ok'])
            self.assertIn('Authority unavailable',response['error'])
        self.d.proc.wait(3);self.assertEqual(self.d.proc.returncode,0)
        db=sqlite3.connect(self.d.root/'catalog.db')
        try:self.assertEqual(db.execute("SELECT count(*) FROM requests WHERE id='after_stop'").fetchone()[0],0)
        finally:db.close()

    def test_watchdog_lives_through_connection_close_response_body(self):
        self.origin.mode='slow_body';start=time.monotonic()
        conn,response=HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert).get(self.origin.url,{},lease=Lease(start+.2))
        try:
            self.assertFalse(conn.done.is_set())
            try:data=response.read(len(DATA))
            except OSError:data=b''
            self.assertLess(len(data),len(DATA));self.assertLess(time.monotonic()-start,1.5)
        finally:response.close();conn.close()

    def test_deadline_rechecked_after_write_precharge(self):
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest);reached=[]
        def hook(p,i):
            if p=='before_write':reached.append(True);time.sleep(.3)
        with Cache(self.base/'precharge',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert),hook=hook) as c:
            c.submit('late_write','x',seconds=.2);self.assertEqual(c.wait('late_write')['request']['state'],'TIMED_OUT')
            self.assertTrue(reached);self.assertEqual(c.usage()['payload_bytes'],0)

    def test_sqlite_admission_wait_cannot_accept_expired_hit(self):
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest)
        with Cache(self.base/'admission',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert)) as c:
            c.submit('ready','x');c.wait('ready');c.db.execute('PRAGMA busy_timeout=2000')
            other=sqlite3.connect(c.root/'catalog.db',check_same_thread=False,isolation_level=None);other.execute('BEGIN IMMEDIATE')
            release=threading.Thread(target=lambda:(time.sleep(.3),other.execute('ROLLBACK')));release.start()
            try:
                with self.assertRaisesRegex(CacheError,'expired before admission'):c.submit('late_hit','x',seconds=.1)
            finally:release.join(2);other.close()
            with self.assertRaises(CacheError):c.status('late_hit')

    def test_sqlite_publish_wait_cannot_create_expired_intent(self):
        spec=Spec('x',self.origin.url,len(DATA),self.d.digest);holders=[];reached=[]
        def hook(p,i):
            if p=='verified':
                reached.append(True);other=sqlite3.connect(c.root/'catalog.db',check_same_thread=False,isolation_level=None)
                other.execute('BEGIN IMMEDIATE')
                t=threading.Thread(target=lambda:(time.sleep(.6),other.execute('ROLLBACK')));holders.append((other,t));t.start()
        with Cache(self.base/'intent_wait',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert),hook=hook) as c:
            c.db.execute('PRAGMA busy_timeout=2000');c.submit('late_intent','x',seconds=.4)
            self.assertEqual(c.wait('late_intent')['request']['state'],'TIMED_OUT');self.assertTrue(reached)
            self.assertFalse(any((c.root/'objects').iterdir()))
        for other,t in holders:t.join(2);other.close()
