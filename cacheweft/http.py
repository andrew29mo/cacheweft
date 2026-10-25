"""Direct HTTPS with explicit address allowlist, no redirects or decompression."""
import http.client
import errno
import ipaddress
import socket
import select
import ssl
import threading
import time
from urllib.parse import urlsplit


class Lease:
    """Live job interest. Only a new subscriber may extend this shared deadline."""
    def __init__(self, deadline=None):
        self.deadline=deadline
        self.cancelled=threading.Event()

    def expired(self):
        return self.cancelled.is_set() or (self.deadline is not None and time.monotonic()>=self.deadline)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, context, timeout):
        super().__init__(host, port, context=context, timeout=timeout)
        self.address = str(ipaddress.ip_address(address))
        self.raw=None;self.raw_lock=threading.Lock();self.done=threading.Event();self.watcher=None;self.lease=None
        self.parsing_headers=False;self.response=None

    def interrupt(self):
        with self.raw_lock:
            if self.raw is not None:
                try:self.raw.shutdown(socket.SHUT_RDWR)
                except OSError:pass

    def watch(self,lease):
        self.lease=lease
        def worker():
            while not self.done.wait(.01):
                if lease.expired():self.interrupt()
        self.watcher=threading.Thread(target=worker,name='cacheweft-transport-deadline',daemon=True)
        self.watcher.start()

    def connect(self):
        sock=socket.socket(socket.AF_INET6 if ':' in self.address else socket.AF_INET,socket.SOCK_STREAM)
        sock.setblocking(False)
        # Keep an independent socket handle: http.client may close its own
        # SSLSocket wrapper after HTTP/1.0 headers while body file refs survive.
        with self.raw_lock:self.raw=sock.dup()
        try:
            error=sock.connect_ex((self.address,self.port));end=time.monotonic()+self.timeout
            if error not in (0,errno.EINPROGRESS,errno.EWOULDBLOCK,errno.EALREADY):raise OSError(error,'Connect failed')
            while error:
                if self.lease is not None and self.lease.expired():raise TimeoutError('Connect interest expired')
                if time.monotonic()>=end:raise TimeoutError('Connect inactivity timeout')
                _,writable,failed=select.select([],[sock],[sock],min(.02,max(0,end-time.monotonic())))
                if writable or failed:
                    error=sock.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
                    if error:raise OSError(error,'Connect failed')
            sock.settimeout(self.timeout)
            with self.raw_lock:
                self.sock=self._context.wrap_socket(sock,server_hostname=self.host,do_handshake_on_connect=False)
            self.sock.do_handshake()
        except BaseException:
            sock.close()
            raise

    def close(self):
        if self.parsing_headers:
            # Internal HTTP connection detachment is not end-of-body cleanup.
            super().close();return
        self.done.set()
        if self.response is not None:self.response.close();self.response=None
        super().close()
        with self.raw_lock:
            if self.raw is not None:self.raw.close();self.raw=None
        if self.watcher and self.watcher is not threading.current_thread():self.watcher.join(1)


class HTTPS:
    def __init__(self, origin, address, cafile=None, timeout=2):
        u = urlsplit(origin)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ValueError('Explicit HTTPS origin required')
        self.host, self.port = u.hostname, u.port or 443
        self.address, self.timeout = str(ipaddress.ip_address(address)), timeout
        self.context = ssl.create_default_context(cafile=cafile)

    def get(self, url, headers, *, lease=None):
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname != self.host or (u.port or 443) != self.port or u.username or u.password or u.fragment:
            raise ValueError('Source outside configured origin')
        conn = PinnedHTTPS(self.host, self.port, self.address, self.context, self.timeout)
        if lease is not None:
            if lease.expired():raise TimeoutError('Transfer interest expired')
            conn.watch(lease)
        try:
            conn.request('GET', u.path + ('?' + u.query if u.query else ''), headers={'Accept-Encoding': 'identity', **headers})
            conn.parsing_headers=True
            try:response = conn.getresponse()
            finally:conn.parsing_headers=False
            conn.response=response
            return conn, response
        except BaseException:
            conn.close()
            raise
