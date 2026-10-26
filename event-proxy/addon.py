"""mitmproxy addon: print analytics events from proxied traffic, in the same layout as
`android-testing-events`.

Used by `ios-testing` and `android-testing --proxy`. Hosts matching hosts.py (Firebase, Google
Analytics, common server-side tagging names, plus your own domains) are decrypted; the requests to
them are recognised by content and decoded by decoders.py: Firebase protobuf uploads, GA4
`/g/collect` requests and Measurement Protocol JSON. Everything else is ignored.

Environment:
  EVENT_PROXY_DOMAINS   extra domains to decrypt, space or comma separated (`example.com` covers
                        its subdomains; a `*` makes it a glob over the whole host name)
  EVENT_PROXY_SAVE      also append a plain-text copy (no colour codes) to this file
  EVENT_PROXY_DEBUG=1   also say when a request to a decrypted host was not recognised
  EVENT_PROXY_RAW_DIR   where undecodable Firebase uploads are kept
                        (default ~/Library/Logs/event-proxy/raw)
"""
import os
import re
import sys
from datetime import datetime

from mitmproxy import ctx

import decoders
import hosts

ANSI = re.compile(r"\033\[[0-9;]*m")
COLOR = sys.stdout.isatty()
LOG_DIR = os.path.expanduser("~/Library/Logs/event-proxy")
HOSTS_LOG = os.path.join(LOG_DIR, "hosts.log")  # every host connected to, for debugging
RAW_DIR = os.environ.get("EVENT_PROXY_RAW_DIR") or os.path.join(LOG_DIR, "raw")


def c(code, text):
    return f"\033[{code}m{text}\033[0m"


def fmt_item(item):
    if isinstance(item, dict):
        return "  ".join(f"{k}={v}" for k, v in item.items())
    return str(item)


def render_params(params):
    width = max((len(k) for k in params), default=0)
    lines = []
    for k, v in params.items():
        if isinstance(v, list):
            lines.append(f"    {c('1', k.ljust(width))}  [{len(v)}]")
            lines += [f"    {' ' * width}    - {fmt_item(x)}" for x in v]
        elif isinstance(v, dict):
            lines.append(f"    {c('1', k.ljust(width))}  {fmt_item(v)}")
        else:
            lines.append(f"    {c('1', k.ljust(width))}  {v}")
    return lines


class EventProxy:
    def __init__(self):
        self.domains = hosts.split_domains(os.environ.get("EVENT_PROXY_DOMAINS"))
        self.debug = os.environ.get("EVENT_PROXY_DEBUG") == "1"
        self.props = {}  # last printed value per user property; Firebase repeats them every upload
        path = os.environ.get("EVENT_PROXY_SAVE")
        self.save = open(path, "a", encoding="utf-8") if path else None

    def load(self, loader):
        # Decrypt only matching hosts; everything else is tunnelled without touching it. (In reverse
        # mode, which the tests use, the filter would apply to the upstream address, so skip it.)
        if not any(str(mode).startswith("reverse") for mode in ctx.options.mode):
            ctx.options.allow_hosts = [hosts.allow_hosts_regex(self.domains)]

    def running(self):
        custom = f", plus {' '.join(self.domains)}" if self.domains else ""
        line = c("2", f"event-proxy: decrypting Firebase, Google Analytics and sgtm/sst/gtmss/data/track hosts{custom}")
        print(line if COLOR else ANSI.sub("", line), flush=True)

    def out(self, text):
        print(text if COLOR else ANSI.sub("", text), flush=True)
        if self.save:
            self.save.write(ANSI.sub("", text) + "\n")
            self.save.flush()

    def server_connect(self, data):
        os.makedirs(LOG_DIR, exist_ok=True)
        host, port = data.server.address
        with open(HOSTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S}  {host}:{port}\n")

    def keep_raw(self, body):
        os.makedirs(RAW_DIR, exist_ok=True)
        path = os.path.join(RAW_DIR, f"{datetime.now():%Y%m%d-%H%M%S-%f}.bin")
        with open(path, "wb") as fh:
            fh.write(body)
        return path

    def decode(self, flow):
        """Return (events, props) for a request; None when it is not an analytics request, False
        when it is one that could not be decoded (already reported)."""
        host = flow.request.pretty_host
        firebase = hosts.is_firebase(host)
        try:
            body = flow.request.get_content() or b""  # mitmproxy undoes Content-Encoding: gzip
        except ValueError:
            body = flow.request.raw_content or b""
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        query = list(flow.request.query.fields)

        if not firebase:
            data = decoders.looks_like_mp(text)
            if data is not None:
                return decoders.decode_mp(data)
            if decoders.looks_like_collect(query, text):
                return decoders.decode_collect(query, text)
        if flow.request.method != "POST" or not body or (text is not None and not firebase):
            return None
        try:
            events, props = decoders.decode_firebase(body)
        except Exception as exc:
            if firebase:
                self.out(c("31", f"event-proxy: could not decode upload ({exc}); raw body kept at {self.keep_raw(body)}"))
                return False
            return None
        if firebase:
            if not events and not props:
                self.out(c("31", f"event-proxy: upload had no events; raw body kept at {self.keep_raw(body)}"))
                return False
            return events, props
        return (events, props) if decoders.plausible_firebase(events) else None

    def request(self, flow):
        host = flow.request.pretty_host
        if not hosts.matches(host, self.domains):
            return
        result = self.decode(flow)
        if result is False:
            return
        if result is None:
            if self.debug:
                self.out(c("2", f"event-proxy: not an event request: {flow.request.method} {host}{flow.request.path.split('?')[0]}"))
            return
        events, props = result

        # Batches are uploaded after the fact, so say when this one arrived (event times are when
        # they fired; formats without a timestamp are shown with the arrival time).
        arrived = datetime.now()
        self.out(c("2", f"-- {host}{flow.request.path.split('?')[0]} received {arrived:%H:%M:%S}: {len(events)} events --"))
        for name, ts, params, origin in events:
            when = datetime.fromtimestamp(ts / 1000) if ts else arrived
            stamp = when.strftime("%H:%M:%S.%f")[:-3]
            title = (c("2", f"{stamp}  {name}  [{origin}]") if origin == "auto"
                     else c("1;36", f"{stamp}  {name}") + c("2", f"  [{origin}]" if origin else ""))
            self.out("\n".join([title] + render_params(params)))
        stamp = arrived.strftime("%H:%M:%S.%f")[:-3]
        for name, value in props:
            if self.props.get(name) != value:
                self.props[name] = value
                self.out(c("33", f"{stamp}  user_property  {name} = {value}"))


addons = [EventProxy()]
