"""Portable real-process notify protocol tests, not a systemd substitute."""
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cacheweft.service import notify
from tests.daemon_fixture import Daemon
from tests.fixture import Origin

class NotifyTests(unittest.TestCase):
    def test_ready_then_stopping_from_actual_daemon(self):
        with tempfile.TemporaryDirectory(prefix='cw-n-',dir='/tmp') as tmp:
            base=Path(tmp);os.chmod(base,0o755)
            with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
                path=base/'notify';sock.bind(str(path));os.chmod(path,0o666);sock.settimeout(5)
                origin=Origin(base/'tls');daemon=Daemon(base/'user',origin)
                try:
                    with patch.dict(os.environ,NOTIFY_SOCKET=str(path)):info=daemon.start()
                    self.assertIn('READY=1',sock.recv(4096).decode())
                    self.assertEqual(info['epoch'],daemon.api('info')['epoch'])
                    daemon.stop();self.assertEqual(sock.recv(4096),b'STOPPING=1')
                    self.assertFalse(daemon.socket.exists());self.assertFalse((daemon.root/'ready.json').exists())
                finally:daemon.stop();origin.close()

    def test_failed_notify_is_failed_start_and_cleans_socket(self):
        with tempfile.TemporaryDirectory(prefix='cw-n-',dir='/tmp') as tmp:
            base=Path(tmp);os.chmod(base,0o755);origin=Origin(base/'tls');daemon=Daemon(base/'user',origin)
            try:
                with patch.dict(os.environ,NOTIFY_SOCKET=str(base/'absent')):
                    result=subprocess.run(daemon.argv(),capture_output=True,timeout=8)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(daemon.socket.exists());self.assertFalse((daemon.root/'ready.json').exists())
                daemon.start();daemon.stop()  # Failed readiness released the writer lock.
            finally:daemon.stop();origin.close()

    def test_no_notify_environment(self):
        with patch.dict(os.environ,{},clear=True):notify('READY=1')

if __name__=='__main__':unittest.main(verbosity=2)
