import struct
import asyncio
import random
import argparse
import logging

from urllib.parse import urlparse


__version__ = '0.5.0'

_logger = logging.getLogger(__name__)

_loop = asyncio.get_event_loop()

PROXY_RESP_504 = '{} 504 Gateway Timeout\r\n' \
    + 'Proxy-Agent: Nagato/{}\r\n'.format(__version__) \
    + 'Connection: close\r\n\r\n'

PROXY_RESP_200 = '{} 200 Connection Established\r\n' \
    + 'Proxy-Agent: Nagato/{}\r\n\r\n'.format(__version__)


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


@asyncio.coroutine
def tunnel_n(reader: asyncio.StreamReader, writer, n):
    while n > 0:
        buf = yield from reader.read(65536)
        n -= len(buf)
        writer.write(buf)

        if not buf and n > 0:
            raise EOFError


def random_str(size):
    return ''.join(random.choice(
        'abcdefghijklmnopqrstuvwxyz'
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ') for _ in range(size))


def random_split(s, step):
    result = []
    while s:
        rr = random.randrange(step-1)+1
        cur, s = s[:rr], s[rr:]
        result.append(cur)
    return result


class NagatoStream:
    def __init__(self, proxy_reader: asyncio.StreamReader, proxy_writer):
        self.proxy_reader = proxy_reader
        self.proxy_writer = proxy_writer
        self.server_reader = None
        self.server_writer = None
        self.host = None
        self.port = None

    @asyncio.coroutine
    def read(self, n):
        buf = yield from self.proxy_reader.read(n)
        if not buf:
            raise EOFError
        return buf

    @asyncio.coroutine
    def nextline(self):
        line = yield from self.proxy_reader.readline()
        if not line:
            raise EOFError
        return line

    @asyncio.coroutine
    def handle_req_line(self):
        req_line = yield from self.nextline()
        method, url, version = req_line.decode().split(' ')
        version = version.rstrip('\r\n')

        _logger.info('{} {} {}'.format(method, url, version))
        return method, urlparse(url), version

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
        # handle the request line
        method, url, version = req_line

        # replace by HTTPS
        # url = url._replace(scheme='https')

        # skip netloc, use host field instead
        # noinspection PyProtectedMember
        url = url._replace(scheme='', netloc='')
        self.server_writer.write('{} {} {}\r\n'.format(
            method, url.geturl(), version).encode())

        # generate dummy fields
        for _ in range(8):
            self.server_writer.write('X-{}: {}\r\n'.format(
                random_str(16), random_str(128)).encode())
        yield from self.server_writer.drain()

        # handle the header fields except host
        # check the body length
        field_lines = []
        host = None
        body_len = 0
        chunked = False

        while True:
            field_line = yield from self.nextline()
            if field_line == b'\r\n':
                break

            name, value = field_line.decode().split(':', 1)
            name = name.strip(' ').lower()
            value = value.lstrip(' ').rstrip('\r\n')

            if name == 'Host'.lower():
                host = value
                continue
            elif name == 'Proxy-Connection'.lower():
                field_lines.append('Connection: {}\r\n'.format(value).encode())
                continue
            elif name == 'Content-Length'.lower():
                body_len = int(value)
            elif name == 'Transfer-Encoding'.lower():
                codings = list(x.strip(' ') for x in value.split(','))
                if 'chunked' in codings:
                    chunked = True

            field_lines.append(field_line)

        self.server_writer.write(b''.join(field_lines))

        # handle the host field
        # mix cases, no space between field name and value
        host_line = 'hoSt:' + (host or self.host) + '\r\n\r\n'

        # field segmentation
        host_line = host_line.encode()
        for p in [host_line[:2], *random_split(host_line[2:], 6)]:
            self.server_writer.write(p)
            yield from self.server_writer.drain()
            yield from asyncio.sleep(random.randrange(10) / 1000.0, loop=_loop)

        # handle the body
        if chunked:
            while True:
                line = yield from self.nextline()
                self.server_writer.write(line)

                chunk_len = int(line.rstrip(b'\r\n'), 16)
                if chunk_len > 0:
                    yield from tunnel_n(self.proxy_reader, self.server_writer,
                                        chunk_len)

                line = yield from self.nextline()
                self.server_writer.write(line)

                if chunk_len == 0:
                    return

        if body_len > 0:
            yield from tunnel_n(self.proxy_reader, self.server_writer,
                                body_len)

    @asyncio.coroutine
    def handle_requests(self, req_line):
        try:
            while True:
                # HTTP persistent connection
                yield from self.handle_request(req_line)
                req_line = yield from self.handle_req_line()
        except EOFError:
            pass

    @asyncio.coroutine
    def handle_streams(self):
        # connect to the server
        req_line = yield from self.handle_req_line()
        method, url, version = req_line

        try:
            host, port = url.netloc.rsplit(':', 1)
            port = int(port)
        except ValueError:
            host = url.netloc
            port = 80

        if method == 'CONNECT':
            # handle the tunneling request
            host, port = url.path.rsplit(':', 1)
            port = int(port)

            # drop the rest, assuming no body
            while True:
                line = yield from self.nextline()
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
        writer = tunnel_stream(self.server_reader, self.proxy_writer,
                               lambda: self.server_writer.close())
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
