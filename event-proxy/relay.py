"""TCP relay: listen on 0.0.0.0:LISTEN and forward every connection to 127.0.0.1:TARGET.

Run by `ios-testing` with /usr/bin/python3 (Python 3.9, stdlib only). That binary is allowed through
the macOS firewall, while the Python that runs mitmproxy is blocked, so the phone connects here and
mitmproxy only listens on loopback.

    /usr/bin/python3 relay.py LISTEN TARGET
"""
import asyncio
import sys


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle(client_reader, client_writer, target):
    try:
        server_reader, server_writer = await asyncio.open_connection("127.0.0.1", target)
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, server_writer), pipe(server_reader, client_writer))


async def main(listen, target):
    server = await asyncio.start_server(lambda r, w: handle(r, w, target), "0.0.0.0", listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
    except KeyboardInterrupt:
        pass
