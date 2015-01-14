import argparse
import logging
import re
import socket
import select
import threading

from logging.handlers import RotatingFileHandler

__version__ = '0.2.0'

HTTPVER = 'HTTP/1.1'
BUFLEN = 1024
VERSION = 'Nagato proxy/{0}'.format(__version__)

logger = logging.getLogger(__name__)


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

        logger.info(self.client_buffer[:eol])
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
        self.fix_server_header()
        self._read_write()

    def fix_server_header(self):
        data = ''
        while '\r\n\r\n' not in data:
            data += self.target.recv(BUFLEN)

        head, other = data.split('\r\n\r\n', 1)
        response, headers = head.split('\r\n', 1)

        new_headers = []
        for line in headers.split('\r\n'):
            try:
                key, val = line.split(': ', 1)
                if key.lower() == 'connection':
                    if val.lower() in ('keep-alive', 'persist'):
                        new_headers.append('Connection: close')
                    else:
                        new_headers.append(line)
                elif key.lower() != 'proxy-connection':
                    new_headers.append(line)
            except ValueError:
                new_headers.append(line)


        self.client.send('{0}{1}\n\n{2}'.format(
            response, '\r\n'.join(new_headers), other))

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
    logger.info("Listening on {0}:{1}.".format(host, port))
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


parser = argparse.ArgumentParser()
parser.add_argument('-H', '--host', help='Host to bind', default='localhost')
parser.add_argument('-p', '--port', type=int, help='Port to bind', default=8080)
parser.add_argument('-v', '--verbose', default=0, action='count',
                    help='Verbose output.')
parser.add_argument('-l', '--logfile', help='Filename for logging')


def init_logging(level, filename=None):
    if not level:
        log_level = logging.WARNING
    elif level == 1:
        log_level = logging.INFO
    else:
        log_level = logging.DEBUG

    formatter = logging.Formatter(
        '%(asctime)s {%(module)s:%(levelname)s}: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)


    if filename:
        file_handler = RotatingFileHandler(filename)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def main():
    args = parser.parse_args()
    host = args.host
    port = args.port
    init_logging(args.verbose, filename=args.logfile)
    try:
        start_proxy(host, port, ipv6=False, timeout=60,
                    handler=MagicProxy)
    except KeyboardInterrupt:
        exit(0)

if __name__ == '__main__':
    main()
