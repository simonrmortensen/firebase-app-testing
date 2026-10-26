import gzip
import json
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "event-proxy"))
import decoders


def varint(n):
    n &= (1 << 64) - 1
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def ld(f, data):
    return varint(f << 3 | 2) + varint(len(data)) + data


def s(f, text):
    return ld(f, text.encode())


def i(f, n):
    return varint(f << 3) + varint(n)


def param(name, string=None, integer=None, dbl=None, children=()):
    b = s(1, name)
    if string is not None:
        b += s(2, string)
    if integer is not None:
        b += i(3, integer)
    if dbl is not None:
        b += varint(5 << 3 | 1) + struct.pack("<d", dbl)
    for ch in children:
        b += ld(6, ch)
    return b


def event(name, ts, *params):
    return b"".join(ld(1, p) for p in params) + s(2, name) + i(3, ts)


def batch(events, props=b""):
    return ld(1, i(1, 1) + b"".join(ld(2, e) for e in events) + props)


class Firebase(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time() * 1000)

    def test_events_params_items_and_user_properties(self):
        item = param("", children=[param("item_id", string="123"), param("price", dbl=99.95), param("quantity", integer=2)])
        props = ld(3, i(1, self.now) + s(2, "non_personalized_ads") + i(4, 1))
        body = batch([
            event("add_to_cart", self.now - 1000, param("_o", string="app"), param("currency", string="DKK"), param("items", children=[item])),
            event("_e", self.now - 2000, param("_o", string="auto"), param("_et", integer=70543), param("_sn", string="product_detail")),
        ], props)
        events, user_props = decoders.decode_firebase(body)
        self.assertEqual([e[0] for e in events], ["user_engagement", "add_to_cart"])  # sorted by time
        engagement, cart = events
        self.assertEqual(engagement[2]["engagement_time_msec"], 70543)
        self.assertEqual(engagement[2]["ga_screen"], "product_detail")
        self.assertEqual(engagement[3], "auto")
        self.assertEqual(cart[2]["items"], [{"item_id": "123", "price": "99.95", "quantity": 2}])
        self.assertEqual(user_props, [("non_personalized_ads", 1)])

    def test_garbage_raises(self):
        with self.assertRaises(Exception):
            decoders.decode_firebase(b"garbage-not-protobuf")

    def test_plausibility_guard_for_foreign_hosts(self):
        good, _ = decoders.decode_firebase(batch([event("purchase", self.now, param("_o", string="app"))]))
        self.assertTrue(decoders.plausible_firebase(good))
        self.assertFalse(decoders.plausible_firebase([]))
        self.assertFalse(decoders.plausible_firebase([("not a name!", self.now, {}, "")]))
        self.assertFalse(decoders.plausible_firebase([("purchase", 12345, {}, "")]))


class Collect(unittest.TestCase):
    QUERY = [("v", "2"), ("tid", "G-ABC123"), ("cid", "1.2"), ("en", "purchase"), ("cu", "DKK"),
             ("ep.transaction_id", "T-1"), ("epn.value", "199.9"), ("epn.shipping", "5"),
             ("pr1", "id123~nmShoe~br~caShoes~pr99.9~qt2~k0color~v0red"), ("up.plan", "gold")]

    def test_single_event_in_query(self):
        self.assertTrue(decoders.looks_like_collect(self.QUERY, ""))
        events, props = decoders.decode_collect(self.QUERY, "")
        (name, ts, params, origin), = events
        self.assertEqual((name, ts, origin), ("purchase", None, "collect"))
        self.assertEqual(params["transaction_id"], "T-1")
        self.assertEqual(params["value"], "199.9")
        self.assertEqual(params["shipping"], "5")
        self.assertEqual(params["currency"], "DKK")
        self.assertEqual(params["items"], [{"item_id": "123", "item_name": "Shoe", "item_brand": "", "item_category": "Shoes",
                                            "price": "99.9", "quantity": "2", "color": "red"}])
        self.assertEqual(props, [("plan", "gold")])

    def test_batched_events_in_body_share_the_query_parameters(self):
        query = [("v", "2"), ("tid", "G-ABC123"), ("cu", "EUR")]
        body = "en=page_view&ep.page=home\nen=scroll&epn.percent_scrolled=90\n"
        self.assertTrue(decoders.looks_like_collect(query, body))
        events, _ = decoders.decode_collect(query, body)
        self.assertEqual([e[0] for e in events], ["page_view", "scroll"])
        self.assertEqual(events[0][2], {"currency": "EUR", "page": "home"})
        self.assertEqual(events[1][2]["percent_scrolled"], "90")

    def test_other_requests_are_not_collect(self):
        self.assertFalse(decoders.looks_like_collect([("page", "2")], ""))
        self.assertFalse(decoders.looks_like_collect([("tid", "G-1")], ""))  # no event
        self.assertFalse(decoders.looks_like_collect([], "hello=world"))


class MeasurementProtocol(unittest.TestCase):
    def test_events_params_and_user_properties(self):
        text = json.dumps({
            "client_id": "x", "timestamp_micros": 1700000000000000,
            "user_properties": {"plan": {"value": "gold"}},
            "events": [{"name": "refund", "params": {"value": 12.5, "items": [{"item_id": "a", "quantity": 1}]}}],
        })
        data = decoders.looks_like_mp(text)
        events, props = decoders.decode_mp(data)
        name, ts, params, origin = events[0]
        self.assertEqual((name, ts, origin), ("refund", 1700000000000, "mp"))
        self.assertEqual(params["value"], "12.5")
        self.assertEqual(params["items"], [{"item_id": "a", "quantity": "1"}])
        self.assertEqual(props, [("plan", "gold")])

    def test_other_json_is_ignored(self):
        self.assertIsNone(decoders.looks_like_mp('{"hello": "world"}'))
        self.assertIsNone(decoders.looks_like_mp("not json"))
        self.assertIsNone(decoders.looks_like_mp(None))


if __name__ == "__main__":
    unittest.main()
