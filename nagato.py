import argparse
import asyncio
import logging

from urllib.parse import urlparse

__version__ = '0.3.2'

logger = logging.getLogger(__name__)

loop = asyncio.get_event_loop()

parser = argparse.ArgumentParser()
parser.add_argument('-H', '--host', help="Host to bind", default="localhost")
parser.add_argument('-p', '--port', type=int,
                    help="Port to bind", default="8080")
parser.add_argument('-v', '--verbose', default=0, action='count',
                    help="Verbose output.")


class ServerConn(asyncio.Protocol):

    def connection_made(self, transport):
        self.connected = True
        self.transport = transport

    def data_received(self, data):
        self.server_transport.write(data)

    def connection_lost(self, *args):
        self.connected = False

    def eof_received(self):
        super().eof_received()
        if self.server_transport:
            self.server_transport.close()


class NagatoProc(asyncio.Protocol):

    def connection_made(self, transport):
        self.transport = transport
        self.buffer = b''
        self.client = None

    @asyncio.coroutine
    def send_data(self, data):

        if self.client is None or not self.client.connected:
            first_line, rest = data.split(b'\r\n', 1)
            logger.info(first_line.decode('utf-8'))
            method, url, httpver = first_line.split(b' ', 2)

            if method == b'CONNECT':
                host, port = url.rsplit(b':', 1)
                port = int(port)
                headers, rest = rest.rsplit(b'\r\n\r\n', 1)
                protocol, client = yield from loop.create_connection(
                    ServerConn, host, port)
                client.server_transport = self.transport
                self.transport.write(
                    httpver + b' 200 Connection established\r\n'
                    b'Proxy-Agent: Nagato/' +
                    __version__.encode('utf-8') + b'\r\n\r\n'
                )
                client.transport.write(rest)
            else:
                parsed = urlparse(url)

                try:
                    host, port = parsed.netloc.rsplit(b':', 1)
                    port = int(port)
                except:
                    host = parsed.netloc
                    port = 80

                first_line = b' '.join((
                    method,
                    parsed._replace(scheme=b'https').geturl(),
                    httpver,
                ))

                protocol, client = yield from loop.create_connection(
                    ServerConn, host, port)
                client.server_transport = self.transport
                client.transport.write(first_line + b'\r\n' + rest)

            self.client = client
        else:
            self.client.transport.write(data)

    def data_received(self, data):
        if self.client is None and b'\r\n\r\n' not in (self.buffer + data):
            self.buffer += data
        else:
            data = self.buffer + data
            self.buffer = b''
            asyncio.Task(self.send_data(data))

    def eof_received(self):
        super().eof_received()
        self.transport.close()
        if self.client:
            self.client.transport.close()


@asyncio.coroutine
def initialize(loop, host, port):
    yield from loop.create_server(NagatoProc, host, port)


def set_logger(level):
    try:
        log_level = [logging.WARNING, logging.INFO, logging.DEBUG][level]
        logger.setLevel(log_level)
    except IndexError:
        logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s {%(module)s:%(levelname)s}: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)


def main():
    args = parser.parse_args()

    set_logger(args.verbose)

    logger.info('Starting')
    asyncio.Task(initialize(loop, args.host, args.port))
    loop.run_forever()
