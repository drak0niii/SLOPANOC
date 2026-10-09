"""Explicit loopback-only OTLP/HTTP bridge for local development and validation."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Thread
from .archive import MAX_REQUEST

@contextmanager
def local_bridge(archive):
    capacity=BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(2)
            if self.path!='/v1/traces' or self.headers.get('Content-Type')!='application/x-protobuf':
                self.reply(400);return
            try: size=int(self.headers.get('Content-Length','0'))
            except ValueError: self.reply(400);return
            if not 0<size<=MAX_REQUEST or self.headers.get('Content-Encoding'):
                self.reply(413);return
            if not capacity.acquire(blocking=False): self.reply(503);return
            try:
                data=self.rfile.read(size)
                if len(data)!=size: self.reply(400);return
                archive.submit(data)
                self.reply(200)
            except Exception: self.reply(503)
            finally: capacity.release()
        def reply(self,status):
            self.send_response(status);self.send_header('Content-Type','application/x-protobuf')
            self.send_header('Content-Length','0');self.end_headers()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port)
    finally: server.shutdown();server.server_close();thread.join(2)
