"""Test-child egress guard. Never imported by production runtime."""
import ipaddress
import os
import socket

if os.environ.get('SLOPANOC_M16_LOCAL_TEST') != '1':
    os._exit(78) # sitecustomize exceptions are otherwise ignored by Python

def allowed(address):
    host = address[0]
    if host == 'localhost':
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise AssertionError('M16 forbids non-loopback network')

def wrap(name):
    original = getattr(socket.socket, name)
    def guarded(self, *args, **kwargs):
        address = args[-1] if name == 'sendto' else args[0]
        if self.family in (socket.AF_INET, socket.AF_INET6):
            allowed(address)
        return original(self, *args, **kwargs)
    setattr(socket.socket, name, guarded)
for name in ('connect', 'connect_ex', 'sendto'):
    wrap(name)
_original_resolve = socket.getaddrinfo
def resolve(host, *args, **kwargs):
    if host is not None:
        allowed((host, 0))
    return _original_resolve(host, *args, **kwargs)
socket.getaddrinfo = resolve
