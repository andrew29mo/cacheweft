"""Serialized authority for payload writes, publication, readers and eviction.

Network reads occur outside the authority lock. No detached process receives
a writable file descriptor. Disk operations are synchronous and may block:
this core makes no bounded-cancellation promise for stuck kernel I/O.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from .http import Lease


class CacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class Spec:
    name: str
    url: str
    size: int
    sha256: str

    def __post_init__(self):
        if not self.name or type(self.size) is not int or self.size < 1 or not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('Invalid trusted manifest entry')


class Cache:
    CHUNK = 65536

    def __init__(self, root, specs, transport, *, budget=16*1024*1024,
                 records=256, jobs=16, readers=8, attempts=6,
                 db_pages=1024, hook=None, manifest_revision=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise CacheError('Cache root must be private')
        self.lock = threading.RLock()
        self.readers_changed = threading.Condition(self.lock)
        self.lockfile = open(self.root/'writer.lock', 'a+b')
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lockfile.close()
            raise CacheError('Another authority holds the lock')
        self.specs = {s.name: s for s in specs}
        if len(self.specs) != len(specs):
            self.lockfile.close()
            raise ValueError('Duplicate manifest ID')
        self.transport, self.budget = transport, budget
        self.records, self.jobs, self.max_readers, self.max_attempts = records, jobs, readers, attempts
        self.hook = hook or (lambda point, info: None)
        self.refs, self.futures, self.future_generations = {}, {}, {}
        self.work = set()
        self.leases={};self.sweeper_stop=threading.Event();self.sweeper=None
        self.stopping = self.degraded = False
        self.epoch = str(uuid.uuid4())
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='cacheweft')
        self.db = sqlite3.connect(self.root/'catalog.db', check_same_thread=False, isolation_level=None, timeout=.2)
        try:
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA max_page_count='+str(db_pages))
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS objects(
                digest TEXT PRIMARY KEY,size INTEGER NOT NULL,url TEXT NOT NULL,
                generation TEXT NOT NULL,state TEXT NOT NULL,offset INTEGER NOT NULL,
                etag TEXT,attempts INTEGER NOT NULL,wire INTEGER NOT NULL,
                pin INTEGER NOT NULL,access INTEGER NOT NULL,revision INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS requests(
                id TEXT PRIMARY KEY,params TEXT NOT NULL,digest TEXT NOT NULL,
                generation TEXT NOT NULL,state TEXT NOT NULL,result TEXT);
            ''')
            columns={r[1] for r in self.db.execute('PRAGMA table_info(requests)')}
            for name,definition in [('deadline','REAL'),('epoch','TEXT'),('revision','INTEGER NOT NULL DEFAULT 1')]:
                if name not in columns:self.db.execute('ALTER TABLE requests ADD COLUMN '+name+' '+definition)
            self.manifest_revision=manifest_revision
            if manifest_revision is not None:self._bind_manifest(manifest_revision)
            for d in ('staging','objects'):
                (self.root/d).mkdir(mode=0o700, exist_ok=True)
                if (self.root/d).is_symlink():
                    raise CacheError('Symlink directory refused')
            self._recover()
            self.sweeper=threading.Thread(target=self._sweep,name='cacheweft-expiry',daemon=True)
            self.sweeper.start()
        except BaseException:
            self.db.close()
            self.pool.shutdown(wait=True)
            self.lockfile.close()
            raise

    def _bind_manifest(self,revision):
        if type(revision) is not int or revision<1 or revision>1000000 or len(self.specs)>128:
            raise ValueError('Invalid manifest revision or entry count')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS manifests(revision INTEGER PRIMARY KEY,sha TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS manifest_entries(name TEXT PRIMARY KEY,params TEXT NOT NULL);''')
        entries={name:json.dumps([s.url,s.size,s.sha256],separators=(',',':')) for name,s in sorted(self.specs.items())}
        fingerprint=hashlib.sha256(json.dumps(entries,sort_keys=True).encode()).hexdigest()
        last=self.db.execute('SELECT max(revision) FROM manifests').fetchone()[0]
        if last is None and self.db.execute('SELECT count(*) FROM requests').fetchone()[0]:
            raise CacheError('Unversioned exploration catalog; preserve it and use a new service root')
        old=self.db.execute('SELECT sha FROM manifests WHERE revision=?',(revision,)).fetchone()
        if last is not None and revision<last:raise CacheError('Stale manifest revision')
        if old and old[0]!=fingerprint:raise CacheError('Manifest revision conflict')
        if not old and self.db.execute('SELECT count(*) FROM manifests').fetchone()[0]>=64:raise CacheError('Manifest history full')
        with self._tx():
            for name,params in entries.items():
                previous=self.db.execute('SELECT params FROM manifest_entries WHERE name=?',(name,)).fetchone()
                if previous and previous[0]!=params:raise CacheError('Artifact ID is immutable; use a new ID')
                if not previous:
                    if self.db.execute('SELECT count(*) FROM manifest_entries').fetchone()[0]>=128:raise CacheError('Manifest entries full')
                    self.db.execute('INSERT INTO manifest_entries VALUES(?,?)',(name,params))
            if not old:self.db.execute('INSERT INTO manifests VALUES(?,?)',(revision,fingerprint))

    def _lease(self,digest,generation):
        key=(digest,generation)
        lease=self.leases.get(key)
        if lease is None or lease.cancelled.is_set():
            lease=Lease();self.leases[key]=lease
        rows=list(self.db.execute("SELECT deadline FROM requests WHERE digest=? AND generation=? AND state='WAITING'",key))
        lease.deadline=None if any(r[0] is None for r in rows) else max((r[0] for r in rows),default=time.monotonic())
        return lease

    def _expire(self,digest,generation):
        now=time.monotonic()
        stale=list(self.db.execute("SELECT id FROM requests WHERE digest=? AND generation=? AND state='WAITING' AND deadline IS NOT NULL AND (epoch<>? OR deadline<=?)",(digest,generation,self.epoch,now)))
        if stale:
            with self._tx():
                self.db.executemany("UPDATE requests SET state='TIMED_OUT',result='TIMED_OUT',revision=revision+1 WHERE id=?",stale)
        count=self.db.execute("SELECT count(*) FROM requests WHERE digest=? AND generation=? AND state='WAITING'",(digest,generation)).fetchone()[0]
        row=self._row(digest)
        if not count and row and row['generation']==generation and row['state'] in ('QUEUED','DOWNLOADING','VERIFYING'):
            with self._tx():self.db.execute("UPDATE objects SET state='PAUSED',revision=revision+1 WHERE digest=?",(digest,))
            lease=self.leases.get((digest,generation))
            if lease:lease.cancelled.set()
        return count

    def _sweep(self):
        while not self.sweeper_stop.wait(.02):
            try:
                with self._authority():
                    if self.stopping or self.degraded:continue
                    for row in list(self.db.execute("SELECT digest,generation FROM objects WHERE state IN ('QUEUED','DOWNLOADING','VERIFYING')")):
                        self._expire(row['digest'],row['generation'])
            except (sqlite3.Error,OSError):pass

    def _call(self, point, **info):
        self.hook(point, info)

    @contextmanager
    def _authority(self):
        # Classify by operation boundary, not an incomplete list of errno values.
        # Transport get/read never runs here. Controlled writes/catalog access do;
        # reads through the public read() file object execute in the caller.
        with self.lock:
            try:
                yield
            except (OSError, sqlite3.Error):
                self.degraded=True
                for lease in self.leases.values():lease.cancelled.set()
                raise

    def _row(self, digest):
        self.db.row_factory = sqlite3.Row
        return self.db.execute('SELECT * FROM objects WHERE digest=?', (digest,)).fetchone()

    def _stage(self, row):
        if not re.fullmatch('[0-9a-f-]{36}', row['generation']):
            raise CacheError('Invalid generation')
        return self.root/'staging'/row['generation']

    def _target(self, digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise CacheError('Invalid digest')
        return self.root/'objects'/digest

    @contextmanager
    def _tx(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _guard(self):
        if self.stopping or self.degraded:
            raise CacheError('Authority unavailable')

    def _syncdir(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _open(self, path, flags):
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        st = os.fstat(fd)
        # Two links are allowed only during our recoverable hard-link publish.
        if not stat.S_ISREG(st.st_mode) or st.st_nlink not in (1,2):
            os.close(fd)
            raise CacheError('Unexpected payload inode')
        return fd

    def _verify(self, path, row):
        h = hashlib.sha256()
        fd = self._open(path, os.O_RDONLY)
        with os.fdopen(fd, 'rb') as f:
            if os.fstat(f.fileno()).st_size != row['size']:
                return False
            while chunk := f.read(self.CHUNK):
                h.update(chunk)
        return h.hexdigest() == row['digest']

    def _usage(self):
        used, allocated, seen = 0, 0, set()
        for dirname in ('staging','objects'):
            for path in (self.root/dirname).iterdir():
                st = path.lstat()
                if not stat.S_ISREG(st.st_mode):
                    raise CacheError('Unknown nonregular cache entry')
                if (st.st_dev,st.st_ino) not in seen:
                    used += st.st_size
                    allocated += st.st_blocks*512
                    seen.add((st.st_dev,st.st_ino))
        reserved = 0
        for row in self.db.execute("SELECT * FROM objects WHERE state IN ('QUEUED','DOWNLOADING','VERIFYING','PUBLISHING')"):
            path = self._stage(row)
            have = path.stat().st_size if path.exists() else 0
            reserved += max(0,row['size']-have)
        return dict(payload_bytes=used,remaining_reserved=reserved,allocated_payload_bytes=allocated,
                    budget=self.budget,free_bytes=os.statvfs(self.root).f_bavail*os.statvfs(self.root).f_frsize)

    def _finish_requests(self, digest, generation, result):
        self.db.execute("UPDATE requests SET state=?,result=?,revision=revision+1 WHERE digest=? AND generation=? AND state='WAITING'",
                        (result, result, digest, generation))

    def _publish(self, row):
        stage,target = self._stage(row),self._target(row['digest'])
        if not target.exists():
            # link() is atomic and refuses any existing destination, including a symlink.
            os.link(stage,target,follow_symlinks=False)
        else:
            # Only our persisted publish intent may adopt an existing destination.
            if not stage.exists() or not os.path.samestat(stage.lstat(),target.lstat()):
                raise CacheError('Existing destination is not this publication')
        self._syncdir(target.parent)
        self._call('after_link', digest=row['digest'])
        if stage.exists():
            stage.unlink()
            self._syncdir(stage.parent)
        self._call('after_unlink_stage', digest=row['digest'])
        with self._tx():
            self.db.execute("UPDATE objects SET state='READY',offset=size WHERE digest=?", (row['digest'],))
            self._finish_requests(row['digest'],row['generation'],'SUCCEEDED')
        self._call('after_ready', digest=row['digest'])

    def _seal_publish(self, row):
        # Authority is held. All writable FDs have closed before hashing.
        if not self._expire(row['digest'],row['generation']):raise CacheError('No live subscriber')
        with self._tx():
            self.db.execute("UPDATE objects SET state='VERIFYING' WHERE digest=?",(row['digest'],))
        self._call('sealed',digest=row['digest'])
        if not self._verify(self._stage(row),row):raise CacheError('Digest mismatch')
        self._call('verified',digest=row['digest'])
        if not self._expire(row['digest'],row['generation']):raise CacheError('No live subscriber')
        with self._tx():
            # BEGIN may wait on SQLite even while our process authority is held.
            live=self.db.execute("SELECT count(*) FROM requests WHERE digest=? AND generation=? AND state='WAITING' AND (deadline IS NULL OR (epoch=? AND deadline>?))",(row['digest'],row['generation'],self.epoch,time.monotonic())).fetchone()[0]
            if not live:raise CacheError('Publication deadline expired before intent')
            self.db.execute("UPDATE objects SET state='PUBLISHING' WHERE digest=?",(row['digest'],))
        self._call('publish_intent',digest=row['digest'])
        self._publish(self._row(row['digest']))

    def _recover(self):
        self.db.row_factory = sqlite3.Row
        with self._tx():
            self.db.execute("UPDATE requests SET state='INTERRUPTED',result='INTERRUPTED',revision=revision+1 WHERE state='WAITING' AND deadline IS NOT NULL AND digest IN (SELECT digest FROM objects WHERE state<>'PUBLISHING')")
        for row in list(self.db.execute('SELECT * FROM objects')):
            stage,target = self._stage(row),self._target(row['digest'])
            state = row['state']
            if state == 'DELETING':
                for path in (stage,target):
                    if path.exists() or path.is_symlink():
                        path.unlink(); self._syncdir(path.parent)
                with self._tx():
                    self.db.execute('DELETE FROM objects WHERE digest=?',(row['digest'],))
            elif state == 'PUBLISHING':
                path = target if target.exists() else stage
                if not self._verify(path,row):
                    raise CacheError('Invalid pending publication')
                # After stage unlink, intent plus verified target permits adoption.
                if target.exists() and not stage.exists():
                    with self._tx():
                        self.db.execute("UPDATE objects SET state='READY' WHERE digest=?",(row['digest'],))
                        self._finish_requests(row['digest'],row['generation'],'SUCCEEDED')
                else:
                    self._publish(row)
            elif state == 'READY':
                if not target.exists() or not self._verify(target,row):
                    with self._tx():
                        self.db.execute("UPDATE objects SET state='CORRUPT' WHERE digest=?",(row['digest'],))
            elif state in ('QUEUED','DOWNLOADING','VERIFYING','PAUSED'):
                offset = row['offset']
                if stage.exists():
                    fd=self._open(stage,os.O_RDWR)
                    with os.fdopen(fd,'r+b') as f:
                        if os.fstat(f.fileno()).st_size < offset:
                            offset=0
                        f.truncate(offset); f.flush(); os.fsync(f.fileno())
                elif offset:
                    offset=0
                with self._tx():
                    interested=self.db.execute("SELECT count(*) FROM requests WHERE digest=? AND generation=? AND state='WAITING'",(row['digest'],row['generation'])).fetchone()[0]
                    self.db.execute("UPDATE objects SET state=?,offset=? WHERE digest=?",('QUEUED' if interested else 'PAUSED',offset,row['digest']))
            elif state in ('CANCELLED','FAILED') and stage.exists():
                stage.unlink(); self._syncdir(stage.parent)
        # Recovery never starts downloads implicitly; resume() is an explicit API.
        usage=self._usage()
        if usage['payload_bytes']+usage['remaining_reserved'] > self.budget:
            self.degraded=True

    def submit(self, request_id, name, *, seconds=None, epoch=None, resume_from=None, token=None):
        received=time.monotonic()
        if self.manifest_revision is not None and seconds is None:raise CacheError('Managed request requires a deadline')
        if seconds is not None and (type(seconds) not in (int,float) or not math.isfinite(seconds) or not .1<=seconds<=120):raise CacheError('Deadline duration must be 0.1..120 seconds')
        if seconds is not None:seconds=float(seconds)
        with self._authority():
            self._guard()
            if seconds is not None and time.monotonic()>=received+seconds:raise CacheError('Submission deadline expired before admission')
            if epoch is not None and epoch!=self.epoch:raise CacheError('Stale epoch')
            if not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',request_id):
                raise CacheError('Invalid request ID')
            spec=self.specs[name]
            fields=[name,spec.url,spec.size,spec.sha256]
            if seconds is not None:fields.extend([seconds,resume_from])
            params=json.dumps(fields,separators=(',',':'))
            old=self.db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if old:
                if old['params'] != params: raise CacheError('Request ID conflict')
                return self.status(request_id)
            if self.db.execute('SELECT count(*) FROM requests').fetchone()[0] >= self.records:
                raise CacheError('Request history full')
            row=self._row(spec.sha256)
            if resume_from is not None:
                source=self.status(resume_from)['request']
                if not token or token.get('epoch')!=self.epoch or token.get('revision')!=source['revision'] or token.get('generation')!=source['generation'] or source['state'] not in ('INTERRUPTED','TIMED_OUT') or source['digest']!=spec.sha256 or json.loads(source['params'])[:4]!=fields[:4] or row is None or row['generation']!=source['generation'] or row['state']!='PAUSED':raise CacheError('Stale or invalid resume')
                previous=self.futures.get(spec.sha256)
                if previous and not previous.done():raise CacheError('Retired worker still exiting; retry explicit resume')
                self.work={f for f in self.work if not f.done()}
                if len(self.work)>=self.jobs:raise CacheError('Outstanding worker budget full')
            elif row and row['state']=='PAUSED':raise CacheError('Explicit resume required')
            if row and row['size'] != spec.size: raise CacheError('Digest size conflict')
            if row and row['state'] in ('FAILED','CANCELLED','CORRUPT','DELETING'):
                raise CacheError('Explicit cleanup required')
            if row is None:
                self.work={f for f in self.work if not f.done()}
                if len(self.work)>=self.jobs:raise CacheError('Outstanding worker budget full')
                if self.db.execute("SELECT count(*) FROM objects WHERE state NOT IN ('READY','CORRUPT','FAILED','CANCELLED')").fetchone()[0] >= self.jobs:
                    raise CacheError('Work queue full')
                u=self._usage()
                if u['payload_bytes']+u['remaining_reserved']+spec.size > self.budget:
                    raise CacheError('Capacity: explicit GC required')
                generation=str(uuid.uuid4())
            else:
                generation=row['generation']
            try:
                with self._tx():
                    if seconds is not None and time.monotonic()>=received+seconds:raise CacheError('Submission deadline expired before admission')
                    if row is None:
                        self.db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                        (spec.sha256,spec.size,spec.url,generation,'QUEUED',0,None,0,0,0,time.time_ns(),1))
                    done=bool(row and row['state']=='READY')
                    if row and row['state']=='PAUSED':
                        u=self._usage()
                        path=self._stage(row);have=path.stat().st_size if path.exists() else 0
                        if u['payload_bytes']+u['remaining_reserved']+max(0,spec.size-have)>self.budget:raise CacheError('Resume capacity refused')
                        self.db.execute("UPDATE objects SET state='QUEUED',revision=revision+1 WHERE digest=?",(spec.sha256,))
                    self.db.execute('INSERT INTO requests(id,params,digest,generation,state,result,deadline,epoch,revision) VALUES(?,?,?,?,?,?,?,?,1)',
                                    (request_id,params,spec.sha256,generation,'SUCCEEDED' if done else 'WAITING','SUCCEEDED' if done else None,received+seconds if seconds is not None else None,self.epoch))
                self._lease(spec.sha256,generation)
                self._schedule(spec.sha256,generation)
            except sqlite3.Error:
                self.degraded=True
                raise
            return self.status(request_id)

    def _schedule(self,digest,generation):
        row=self._row(digest)
        if row['state']=='READY': return
        self._lease(digest,generation)
        old=self.futures.get(digest)
        if old and not old.done() and self.future_generations.get(digest)==generation:return
        self.future_generations[digest]=generation
        self.futures[digest]=self.pool.submit(self._download,digest,generation,self.epoch)
        self.work.add(self.futures[digest])

    def resume(self):
        with self._authority():
            self._guard()
            for row in list(self.db.execute("SELECT * FROM objects WHERE state='QUEUED'")):
                if self.db.execute("SELECT count(*) FROM requests WHERE digest=? AND generation=? AND state='WAITING'",(row['digest'],row['generation'])).fetchone()[0]:
                    self._schedule(row['digest'],row['generation'])

    def _active(self,digest,generation,epoch):
        self._guard()
        self._expire(digest,generation)
        row=self._row(digest)
        if epoch != self.epoch or row is None or row['generation'] != generation or row['state'] not in ('QUEUED','DOWNLOADING'):
            raise CacheError('Retired download')
        return row

    def _download(self,digest,generation,epoch):
        try:
            while True:
                with self._authority():
                    row=self._active(digest,generation,epoch)
                    # A fully checkpointed artifact needs local verification, not
                    # an unsatisfiable Range bytes=size- (which correctly gets 416).
                    if row['offset']==row['size']:
                        self._seal_publish(row)
                        return
                    if row['attempts'] >= self.max_attempts or row['wire'] >= row['size']*4:
                        raise CacheError('Persistent transfer budget exhausted')
                    offset=row['offset'] if row['etag'] else 0
                    stage=self._stage(row)
                    fd=self._open(stage,os.O_CREAT|os.O_RDWR)
                    with os.fdopen(fd,'r+b') as f:
                        f.truncate(offset); f.flush(); os.fsync(f.fileno())
                    self._syncdir(stage.parent)
                    with self._tx():
                        self.db.execute("UPDATE objects SET state='DOWNLOADING',offset=?,attempts=attempts+1 WHERE digest=?",(offset,digest))
                    headers={'Range':f'bytes={offset}-','If-Range':row['etag']} if offset else {}
                    url,size,etag=row['url'],row['size'],row['etag']
                conn=None
                try:
                    conn,response=self.transport.get(url,headers,lease=self.leases[(digest,generation)])
                    encoding=response.getheader('Content-Encoding')
                    if encoding not in (None,'identity'): raise CacheError('Encoded body refused')
                    got_etag=response.getheader('ETag')
                    strong=got_etag if got_etag and re.fullmatch(r'"[\x21\x23-\x7e]*"',got_etag) else None
                    if response.status==200:
                        offset=0
                    elif response.status==206 and headers:
                        if response.getheader('Content-Range') != f'bytes {offset}-{size-1}/{size}' or strong != etag:
                            raise CacheError('Invalid range/validator')
                    else:
                        raise CacheError('Unexpected HTTP status')
                    if response.getheader('Content-Length') != str(size-offset):
                        raise CacheError('Exact Content-Length required')
                    with self._authority():
                        row=self._active(digest,generation,epoch)
                        fd=self._open(stage,os.O_RDWR)
                        with os.fdopen(fd,'r+b') as f:
                            f.truncate(offset); f.flush(); os.fsync(f.fileno())
                        with self._tx():
                            self.db.execute('UPDATE objects SET offset=?,etag=? WHERE digest=?',(offset,strong,digest))
                    position=offset
                    while position < size:
                        chunk=response.read(min(self.CHUNK,size-position))
                        if not chunk: raise OSError('Truncated body')
                        self._call('chunk_received',position=position,digest=digest)
                        with self._authority():
                            row=self._active(digest,generation,epoch)
                            # Charge transfer before write; reservation is retained on any error.
                            if row['wire']+len(chunk)>row['size']*4:
                                raise CacheError('Persistent body-byte budget exhausted')
                            with self._tx():
                                self.db.execute('UPDATE objects SET wire=wire+? WHERE digest=?',(len(chunk),digest))
                            self._call('before_write',digest=digest)
                            self._active(digest,generation,epoch)
                            fd=self._open(stage,os.O_RDWR)
                            with os.fdopen(fd,'r+b') as f:
                                f.seek(position); f.write(chunk); f.flush(); os.fsync(f.fileno())
                            self._call('after_file_sync',digest=digest)
                            position+=len(chunk)
                            with self._tx():
                                self.db.execute('UPDATE objects SET offset=? WHERE digest=?',(position,digest))
                            self._call('after_checkpoint',digest=digest)
                    with self._authority():
                        row=self._active(digest,generation,epoch)
                        self._seal_publish(row)
                    return
                except (OSError,TimeoutError) as exc:
                    if self.degraded:raise
                    # Network failures may retry; disk/DB failures are handled outside.
                    with self._authority():
                        row=self._active(digest,generation,epoch)
                        self._call('retry',offset=row['offset'],reason=type(exc).__name__)
                    continue
                finally:
                    if conn: conn.close()
        except BaseException as exc:
            with self._authority():
                row=self._row(digest)
                # A deadline-triggered shutdown can surface as a parser exception,
                # not OSError. Deadline state wins even if the sweep was delayed.
                if not self.degraded and not self.stopping and row and row['generation']==generation and row['state'] in ('QUEUED','DOWNLOADING','VERIFYING'):
                    self._expire(digest,generation)
                    row=self._row(digest)
                if isinstance(exc,sqlite3.Error):
                    self.degraded=True
                if not self.degraded and not self.stopping and row and row['generation']==generation and row['state'] not in ('CANCELLED','READY','PUBLISHING','PAUSED'):
                    try:
                        with self._tx():
                            self.db.execute("UPDATE objects SET state='FAILED' WHERE digest=?",(digest,))
                            self._finish_requests(digest,generation,'FAILED')
                    except sqlite3.Error:
                        self.degraded=True
                # PUBLISHING failure remains recoverable and must stop mutations.
                if row and row['generation']==generation and row['state']=='PUBLISHING': self.degraded=True
                self._call('worker_exit',error=type(exc).__name__,errno=getattr(exc,'errno',None),
                           sqlite_errorcode=getattr(exc,'sqlite_errorcode',None))

    def status(self,request_id):
        with self._authority():
            row=self.db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if row is None: raise CacheError('Unknown request')
            obj=self._row(row['digest'])
            return dict(request=dict(row),available=bool(obj and obj['state']=='READY'),
                        object=dict(obj) if obj else None,degraded=self.degraded,epoch=self.epoch,
                        remaining_seconds=max(0,row['deadline']-time.monotonic()) if row['deadline'] is not None and row['epoch']==self.epoch and row['state']=='WAITING' else None)

    def wait(self,request_id,timeout=15):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=self.status(request_id)
            if value['degraded']: raise CacheError('DEGRADED')
            if value['request']['state'] != 'WAITING': return value
            time.sleep(.01)
        raise TimeoutError('Request not terminal')

    def cancel(self,request_id,token=None):
        with self._authority():
            self._guard()
            req=self.status(request_id)['request']; row=self._row(req['digest'])
            if token is not None and (token.get('epoch')!=self.epoch or token.get('revision')!=req['revision'] or token.get('generation')!=req['generation']):raise CacheError('Stale cancellation')
            if req['state']!='WAITING': return 'TOO_LATE'
            if row['state']=='PUBLISHING': return 'TOO_LATE'
            try:
                with self._tx():
                    self.db.execute("UPDATE requests SET state='CANCELLED',result='CANCELLED',revision=revision+1 WHERE id=?",(request_id,))
                    remaining=self.db.execute("SELECT count(*) FROM requests WHERE digest=? AND generation=? AND state='WAITING'",(req['digest'],req['generation'])).fetchone()[0]
                    if not remaining: self.db.execute("UPDATE objects SET state='CANCELLED' WHERE digest=?",(req['digest'],))
                if not remaining:
                    lease=self.leases.get((req['digest'],req['generation']))
                    if lease:lease.cancelled.set()
                    path=self._stage(row)
                    if path.exists(): path.unlink(); self._syncdir(path.parent)
            except (sqlite3.Error,OSError):
                self.degraded=True
                raise
            return 'CANCELLED'

    @contextmanager
    def read(self,digest):
        with self._authority():
            self._guard()
            if sum(self.refs.values()) >= self.max_readers: raise CacheError('Reader limit')
            row=self._row(digest)
            if row is None or row['state']!='READY': raise CacheError('Not ready')
            fd=self._open(self._target(digest),os.O_RDONLY)
            try:
                self.db.execute('UPDATE objects SET access=? WHERE digest=?',(time.time_ns(),digest))
            except sqlite3.Error:
                os.close(fd);self.degraded=True;raise
            self.refs[digest]=self.refs.get(digest,0)+1
        try:
            with os.fdopen(fd,'rb') as f: yield f
        finally:
            with self._authority():
                self.refs[digest]-=1
                self.readers_changed.notify_all()

    def pin(self,digest,value,token):
        with self._authority():
            self._guard()
            row=self._row(digest)
            if row is None or row['state']!='READY' or row['generation']!=token.get('generation') or row['revision']!=token.get('revision') or self.epoch!=token.get('epoch'): raise CacheError('Stale pin')
            try:
                self.db.execute('UPDATE objects SET pin=?,revision=revision+1 WHERE digest=?',(int(bool(value)),digest))
            except sqlite3.Error:
                self.degraded=True;raise

    def gc_plan(self):
        with self._authority():
            return [{'digest':r['digest'],'generation':r['generation'],'revision':r['revision'],'epoch':self.epoch} for r in self.db.execute("SELECT * FROM objects WHERE pin=0 AND state IN ('READY','CANCELLED','FAILED','CORRUPT','PAUSED') ORDER BY access") if not self.refs.get(r['digest'],0)]

    def gc(self,plan):
        with self._authority():
            self._guard()
            removed=[]
            try:
                for item in plan:
                    row=self._row(item['digest'])
                    if row is None or row['generation']!=item['generation'] or row['revision']!=item['revision'] or self.epoch!=item['epoch'] or row['pin'] or self.refs.get(row['digest'],0) or row['state'] not in ('READY','CANCELLED','FAILED','CORRUPT','PAUSED'):
                        continue
                    with self._tx(): self.db.execute("UPDATE objects SET state='DELETING' WHERE digest=?",(row['digest'],))
                    self._call('deleting',digest=row['digest'])
                    for path in (self._stage(row),self._target(row['digest'])):
                        if path.exists(): path.unlink(); self._syncdir(path.parent)
                    self._call('after_delete',digest=row['digest'])
                    with self._tx(): self.db.execute('DELETE FROM objects WHERE digest=?',(row['digest'],))
                    removed.append(row['digest'])
            except (sqlite3.Error,OSError):
                self.degraded=True
                raise
            return removed

    def usage(self):
        with self._authority(): return self._usage()

    def begin_shutdown(self):
        with self._authority():
            self.stopping=True;self.sweeper_stop.set()
            for lease in self.leases.values():lease.cancelled.set()

    def close(self):
        self.begin_shutdown()
        if self.sweeper:self.sweeper.join()
        self.pool.shutdown(wait=True,cancel_futures=True)
        with self._authority():
            while any(self.refs.values()):self.readers_changed.wait()
            self.db.close()
            self.lockfile.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
