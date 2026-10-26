"""Write a gzipped Firebase-style protobuf upload to stdout (for integration.sh)."""
import gzip
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from test_decoders import batch, event, i, ld, param, s

now = int(time.time() * 1000)
item = param("", children=[param("item_id", string="123"), param("quantity", integer=2)])
props = ld(3, i(1, now) + s(2, "non_personalized_ads") + i(4, 1))
body = batch([
    event("_e", now - 2000, param("_o", string="auto"), param("_et", integer=70543)),
    event("add_to_cart", now - 1000, param("_o", string="app"), param("currency", string="DKK"), param("items", children=[item])),
], props)
sys.stdout.buffer.write(gzip.compress(body))
