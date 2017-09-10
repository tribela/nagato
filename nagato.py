import collections
import argparse
import asyncio
import logging

from urllib.parse import urlparse


__version__ = '0.5.0'

_logger = logging.getLogger(__name__)

_loop = asyncio.get_event_loop()


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


class HttpStreamReader:
    def __init__(self, reader):
        self.reader = reader

    async def _iterline(self):
        line = await self.reader.readline()
        if not line:
            raise EOFError
        return line

    async def parse_request(self):
        """
        Parse an HTTP request and yield the results.

        line, result: line is the raw line. result is None if nothing parsed.

        int: the length of the following data bytes to be skipped for parsing.
        """
        # read the request line
        req_line = await self._iterline()
        method, url, version = req_line.decode().split(' ')
        version = version.rstrip('\r\n')
        yield req_line, (method, url, version)

        # read the header fields, check the body length
        body_len = 0
        chunked = False

        while True:
            field_line = await self._iterline()
            if field_line == b'\r\n':
                yield field_line, None
                break

            name, value = field_line.decode().split(':', 1)
            name = name.strip(' ').lower()
            value = value.lstrip(' ').rstrip('\r\n')

            if name == 'Content-Length'.lower():
                body_len = int(value)
            elif name == 'Transfer-Encoding'.lower():
                codings = list(x.strip(' ') for x in value.split(','))
                if 'chunked' in codings:
                    chunked = True

            yield field_line, (name, value)

        # skip the body by yielding its length
        if chunked:
            while True:
                line = await self._iterline()
                yield line, None

                chunk_len = int(line.rstrip(b'\r\n'), 16)
                yield chunk_len

                line = await self._iterline()
                yield line, None

                if chunk_len == 0:
                    return

        yield body_len


class ConnectRequest(Exception):
    def __init__(self, host, port, version):
        self.host = host
        self.port = port
        self.version = version


async def tunnel_stream(reader, writer, closing):
    try:
        while True:
            buf = await reader.read(65536)
            if not buf:
                break
            writer.write(buf)
    finally:
        writer.close()
        closing()


async def tunnel_n(reader, writer, n):
    while n > 0:
        buf = await reader.read(65536)
        n -= len(buf)
        writer.write(buf)

        if not buf and n > 0:
            raise EOFError


class NagatoStream:
    def __init__(self, proxy_reader, proxy_writer):
        self.proxy_reader = proxy_reader
        self.proxy_writer = proxy_writer
        self.server_reader = None
        self.server_writer = None

    async def handle_tunnel(self, host, port, version):
        # open a tunneling connection
        server_reader, server_writer \
            = await asyncio.open_connection(host, port, loop=_loop)

        self.proxy_writer.write((
            f'{version} 200 Connection established\r\n'
            f'Proxy-Agent: Nagato/{__version__}\r\n\r\n').encode())

        # set tunneling
        reader = tunnel_stream(self.proxy_reader, server_writer,
                               lambda: self.proxy_writer.close())
        writer = tunnel_stream(server_reader, self.proxy_writer,
                               lambda: server_writer.close())
        await asyncio.wait([reader, writer], loop=_loop)

    async def handle_request(self, parser):
        req_line, (method, url, version) = await parser.__anext__()
        _logger.info(f'{method} {url} {version}')

        if method == 'CONNECT':
            # handle the tunneling request
            host, port = url.rsplit(':', 1)
            port = int(port)
            raise ConnectRequest(host, port, version)

        # handle the HTTP request
        # handle the request line
        parsed = urlparse(url)
        try:
            host, port = parsed.netloc.rsplit(':', 1)
            port = int(port)
        except ValueError:
            host = parsed.netloc
            port = 80

        if self.server_reader is None or self.server_writer is None:
            self.server_reader, self.server_writer \
                = await asyncio.open_connection(host, port, loop=_loop)
            _loop.create_task(tunnel_stream(
                self.server_reader, self.proxy_writer,
                lambda: self.server_writer.close()))

        # replace by HTTPS
        # noinspection PyProtectedMember
        parsed = parsed._replace(scheme='https')
        req_line = f'{method} {parsed.geturl()} {version}\r\n'.encode()
        self.server_writer.write(req_line)

        # handle the header fields
        field_lines = []

        while True:
            field_line, field = await parser.__anext__()
            if field is None:
                field_lines.append(field_line)
                break

            name, value = field
            if name == 'Host'.lower():
                # host field segmentation
                field_lines.append(b'Ho')
                self.server_writer.write(b''.join(field_lines))
                await self.server_writer.drain()

                self.server_writer.write(f'st: {value}\r\n'.encode())
                field_lines = []
                continue
            elif name == 'Proxy-Connection'.lower():
                field_lines.append(f'Connection: {value}\r\n'.encode())
                continue

            field_lines.append(field_line)

        self.server_writer.write(b''.join(field_lines))

        # handle the body
        while True:
            try:
                result = await parser.__anext__()
            except StopAsyncIteration:
                break

            if isinstance(result, int):
                data_len = result
                if data_len > 0:
                    await tunnel_n(self.proxy_reader, self.server_writer,
                                   data_len)
            else:
                self.server_writer.write(result[0])

    async def handle_requests(self):
        http_reader = HttpStreamReader(self.proxy_reader)

        try:
            while True:
                # HTTP persistent connection
                parser = http_reader.parse_request()
                try:
                    await self.handle_request(parser)
                except ConnectRequest as r:
                    async for x in parser:
                        # drop the rest, assuming no body
                        pass
                    await self.handle_tunnel(r.host, r.port, r.version)
                    break
        except EOFError:
            pass
        finally:
            if self.server_writer is not None:
                self.server_writer.close()
            self.proxy_writer.close()


def nagato_stream(reader, writer):
    return _loop.create_task(NagatoStream(reader, writer).handle_requests())


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

    coro = asyncio.start_server(nagato_stream, args.host, args.port, loop=_loop)
    server = _loop.run_until_complete(coro)

    try:
        _loop.run_forever()
    except KeyboardInterrupt:
        pass

    server.close()
    _loop.run_until_complete(server.wait_closed())
    _loop.close()
