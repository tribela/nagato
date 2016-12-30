import asyncore
import socket

from six.moves.urllib.parse import urlparse


class Sock(asyncore.dispatcher):

    def __init__(self, sock):
        asyncore.dispatcher.__init__(self, sock)
        self.write_buffer = b''

    def set_other(self, other):
        self.other = other

    def readable(self):
        return not self.other.write_buffer

    def handle_read(self):
        self.other.write_buffer += self.recv(4096)

    def handle_write(self):
        sent = self.send(self.write_buffer)
        self.write_buffer = self.write_buffer[sent:]

    def handle_close(self):
        self.close()
        if self.other.other:
            self.other.close()
            self.other = None


class NagatoServer(asyncore.dispatcher):

    def __init__(self, host, port):
        asyncore.dispatcher.__init__(self)

        self.create_socket()
        self.set_reuse_addr()
        self.bind((host, port))

        self.listen(5)

    def handle_accept(self):
        pair = self.accept()
        if not pair:
            return
        c_sock, addr = pair

        first_line = self.get_dest(c_sock)
        method, url, version = first_line.split(b' ', 2)
        parsed = urlparse(url)
        try:
            host, port = parsed.netloc.rsplit(b':')
            port = int(port)
        except:
            host = parsed.netloc
            port = 80

        first_line = b' '.join((
            method,
            parsed._replace(scheme=b'https').geturl(),  # Magic trick
            version,
        ))

        try:
            s_sock = socket.create_connection((host, port))
        except socket.error as e:
            # TODO: logging
            c_sock.close()
        else:
            s_sock.send(first_line)
            a, b = Sock(c_sock), Sock(s_sock)
            a.set_other(b)
            b.set_other(a)

    @classmethod
    def get_dest(cls, sock):
        first_line = NagatoServer.get_line(sock)
        # headers = cls.get_headers(sock)

        return first_line #, headers

    @classmethod
    def get_line(cls, sock):
        buff = b''
        while not buff.endswith(b'\r\n'):
            buff += sock.recv(1)

        return buff

    @classmethod
    def get_headers(cls, sock):
        headers = {}
        while True:
            line = cls.get_line(sock).rstrip(b'\r\n')
            if not line:
                break
            key, val = line.split(b': ', 1)
            headers[key] = val

        return headers


def main():
    server = NagatoServer('localhost', 8080)
    try:
        asyncore.loop()
    finally:
        server.close()
