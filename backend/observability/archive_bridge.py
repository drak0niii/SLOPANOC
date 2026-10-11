"""Explicit loopback-only OTLP/HTTP bridge for local development and validation."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Thread
from .archive import MAX_REQUEST

@contextmanager
def local_bridge(archive):
    capacity=BoundedSemaphore(4)
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def process_request(self, request, address):
            # Admission precedes header parsing and creation of a handler thread.
            if not capacity.acquire(blocking=False):
                try:
                    request.settimeout(.1)
                    request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request,address)
            except BaseException:
                capacity.release()
                raise
        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request,address)
            finally:
                capacity.release()
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(2)
            super().setup()
        def do_POST(self):
            self.connection.settimeout(2)
            if self.path!='/v1/traces' or self.headers.get('Content-Type')!='application/x-protobuf':
                self.reply(400);return
            try: size=int(self.headers.get('Content-Length','0'))
            except ValueError: self.reply(400);return
            if not 0<size<=MAX_REQUEST or self.headers.get('Content-Encoding'):
                self.reply(413);return
            try:
                data=self.rfile.read(size)
                if len(data)!=size: self.reply(400);return
                archive.submit(data)
                self.reply(200)
            except Exception: self.reply(503)
        def reply(self,status):
            self.send_response(status);self.send_header('Content-Type','application/x-protobuf')
            self.send_header('Content-Length','0');self.end_headers()
        def log_message(self,*args): pass
    server=Server(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port)
    finally: server.shutdown();server.server_close();thread.join(2)
