import os
from pathlib import Path
import tempfile
import time
import unittest
from cacheweft import Cache,Spec,CacheError
from cacheweft.http import HTTPS
from .fixture import DATA
import hashlib
from .fixture import Origin
from .review_fixture import admission,publication


class DeadlineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cw-review-',dir='/tmp');self.base=Path(self.temp.name);os.chmod(self.base,0o755)
        self.origin=Origin(self.base/'tls')
    def tearDown(self):self.origin.release.set();self.origin.close();self.temp.cleanup()
    def test_daemon_admission_commit_reader_cutoff(self):admission(self.base/'case',self.origin)
    def test_publish_commit_reader_cutoff(self):publication(self.base/'case',self.origin)
    def test_shared_begin_wait_only_live_member_succeeds(self):publication(self.base/'case',self.origin,kind='writer',shared=True)
    def test_shared_commit_wait_only_live_member_succeeds(self):publication(self.base/'case',self.origin,shared=True)
    def test_delayed_validation_cannot_commit_after_cutoff(self):
        spec=Spec('x',self.origin.url,len(DATA),hashlib.sha256(DATA).hexdigest())
        with Cache(self.base/'validation',[spec],HTTPS(self.origin.origin,'127.0.0.1',self.origin.cert),manifest_revision=1) as cache:
            cache.db.execute('CREATE TABLE probe(value INTEGER)')
            with cache._authority():
                with self.assertRaisesRegex(CacheError,'deadline expired'):
                    with cache._tx(deadline=time.monotonic()+.1,before_commit=lambda:time.sleep(.15)):
                        cache.db.execute('INSERT INTO probe VALUES(1)')
                self.assertFalse(cache.db.in_transaction)
                self.assertEqual(cache.db.execute('SELECT count(*) FROM probe').fetchone()[0],0)
                self.assertEqual(cache.db.execute('PRAGMA busy_timeout').fetchone()[0],200)
