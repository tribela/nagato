import struct
import asyncio
import random
import argparse
import logging

from urllib.parse import urlparse


__version__ = '0.6.1'

_logger = logging.getLogger(__name__)

_loop = asyncio.get_event_loop()

PROXY_RESP_504 = '{} 504 Gateway Timeout\r\n' \
    + 'Proxy-Agent: Nagato/{}\r\n'.format(__version__) \
    + 'Connection: close\r\n\r\n'

PROXY_RESP_200 = '{} 200 Connection Established\r\n' \
    + 'Proxy-Agent: Nagato/{}\r\n\r\n'.format(__version__)

PROXY_RESP_307 = '{} 307 Temporary Redirect\r\n' \
    + 'Location: {}\r\n' \
    + 'Proxy-Agent: Nagato/{}\r\n'.format(__version__) \
    + 'Connection: close\r\n\r\n'

host_abs_url = {}
"""host:port -> replace by HTTPS scheme?"""


def set_logger(level):
    try:
        log_level = [logging.WARNING, logging.INFO, logging.DEBUG][level]
        _logger.setLevel(log_level)
    except IndexError:
        _logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s {%(module)s:%(levelname)s}: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    _logger.addHandler(stream_handler)


@asyncio.coroutine
def tunnel_stream(reader, writer, closing):
    try:
        while True:
            buf = yield from reader.read(65536)
            if not buf:
                break
            writer.write(buf)
    finally:
        writer.close()
        closing()


def random_str(size):
    return ''.join(random.choice(
        'abcdefghijklmnopqrstuvwxyz'
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ') for _ in range(size))


def random_split(s, step):
    """
    :type s: str | bytes
    :type step: int
    """
    while s:
        rr = random.randrange(step-1)+1
        cur, s = s[:rr], s[rr:]
        yield cur


class HttpStream:
    def __init__(self, reader: asyncio.StreamReader, writer=None):
        """
        *writer* is used for tunneling if the argument *tunnel* is True.
        """
        self.reader = reader
        self.writer = writer
        self.field_done = False
        self.body_len = 0
        self.chunked = False
        self.chunk_len = None
        self.body_done = False

    @asyncio.coroutine
    def nextline(self):
        line = yield from self.reader.readline()
        if not line:
            raise EOFError
        return line

    @asyncio.coroutine
    def request_line(self, tunnel=False):
        """
        Return tuple ``method, url, version`` of type
        ``str, str, str``.
        """
        req_line = yield from self.nextline()
        if tunnel:
            self.writer.write(req_line)

        method, url, version = req_line.decode().split(' ')
        version = version.rstrip('\r\n')

        _logger.info('{} {} {}'.format(method, url, version))
        return method, url, version

    @asyncio.coroutine
    def status_line(self, tunnel=False):
        """
        Return tuple ``version, status, reason`` of type ``str, int, str``.
        """
        status_line = yield from self.nextline()
        if tunnel:
            self.writer.write(status_line)

        version, status, reason = status_line.decode().split(' ', 2)
        reason = reason.rstrip('\r\n')
        return version, int(status), reason

    @asyncio.coroutine
    def next_header_field(self, tunnel=False):
        """
        * ``tuple``: name, value of type ``str, str``
        * ``bytes``: empty line
        * ``None``: header finished
        """
        if self.field_done:
            return None

        field_line = yield from self.nextline()
        if tunnel:
            self.writer.write(field_line)

        if field_line == b'\r\n':
            self.field_done = True
            return field_line

        name, value = field_line.decode().split(':', 1)
        name = name.strip(' ')
        value = value.lstrip(' ').rstrip('\r\n')

        name_lower = name.lower()
        if name_lower == 'Content-Length'.lower():
            self.body_len = int(value)
        elif name_lower == 'Transfer-Encoding'.lower():
            codings = list(x.strip(' ') for x in value.split(','))
            if 'chunked' in codings:
                self.chunked = True

        return name, value

    @asyncio.coroutine
    def next_chunk_ready(self, tunnel=False):
        """
        * ``int``: chunk or body length
        * ``bytes``: line (expected to be empty)
        * ``None``: body finished

        The chunk or body data is not read by this method.
        """
        if self.body_done:
            return None

        if not self.chunked:
            self.body_done = True
            if self.body_len > 0:
                return self.body_len
            else:
                return None

        line = yield from self.nextline()
        if tunnel:
            self.writer.write(line)

        if self.chunk_len is None:
            chunk_len = int(line.rstrip(b'\r\n'), 16)
            self.chunk_len = chunk_len
            return chunk_len

        if self.chunk_len == 0:
            self.body_done = True

        self.chunk_len = None
        return line

    @asyncio.coroutine
    def tunnel_chunk(self):
        if not self.chunked:
            n = self.body_len
        else:
            n = self.chunk_len

        while n > 0:
            rlen = 65536 if n > 65536 else n
            buf = yield from self.reader.read(rlen)
            n -= len(buf)
            self.writer.write(buf)

            if not buf and n > 0:
                raise EOFError


class NagatoStream:
    def __init__(self, proxy_reader: asyncio.StreamReader, proxy_writer):
        self.proxy_reader = proxy_reader
        self.proxy_writer = proxy_writer
        self.server_reader = None
        self.server_writer = None
        self.host = None
        """HTTP server host"""
        self.port = None
        """HTTP server port"""
        self.last_url = None
        """last requested URL"""

    @asyncio.coroutine
    def read(self, n):
        buf = yield from self.proxy_reader.read(n)
        if not buf:
            raise EOFError
        return buf

    @asyncio.coroutine
    def handle_tunnel(self, host, port, version):
        # open a tunneling connection
        try:
            server_reader, server_writer \
                = yield from asyncio.open_connection(host, port, loop=_loop)
        except OSError:
            self.proxy_writer.write(PROXY_RESP_504.format(version).encode())
            self.proxy_writer.close()
            return
        else:
            self.proxy_writer.write(PROXY_RESP_200.format(version).encode())

        # handle TLS
        buf = yield from self.read(5)
        server_writer.write(buf)

        if buf.startswith(b'\x16\x03\x01'):
            # TLS Client Hello
            hello_len, = struct.unpack('>H', buf[3:])
            if hello_len > 85:
                # try to segment SNI
                buf = yield from self.read(85)
                server_writer.write(buf)
                yield from server_writer.drain()

        # start tunneling
        reader = tunnel_stream(self.proxy_reader, server_writer,
                               lambda: self.proxy_writer.close())
        writer = tunnel_stream(server_reader, self.proxy_writer,
                               lambda: server_writer.close())
        yield from asyncio.wait([reader, writer], loop=_loop)

    @asyncio.coroutine
    def handle_request(self, req_line):
        """:type req_line: (str, urllib.parse.ParseResult, str)"""
        # handle the request line
        method, url, version = req_line
        self.last_url = url

        parsed = urlparse(url)

        is_absolute = host_abs_url.get(
            '{}:{}'.format(self.host, self.port), True)
        if is_absolute:
            # replace by HTTPS
            # noinspection PyProtectedMember
            parsed = parsed._replace(scheme='https')
        else:
            # skip netloc, use host field instead
            # noinspection PyProtectedMember
            parsed = parsed._replace(scheme='', netloc='')

        self.server_writer.write('{} {} {}\r\n'.format(
            method, parsed.geturl(), version).encode())

        if not is_absolute:
            # generate dummy fields
            for _ in range(8):
                self.server_writer.write('X-{}: {}\r\n'.format(
                    random_str(16), random_str(128)).encode())
            yield from self.server_writer.drain()

        # handle the header fields except host
        http = HttpStream(self.proxy_reader, self.server_writer)
        field_lines = []
        host = None

        while True:
            field = yield from http.next_header_field()
            if field == b'\r\n' or field is None:
                break

            name, value = field
            name_lower = name.lower()

            if name_lower == 'Host'.lower():
                host = value
                continue
            elif name_lower == 'Proxy-Connection'.lower():
                field_lines.append('Connection: {}\r\n'.format(value).encode())
                continue

            field_lines.append('{}: {}\r\n'.format(name, value).encode())

        self.server_writer.write(b''.join(field_lines))

        if not is_absolute or host is not None:
            # handle the host field
            # mix cases, no space between field name and value
            host_line = 'hoSt:' + (host or self.host) + '\r\n'

            # field segmentation
            host_line = host_line.encode()
            for p in [host_line[:2], *random_split(host_line[2:], 6)]:
                self.server_writer.write(p)
                yield from self.server_writer.drain()
                yield from asyncio.sleep(random.randrange(10) / 1000.0,
                                         loop=_loop)
        # finish handling the header
        self.server_writer.write(b'\r\n')

        # handle the body
        while True:
            chunk_line = yield from http.next_chunk_ready(True)
            if chunk_line is None:
                break
            if isinstance(chunk_line, int):
                yield from http.tunnel_chunk()

    @asyncio.coroutine
    def handle_response(self):
        http = HttpStream(self.server_reader, self.proxy_writer)
        # handle the status line
        version, status, reason = yield from http.status_line()

        if 200 <= status < 300 or status == 304:
            # succeeded: continue to replace by HTTPS scheme
            host_abs_url['{}:{}'.format(self.host, self.port)] = True
        elif 400 <= status < 600 and status != 503:
            # failed: restart and then don't replace scheme
            # ignore the response from the server
            _logger.info('{} {} {} -> 307 Temporary Redirect'.format(
                version, status, reason))
            host_abs_url['{}:{}'.format(self.host, self.port)] = False

            self.proxy_writer.write(PROXY_RESP_307.format(
                version, self.last_url.geturl()).encode())
            self.proxy_writer.close()
            raise EOFError
        # inconclusive for the other status codes

        self.proxy_writer.write('{} {} {}\r\n'.format(
            version, status, reason).encode())

        # just tunnel the rest
        while True:
            field = yield from http.next_header_field(True)
            if field is None:
                break

        while True:
            chunk_line = yield from http.next_chunk_ready(True)
            if chunk_line is None:
                break
            if isinstance(chunk_line, int):
                yield from http.tunnel_chunk()

    @asyncio.coroutine
    def handle_requests(self, req_line):
        """:type req_line: (str, str, str)"""
        try:
            while True:
                # HTTP persistent connection
                yield from self.handle_request(req_line)
                req_line = yield from HttpStream(
                    self.proxy_reader).request_line()
        except EOFError:
            pass

    @asyncio.coroutine
    def handle_responses(self):
        host = '{}:{}'.format(self.host, self.port)
        try:
            while host_abs_url.get(host) is None:
                # HTTP persistent connection
                yield from self.handle_response()
        except EOFError:
            self.proxy_writer.close()
            self.server_writer.close()
            return

        # decided, so switch to tunnel everything
        yield from tunnel_stream(self.server_reader, self.proxy_writer,
                                 lambda: self.server_writer.close())

    @asyncio.coroutine
    def handle_streams(self):
        # connect to the server
        http = HttpStream(self.proxy_reader)
        req_line = yield from http.request_line()
        """:type: (str, urllib.parse.ParseResult, str)"""
        method, url, version = req_line

        try:
            parsed = urlparse(url)
            host, port = parsed.netloc.rsplit(':', 1)
            port = int(port)
        except ValueError:
            host = parsed.netloc
            port = 80

        if method == 'CONNECT':
            # handle the tunneling request
            host, port = url.rsplit(':', 1)
            port = int(port)

            # drop the rest, assuming no body
            while True:
                line = yield from http.nextline()
                if line == b'\r\n':
                    break

            yield from self.handle_tunnel(host, port, version)
            return

        # start relaying
        try:
            self.server_reader, self.server_writer \
                = yield from asyncio.open_connection(host, port, loop=_loop)
            self.host, self.port = host, port
        except OSError:
            self.proxy_writer.write(PROXY_RESP_504.format(version).encode())
            self.proxy_writer.close()
            return

        reader = self.handle_requests(req_line)
        writer = self.handle_responses()
        yield from asyncio.wait([reader, writer], loop=_loop)


@asyncio.coroutine
def nagato_stream(reader, writer):
    streams = NagatoStream(reader, writer)
    try:
        yield from streams.handle_streams()
    except EOFError:
        pass
    finally:
        if streams.server_writer is not None:
            streams.server_writer.close()
        streams.proxy_writer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', help="Host to bind",
                        default="localhost")
    parser.add_argument('-p', '--port', type=int,
                        help="Port to bind", default="8080")
    parser.add_argument('-v', '--verbose', default=0, action='count',
                        help="Verbose output.")

    args = parser.parse_args()

    set_logger(args.verbose)

    _logger.info('Nagato {} Starting on {}:{}'.format(
        __version__, args.host, args.port))

    # noinspection PyTypeChecker
    coro = asyncio.start_server(nagato_stream, args.host, args.port, loop=_loop)
    server = _loop.run_until_complete(coro)

    try:
        _loop.run_forever()
    except KeyboardInterrupt:
        pass

    server.close()
    _loop.run_until_complete(server.wait_closed())
    _loop.close()
