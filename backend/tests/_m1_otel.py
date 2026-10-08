"""Isolated loopback OTLP capture, no disk telemetry, ADC or external services."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Condition
import time
import gzip


@contextmanager
def receiver(status=200):
    records=[]
    condition=Condition()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data=self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('Content-Encoding')=='gzip':
                data=gzip.decompress(data)
            with condition:
                records.append((self.path,data,dict(self.headers)))
                condition.notify_all()
            self.send_response(status)
            self.send_header('Content-Type','application/x-protobuf')
            self.send_header('Content-Length','0')
            self.end_headers()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    class Capture:
        endpoint=f'http://127.0.0.1:{server.server_port}'
        def wait(self,path,timeout=5):
            deadline=time.monotonic()+timeout
            with condition:
                while not any(r[0]==path for r in records):
                    remaining=deadline-time.monotonic()
                    if remaining <= 0: raise AssertionError(f'No OTLP {path} received')
                    condition.wait(remaining)
            return [r for r in records if r[0]==path]
        def snapshot(self): return list(records)
    try: yield Capture()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=1)
