import asyncio
import logging

from urllib.parse import urlparse

__version__ = '0.1.0'

logger = logging.getLogger(__name__)

loop = asyncio.get_event_loop()


class ServerConn(asyncio.Protocol):

    def connection_made(self, transport):
        self.connected = True
        self.transport = transport

    def data_received(self, data):
        self.server_transport.write(data)

    def connection_lost(self, *args):
        self.connected = False


class NagatoProc(asyncio.Protocol):

    def connection_made(self, transport):
        self.transport = transport
        self.buffer = b''
        self.client = None

    @asyncio.coroutine
    def send_data(self, data):

        if self.client is None or not self.client.connected:
            first_line, rest = data.split(b'\r\n', 1)
            print(first_line.decode('utf-8'))
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


@asyncio.coroutine
def initialize(loop):
    yield from loop.create_server(NagatoProc, 'localhost', 8080)


def main():
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)
    logger.info('Starting')
    asyncio.Task(initialize(loop))
    loop.run_forever()
