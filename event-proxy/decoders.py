"""Decoders for the analytics request formats the proxy can see.

Every decoder returns (events, user_properties):
  events           [(name, timestamp_ms or None, params, origin)], params being an ordered dict
  user_properties  [(name, value)]

Formats:
  Firebase protobuf    POST of a (gzipped) protobuf batch from the Firebase SDK
  GA4 collect          /g/collect style requests (`v=2&tid=G-...&en=...&ep.x=...`), the protocol
                       web tags and server-side GTM endpoints use; extra events travel in the body
  Measurement Protocol POST of a JSON body with an `events` array
"""
import json
import re
import struct
import time
from urllib.parse import parse_qsl

# --- Firebase protobuf ------------------------------------------------------------------------

# Short names the SDK uses on the wire -> the names the Android tool shows.
EVENT_NAMES = {
    "_e": "user_engagement", "_vs": "screen_view", "_f": "first_open",
    "_s": "session_start", "_ab": "app_background", "_ou": "os_update",
}
PARAM_NAMES = {
    "_o": "ga_event_origin", "_sn": "ga_screen", "_sc": "ga_screen_class", "_si": "ga_screen_id",
    "_pc": "ga_previous_class", "_pi": "ga_previous_id", "_et": "engagement_time_msec",
}


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
    return EVENT_NAMES.get(name, name), ts, params, params.get("ga_event_origin", "")


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


def decode_firebase(buf):
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


def plausible_firebase(events):
    """Guard for hosts that are not Firebase's: random bytes must not pass as events."""
    now_ms = time.time() * 1000
    return bool(events) and all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", name)
        and 1.4e12 < ts < now_ms + 86400000
        for name, ts, _params, _origin in events
    )


# --- GA4 collect ------------------------------------------------------------------------------

# Item parameters (pr1=id123~nmShoe~pr99.9~qt2) -> item field names.
ITEM_FIELDS = {
    "id": "item_id", "nm": "item_name", "br": "item_brand", "ca": "item_category",
    "ca2": "item_category2", "ca3": "item_category3", "ca4": "item_category4",
    "ca5": "item_category5", "va": "item_variant", "pr": "price", "qt": "quantity",
    "cp": "coupon", "ds": "discount", "ln": "item_list_name", "li": "item_list_id",
    "lp": "index",
}
# Common request parameters worth showing as event parameters.
COMMON_FIELDS = {
    "cu": "currency", "_et": "engagement_time_msec", "dl": "page_location", "dt": "page_title",
    "dr": "page_referrer",
}


def number(text):
    try:
        return str(int(text))
    except ValueError:
        try:
            return str(float(text))
        except ValueError:
            return text


def parse_item(text):
    item, custom = {}, {}
    for token in text.split("~"):
        m = re.fullmatch(r"([kv])(\d+)(.*)", token)
        if m:
            custom.setdefault(m.group(2), {})[m.group(1)] = m.group(3)
            continue
        m = re.fullmatch(r"(ca[2-5]|[a-z]{2})(.*)", token)
        if m:
            item[ITEM_FIELDS.get(m.group(1), m.group(1))] = m.group(2)
    for pair in custom.values():
        if "k" in pair:
            item[pair["k"]] = pair.get("v", "")
    return item


def collect_event(pairs):
    """One event from a list of (key, value) request parameters."""
    name, params, items, props = "", {}, [], []
    for k, v in pairs:
        if k == "en":
            name = v
        elif k.startswith("ep."):
            params[k[3:]] = v
        elif k.startswith("epn."):
            params[k[4:]] = number(v)
        elif k.startswith("up."):
            props.append((k[3:], v))
        elif k.startswith("upn."):
            props.append((k[4:], number(v)))
        elif re.fullmatch(r"pr\d+", k):
            items.append(parse_item(v))
        elif k in COMMON_FIELDS:
            params[COMMON_FIELDS[k]] = v
    if items:
        params["items"] = items
    return name, params, props


def looks_like_collect(query, body):
    keys = {k for k, _ in query}
    lines = [ln for ln in (body or "").splitlines() if ln.startswith("en=") or "&en=" in ln]
    return ("tid" in keys or ("v", "2") in query) and ("en" in keys or bool(lines))


def decode_collect(query, body):
    """query: list of (key, value); body: text of a POST (one event per line) or ''."""
    common = [(k, v) for k, v in query if k != "en" and not k.startswith(("ep.", "epn.", "pr"))]
    event_pairs = []
    if any(k == "en" for k, _ in query):
        event_pairs.append(query)
    for line in (body or "").splitlines():
        if line.strip():
            event_pairs.append(parse_qsl(line, keep_blank_values=True))
    events, props = [], []
    for pairs in event_pairs:
        # Parameters in the query that belong to every event are folded in first.
        name, params, event_props = collect_event(common + pairs if pairs is not query else pairs)
        events.append((name, None, params, "collect"))
        props += event_props
    return events, props


# --- Measurement Protocol (JSON) --------------------------------------------------------------

def flatten(value):
    if isinstance(value, list):
        return [flatten(v) for v in value]
    if isinstance(value, dict):
        return {k: flatten(v) for k, v in value.items()}
    return "" if value is None else str(value)


def looks_like_mp(text):
    if not text or not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("events"), list) else None


def decode_mp(data):
    events, props = [], []
    top_ts = data.get("timestamp_micros")
    for ev in data["events"]:
        if not isinstance(ev, dict):
            continue
        micros = ev.get("timestamp_micros", top_ts)
        ts = int(micros) // 1000 if str(micros or "").isdigit() else None
        events.append((str(ev.get("name", "")), ts, flatten(ev.get("params") or {}), "mp"))
    for name, value in (data.get("user_properties") or {}).items():
        props.append((name, str(value.get("value")) if isinstance(value, dict) else str(value)))
    return events, props
