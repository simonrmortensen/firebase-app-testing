"""mitmproxy addon: print Firebase Analytics events that an iOS app uploads to Firebase.

Used by `ios-testing`. The SDK batches events and POSTs them to https://app-measurement.com/a (or the same path on
region1.app-analytics-services.com, which newer Firebase SDKs use) as
(optionally gzipped) protobuf; this decodes the batch and prints each event in the same layout as
`android-testing-events`.

Environment:
  IOS_TESTING_SAVE=FILE   also append a plain-text copy (no colour codes) to FILE
  IOS_TESTING_RAW_DIR=DIR where bodies that could not be decoded are kept (default
                          ~/Library/Logs/ios-testing/raw)
"""
import os
import re
import struct
import sys
from datetime import datetime

ANSI = re.compile(r"\033\[[0-9;]*m")
COLOR = sys.stdout.isatty()
LOG_DIR = os.path.expanduser("~/Library/Logs/ios-testing")
HOSTS_LOG = os.path.join(LOG_DIR, "hosts.log")  # every host the phone connects to, for debugging
RAW_DIR = os.environ.get("IOS_TESTING_RAW_DIR") or os.path.expanduser("~/Library/Logs/ios-testing/raw")

# Short names the SDK uses on the wire -> the names `android-testing` shows.
EVENT_NAMES = {
    "_e": "user_engagement", "_vs": "screen_view", "_f": "first_open",
    "_s": "session_start", "_ab": "app_background", "_ou": "os_update",
}
PARAM_NAMES = {
    "_o": "ga_event_origin", "_sn": "ga_screen", "_sc": "ga_screen_class", "_si": "ga_screen_id",
    "_pc": "ga_previous_class", "_pi": "ga_previous_id", "_et": "engagement_time_msec",
}


def c(code, text):
    return f"\033[{code}m{text}\033[0m"


# --- protobuf wire format ---------------------------------------------------------------------

def read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def fields(buf):
    """Yield (field_number, value) for every field; length-delimited values are bytes."""
    i = 0
    while i < len(buf):
        tag, i = read_varint(buf, i)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = read_varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            n, i = read_varint(buf, i)
            if i + n > len(buf):
                raise ValueError("truncated field")
            value, i = buf[i:i + n], i + n
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield number, value


def signed(n):
    return n - (1 << 64) if n >= 1 << 63 else n


# --- Firebase measurement messages ------------------------------------------------------------
# Batch.bundle(1) > Bundle.event(2) / Bundle.user_property(3)
# Event: param(1) name(2) timestamp_millis(3).  Param: name(1) string(2) int(3) float(4) double(5)
# nested params(6).  UserProperty: name(2) string(3) int(4) float(5) double(6).

def decode_value(f, v, string, integer, flt, dbl):
    """Map one of the value fields to a Python value; returns None if f is not a value field."""
    if f == string:
        return v.decode("utf-8", "replace")
    if f == integer:
        return signed(v)
    if f == flt:
        return f"{struct.unpack('<f', v)[0]:.7g}"
    if f == dbl:
        return repr(struct.unpack("<d", v)[0])
    return None


def decode_param(buf):
    name, value, children = None, None, []
    for f, v in fields(buf):
        if f == 1:
            name = v.decode("utf-8", "replace")
        elif f == 6:
            children.append(decode_param(v))
        else:
            got = decode_value(f, v, 2, 3, 4, 5)
            if got is not None:
                value = got
    if children:
        # Unnamed children are list entries (e.g. each item in `items`); named ones are fields.
        if all(not n for n, _ in children):
            value = [x for _, x in children]
        else:
            value = {n: x for n, x in children}
    return name, value


def decode_event(buf):
    name, ts, params = "", 0, {}
    for f, v in fields(buf):
        if f == 1:
            pname, pvalue = decode_param(v)
            params[PARAM_NAMES.get(pname, pname)] = pvalue
        elif f == 2:
            name = v.decode("utf-8", "replace")
        elif f == 3:
            ts = v
    return EVENT_NAMES.get(name, name), ts, params


def decode_user_property(buf):
    name, value = "", None
    for f, v in fields(buf):
        if f == 2:
            name = v.decode("utf-8", "replace")
        else:
            got = decode_value(f, v, 3, 4, 5, 6)
            if got is not None:
                value = got
    return name, value


def decode_batch(buf):
    """Return (events, user_properties) from an upload body; events are (name, ts_ms, params)."""
    events, props = [], []
    for f, bundle in fields(buf):
        if f != 1:
            continue
        for bf, v in fields(bundle):
            if bf == 2:
                events.append(decode_event(v))
            elif bf == 3:
                props.append(decode_user_property(v))
    return sorted(events, key=lambda e: e[1]), props


# --- output -----------------------------------------------------------------------------------

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


class FirebaseEvents:
    def __init__(self):
        self.save = None
        self.props = {}  # last printed value per user property; every upload repeats them all
        path = os.environ.get("IOS_TESTING_SAVE")
        if path:
            self.save = open(path, "a", encoding="utf-8")
            self.save.write(f"# Firebase Analytics events (iOS), started {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

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

    def request(self, flow):
        if not re.search(r"(^|\.)(app-measurement|app-analytics-services)\.com$", flow.request.pretty_host):
            return
        if flow.request.method != "POST":
            return
        body = flow.request.get_content() or b""  # mitmproxy undoes Content-Encoding: gzip
        try:
            events, props = decode_batch(body)
        except Exception as exc:
            self.out(c("31", f"ios-testing: could not decode upload ({exc}); raw body kept at {self.keep_raw(body)}"))
            return
        if not events and not props:
            self.out(c("31", f"ios-testing: upload had no events; raw body kept at {self.keep_raw(body)}"))
            return

        # The SDK uploads in batches, so say when this one arrived (event times are when they fired).
        self.out(c("2", f"-- upload received {datetime.now():%H:%M:%S}: {len(events)} events --"))
        for name, ts, params in events:
            when = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S.%f")[:-3] if ts else "--:--:--.---"
            origin = params.get("ga_event_origin", "")
            title = (c("2", f"{when}  {name}  [{origin}]") if origin == "auto"
                     else c("1;36", f"{when}  {name}") + c("2", f"  [{origin}]" if origin else ""))
            self.out("\n".join([title] + render_params(params)))
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        for name, value in props:
            if self.props.get(name) != value:
                self.props[name] = value
                self.out(c("33", f"{now}  user_property  {name} = {value}"))


addons = [FirebaseEvents()]
