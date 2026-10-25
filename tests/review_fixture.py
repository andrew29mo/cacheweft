"""Real TLS and SQLite contention through the formal managed backend/daemon."""
import os
from pathlib import Path
import sqlite3
import threading
import time

from cacheweft import Cache,Spec,CacheError
from cacheweft.http import HTTPS
from .fixture import DATA
from .daemon_fixture import Daemon,require


def hold(path,kind,seconds=.15):
    db=sqlite3.connect(path,isolation_level=None,check_same_thread=False)
    db.execute('BEGIN' if kind=='reader' else 'BEGIN IMMEDIATE')
    db.execute('SELECT * FROM objects').fetchall()
    release=threading.Thread(target=lambda:(time.sleep(seconds),db.execute('ROLLBACK')))
    release.start();return db,release


def admission(base,origin):
    daemon=Daemon(base,origin);holder=None
    try:
        daemon.start();daemon.submit('warm',3);require(daemon.terminal('warm')['request']['state']=='SUCCEEDED','Warm artifact failed')
        info=daemon.api('info');epoch=info['epoch'];before_busy=info['sqlite_busy']['COMMIT']
        holder=hold(daemon.root/'catalog.db','reader');start=time.monotonic();rejected=False
        try:daemon.api('fetch',id='late_hit',artifact='fixture-v1',seconds=.1,epoch=epoch)
        except CacheError as exc:
            require('Submission deadline expired before admission' in str(exc),'Not deadline rejection');rejected=True
        require(rejected,'Late cached admission succeeded')
        elapsed=time.monotonic()-start;holder[1].join();holder[0].close();holder=None
        absent=False
        try:daemon.api('status',id='late_hit')
        except CacheError:absent=True
        after=daemon.api('info');busy_delta=after['sqlite_busy']['COMMIT']-before_busy
        require(absent and not after['degraded'] and busy_delta>0,'Admission lacked actual COMMIT contention or left a receipt/retired authority')
        daemon.submit('fresh',3);require(daemon.terminal('fresh')['request']['state']=='SUCCEEDED','Fresh hit failed')
        return dict(mode='admission_commit_reader',subject='actual daemon',duration=.1,reader_hold=.15,
                    sqlite_busy_timeout_ms=200,rejected=True,no_receipt=True,healthy_after=True,seconds=elapsed,
                    busy_statement='COMMIT',busy_count=busy_delta)
    finally:
        if holder:holder[1].join();holder[0].close()
        daemon.stop()


def publication(base,origin,*,kind='reader',shared=False):
    daemon=Daemon(base,origin)  # supplies only manifest data, no product replacement
    spec=Spec('fixture-v1',origin.url,len(DATA),daemon.digest)
    holders=[];reached=[];intent=[];blocked=[];initial=None
    origin.mode='hold' if shared else 'normal';origin.arrived.clear();origin.release.clear()
    def hook(point,info):
        if point=='verified':
            remaining=cache.status('short')['request']['deadline']-time.monotonic()
            require(remaining>.09,'Did not reach contention before deadline')
            time.sleep(remaining-.08)
            holders.append(hold(cache.root/'catalog.db',kind))
            require(time.monotonic()<cache.status('short')['request']['deadline'],'Lock was not acquired before short cutoff')
            reached.append(True)
        if point=='publish_intent':intent.append(time.monotonic())
        if point=='sqlite_busy':blocked.append(info)
    try:
        with Cache(Path(base)/'backend',[spec],HTTPS(origin.origin,'127.0.0.1',origin.cert),manifest_revision=1,hook=hook) as cache:
            initial=cache.submit('short','fixture-v1',seconds=1)
            if shared:
                require(origin.arrived.wait(2),'No coalesced request barrier')
                cache.submit('long','fixture-v1',seconds=4);origin.release.set()
            short=cache.wait('short',6)
            if shared:long=cache.wait('long',6)
            for db,t in holders:t.join();db.close()
            holders.clear()
            require(reached and short['request']['state']=='TIMED_OUT','Expired subscriber succeeded')
            statement='BEGIN IMMEDIATE' if kind=='writer' else 'COMMIT'
            busy=[x for x in blocked if x['statement']==statement]
            require(busy and any(x['monotonic_at']<initial['request']['deadline'] for x in busy),'No actual contention before short cutoff')
            require(short['request']['deadline']==initial['request']['deadline'] and short['request']['revision']==initial['request']['revision']+1,'Deadline/revision changed incorrectly')
            require(cache.db.execute('PRAGMA busy_timeout').fetchone()[0]==200 and not cache.degraded,'Busy budget not restored')
            if shared:
                require(long['request']['state']=='SUCCEEDED' and intent and intent[0]>initial['request']['deadline'],'Did not cross short deadline with a live long subscriber')
                with cache.read(daemon.digest) as f:require(f.read()==DATA,'Wrong published bytes')
            else:require(not intent and not short['available'],'Expired publication reached intent')
            return dict(mode='shared_'+kind+'_wait' if shared else 'publish_commit_reader',subject='formal managed backend with scheduling barrier',
                        sqlite_busy_timeout_ms=200,short_state=short['request']['state'],short_deadline_unchanged=True,
                        short_revision_delta=1,long_state=long['request']['state'] if shared else None,
                        intent_after_short_deadline=intent[0]-initial['request']['deadline'] if shared else None,
                        no_intent=not intent,busy_timeout_restored=True,healthy_after=True,bytes_correct=True if shared else None,
                        lock_acquired_before_cutoff=True,busy_before_cutoff=True,busy_statement=statement,busy_count=len(busy))
    finally:
        origin.release.set()
        for db,t in holders:t.join();db.close()
