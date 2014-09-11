import re
import socket
import select
import threading

__version__ = '0.2.0'

HTTPVER = 'HTTP/1.1'
BUFLEN = 1024
VERSION = 'Nagato proxy/{0}'.format(__version__)


class MagicProxy():
    http = re.compile(r'https?://(?P<host>[^/]*)(?P<path>.*)')
    host = re.compile(r'(?P<host>[^:]+)((?::)(?P<port>\d+))?')

    def __init__(self, conn, address, timeout):
        self.client = conn
        self.client_buffer = ''
        self.timeout = timeout
        self.method, self.path, self.protocol = self.get_base_header()
        if self.method == 'CONNECT':
            self.method_connect()
        elif self.method in ('GET', 'POST', 'PUT',
                             'DELETE', 'OPTIONS', 'TRACE'):
            self.method_others()
        self.client.close()
        self.target.close()

    def get_base_header(self):
        while 1:
            self.client_buffer += self.client.recv(BUFLEN)
            eol = self.client_buffer.find('\n')
            if eol != -1:
                break

        print(self.client_buffer[:eol])
        data = self.client_buffer[:eol+1].split()
        self.client_buffer = self.client_buffer[eol+1:]
        return data

    def method_connect(self):
        self._connect_target(self.path)
        self.client.send('{0} 200 Connection established\n'
                         'Proxy-agent: {1}\n\n'.format(HTTPVER, VERSION))
        self.client_buffer = ''
        self._read_write()

    def method_others(self):
        matched = self.http.match(self.path)
        host = matched.group('host')
        path = matched.group('path')

        #magic trick
        path = 'https://%s%s' % (host, path)

        self._connect_target(host)
        self.target.send('{0} {1} {2}\n'.format(
            self.method, path, self.protocol))
        self.target.send(self.client_buffer)
        self.client_buffer = ''
        self._read_write()

    def _connect_target(self, host):
        matched = self.host.match(host)
        host = matched.group('host')
        port = matched.group('port') or 80

        addrinfo = socket.getaddrinfo(host, port)[0]
        sock_family = addrinfo[0]
        address = addrinfo[4]

        self.target = socket.socket(sock_family)
        self.target.connect(address)

    def _read_write(self):
        timeout_max = self.timeout / 3
        socks = [self.client, self.target]
        count = 0
        while 1:
            count += 1
            (recv, _, error) = select.select(socks, [], socks, 3)
            if error:
                break
            if recv:
                for _in in recv:
                    data = _in.recv(BUFLEN)
                    if _in is self.client:
                        out = self.target
                    else:
                        out = self.client

                    if data:
                        out.send(data)
                        count = 0
            if count == timeout_max:
                break


def start_proxy(host, port, ipv6, timeout, handler):
    if ipv6:
        sock_type = socket.AF_INET6
    else:
        sock_type = socket.AF_INET

    sock = socket.socket(sock_type)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    print("Listening on {0}:{1}.".format(host, port))
    sock.listen(0)
    threads = []
    while 1:
        (conn, address) = sock.accept()
        _thread = threading.Thread(target=handler,
                                   args=(conn, address, timeout))
        _thread.setDaemon(True)
        _thread.start()

        threads.append(_thread)
        for _thread in threads:
            if not _thread.isAlive():
                _thread.join()


def main():
    try:
        start_proxy('localhost', 8080, ipv6=False, timeout=60,
                    handler=MagicProxy)
    except KeyboardInterrupt:
        exit(0)

if __name__ == '__main__':
    main()
