"""Challenge report freshness and cleanup; happy paths use the real pilot."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from .try_batch import await_cache, execute


class FailurePaths(unittest.TestCase):
    def test_preflight_failure_replaces_previous_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'report.json'
            output.write_text('{"passed": true}')
            args=argparse.Namespace(cacheweft='/nonexistent/cacheweft',consumer=str(Path(tmp)/'missing'),output=str(output))
            with self.assertRaises(FileNotFoundError):execute(args)
            value=json.loads(output.read_text())
            self.assertFalse(value['passed'])
            self.assertEqual(value['status'],'preflight_failed')

    def test_query_timeout_reaps_unclaimed_daemon(self):
        proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
        def query():raise subprocess.TimeoutExpired('cache info',12)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):await_cache(proc,query)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:proc.kill();proc.wait()


if __name__=='__main__':unittest.main()
