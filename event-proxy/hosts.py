"""Which hosts the proxy decrypts. Everything else is tunnelled untouched.

The proxy has to decide this from the host name alone, before any request is visible, so request
features such as the path cannot be used here; they are used afterwards, to tell events from other
traffic on the hosts that were decrypted (see decoders.py).
"""
import re

# Firebase's own upload endpoints. Only these get the (looser) protobuf decoding.
FIREBASE = r"(.+\.)?(app-measurement|app-analytics-services)\.com"

# Defaults: a label that starts with one of the words (sgtm.example.com, track1.example.com,
# data.example.com, ...) covers server-side tagging endpoints; Google Analytics is matched anywhere.
DEFAULTS = [
    FIREBASE,
    r"(.+\.)?analytics\.google\.com",
    r"(.+\.)?(googleanalytics|google-analytics|sgtm|sst|gtmss|data|track)[^.]*\..+",
]


def pattern_regex(pattern):
    """Turn a user supplied domain into a regex for the whole host name.

    `example.com`   -> example.com and every subdomain
    `sgtm*.foo.*`   -> a glob; `*` matches anything, including dots
    """
    pattern = pattern.strip().lower()
    if "*" in pattern:
        return ".*".join(re.escape(part) for part in pattern.split("*"))
    return r"(.+\.)?" + re.escape(pattern)


def split_domains(text):
    return [d for d in re.split(r"[\s,]+", text or "") if d]


def host_regex(domains=()):
    """Regex (case-insensitive, whole host) for the defaults plus the given custom domains."""
    parts = DEFAULTS + [pattern_regex(d) for d in domains]
    return "(?i)^(?:" + "|".join(parts) + ")$"


def allow_hosts_regex(domains=()):
    """The same, in the form mitmproxy's allow_hosts expects (matched against host:port)."""
    return host_regex(domains)[:-1] + r":\d+$"


def matches(host, domains=()):
    return re.match(host_regex(domains), host) is not None


def is_firebase(host):
    return re.match("(?i)^" + FIREBASE + "$", host) is not None
