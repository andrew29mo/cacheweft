import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import ssl
import time
import unittest
import cacheweft


def modules():
    root=Path(cacheweft.__file__).parent
    return {'cacheweft/'+p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.py'))}


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);a=parser.parse_args()
    suite=unittest.defaultTestLoader.discover('tests',top_level_dir='.')
    def ids(s):
        for t in s:
            if isinstance(t,unittest.TestSuite):yield from ids(t)
            else:yield t.id()
    test_ids=list(ids(suite));start=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
    value=dict(passed=result.wasSuccessful() and not result.skipped,tests=result.testsRun,test_ids=test_ids,
               failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
               seconds=time.monotonic()-start,platform=platform.system(),python=platform.python_version(),
               sqlite=sqlite3.sqlite_version,openssl=ssl.OPENSSL_VERSION,modules=modules())
    Path(a.output).write_text(json.dumps(value,indent=2)+'\n')
    raise SystemExit(0 if value['passed'] else 1)

if __name__=='__main__':run()
