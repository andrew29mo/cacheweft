"""Direct HTTPS with explicit address allowlist, no redirects or decompression."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, context, timeout):
        super().__init__(host, port, context=context, timeout=timeout)
        self.address = str(ipaddress.ip_address(address))

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class HTTPS:
    def __init__(self, origin, address, cafile=None, timeout=2):
        u = urlsplit(origin)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ValueError('Explicit HTTPS origin required')
        self.host, self.port = u.hostname, u.port or 443
        self.address, self.timeout = str(ipaddress.ip_address(address)), timeout
        self.context = ssl.create_default_context(cafile=cafile)

    def get(self, url, headers):
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname != self.host or (u.port or 443) != self.port or u.username or u.password or u.fragment:
            raise ValueError('Source outside configured origin')
        conn = PinnedHTTPS(self.host, self.port, self.address, self.context, self.timeout)
        try:
            conn.request('GET', u.path + ('?' + u.query if u.query else ''), headers={'Accept-Encoding': 'identity', **headers})
            response = conn.getresponse()
            return conn, response
        except BaseException:
            conn.close()
            raise
