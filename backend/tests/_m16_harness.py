"""Synthetic local assertions only; never imported into application configuration."""
from collections import Counter
import asyncio
import json
import os
import statistics
import threading
import time

DOMAINS = ('provider', 'agent', 'tool', 'command', 'gateway', 'remediation', 'turn')

class Dispatches:
    def __init__(self):
        self.counts = Counter({key: 0 for key in DOMAINS})
    def hit(self, domain):
        assert domain in DOMAINS
        self.counts[domain] += 1
    def expect(self, **expected):
        assert dict(self.counts) == {key: expected.get(key, 0) for key in DOMAINS}


def resources(pid=None):
    import subprocess
    import shutil
    target = pid or os.getpid()
    rss = int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(target)], text=True).strip())*1024
    # Darwin has no /proc. Inspect only the explicitly owned PID and retain
    # counts, never socket addresses or file paths.
    threads = len(threading.enumerate()) if target == os.getpid() else None
    fds = len(os.listdir('/dev/fd')) if target == os.getpid() else None
    sockets = None
    if threads is None:
        rows = subprocess.check_output(['ps', '-M', '-p', str(target)], text=True).splitlines()
        threads = max(0, len(rows)-1)
    if shutil.which('lsof'):
        result = subprocess.run(['lsof', '-a', '-p', str(target), '-Fft'], capture_output=True, text=True)
        if result.returncode == 0:
            numeric = False; fds = 0; sockets = 0
            for row in result.stdout.splitlines():
                if row.startswith('f'):
                    numeric = row[1:].isdigit()
                    fds += int(numeric)
                elif numeric and row.startswith('t'):
                    sockets += int(row[1:] in {'IPv4', 'IPv6', 'unix'})
    return {'rss': rss, 'threads':threads, 'fds':fds, 'sockets':sockets,
            'tasks':len(asyncio.all_tasks()) if _loop() and target == os.getpid() else None}

def _loop():
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def envelope(name, timings, **values):
    ordered = sorted(timings)
    report = {'subsystem':name, 'samples':len(ordered), 'local_synthetic':True, **values}
    if ordered:
        report.update(p50_ms=statistics.median(ordered)*1000, max_ms=max(ordered)*1000,
                      p95_ms=ordered[int(.95*(len(ordered)-1))]*1000 if len(ordered)>=100 else None,
                      p99_ms=ordered[int(.99*(len(ordered)-1))]*1000 if len(ordered)>=1000 else None)
    print('M16_ENVELOPE='+json.dumps(report, sort_keys=True))
    return report
