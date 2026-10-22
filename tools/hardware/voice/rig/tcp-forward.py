#!/usr/bin/env python3
"""tcp-forward: expose a loopback-only rig service on the Tailscale address (byte-for-byte TCP relay).

  tcp-forward.py LISTEN_HOST:PORT TARGET_HOST:PORT

Used for the owner-voice TTS (voxcpm-greek.service binds 127.0.0.1:8890 for OpenUnum; the phone reaches it at
100.76.5.104:8890 through this relay) without restarting the voice server. Streaming responses pass through
unbuffered (TCP_NODELAY both ways). Standard library only.
"""
import asyncio
import socket
import sys


def split(hp: str):
    host, port = hp.rsplit(':', 1)
    return host, int(port)


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


def nodelay(writer):
    sock = writer.get_extra_info('socket')
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


def handler(target):
    async def handle(cr, cw):
        try:
            tr, tw = await asyncio.wait_for(asyncio.open_connection(*target), 5)
        except (OSError, asyncio.TimeoutError):
            cw.close()
            return
        nodelay(cw)
        nodelay(tw)
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
    return handle


async def main(listen, target):
    server = await asyncio.start_server(handler(target), *listen, reuse_address=True)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(split(sys.argv[1]), split(sys.argv[2])))
