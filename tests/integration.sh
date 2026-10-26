#!/bin/sh
# End-to-end check of the proxy addon in a real mitmdump. Nothing leaves the machine: mitmdump runs
# in reverse mode towards a closed port and the requests only carry a spoofed Host header.
#   tests/integration.sh
cd "$(dirname "$0")/.." || exit 1
TMP=$(mktemp -d)
PORT=18099
export EVENT_PROXY_DOMAINS="custom.test" EVENT_PROXY_DEBUG=1 EVENT_PROXY_SAVE="$TMP/saved.log" EVENT_PROXY_LOG_DIR="$TMP/logs"

python3 tests/make_upload.py >"$TMP/upload.gz"
mitmdump --mode reverse:http://127.0.0.1:9 --listen-port "$PORT" --set flow_detail=0 \
  --set connection_strategy=lazy --set keep_host_header=true --set termlog_verbosity=warn \
  -s event-proxy/addon.py >"$TMP/out.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$TMP"' EXIT
sleep 5

send() { # host, then curl arguments
  host=$1; shift
  curl -s -o /dev/null -m 5 -H "Host: $host" "$@"
}
URL="http://127.0.0.1:$PORT"
Q='v=2&tid=G-TEST1&en=purchase&cu=DKK&ep.transaction_id=T-1&epn.value=199.9&pr1=id123~nmShoe~pr99.9~qt2'

send region1.app-analytics-services.com -H 'Content-Encoding: gzip' --data-binary @"$TMP/upload.gz" "$URL/a"
send sgtm.example.com "$URL/g/collect?$Q"
send sst.example.com -H 'Content-Type: application/json' -d '{"client_id":"x","events":[{"name":"refund","params":{"value":12.5}}]}' "$URL/mp/collect"
send data.example.com -H 'Content-Encoding: gzip' --data-binary @"$TMP/upload.gz" "$URL/ingest"
send custom.test -d '{"events":[{"name":"custom_domain_event"}]}' "$URL/anything"
send www.example.com "$URL/g/collect?$Q"
send sgtm.example.com "$URL/healthz"
send app-measurement.com --data-binary 'garbage-not-protobuf' "$URL/a"
sleep 1
kill $PID; wait $PID 2>/dev/null
OUT=$(cat "$TMP/out.log")

fail=0
check() { # description, pattern, expected count of matching lines
  got=$(printf '%s\n' "$OUT" | grep -cE -- "$2")
  if [ "$got" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1 (wanted $3, got $got)"; fail=1; fi
}
check "startup names the custom domain"       'decrypting .* plus custom.test' 1
check "firebase protobuf, both events"        'region1.app-analytics-services.com/a received .*: 2 events' 1
check "firebase protobuf names and items"     'user_engagement  \[auto\]|add_to_cart  \[app\]|item_id=123  quantity=2' 6
check "ga4 collect via sgtm host"             '^[0-9:.]+  purchase  \[collect\]' 1
check "ga4 collect item"                      'item_id=123  item_name=Shoe  price=99.9  quantity=2' 1
check "measurement protocol via sst host"     '^[0-9:.]+  refund  \[mp\]' 1
check "protobuf on a non-firebase host"       'data.example.com/ingest received .*: 2 events' 1
check "custom domain event"                   'custom_domain_event' 1
check "unmatched host is ignored"             'www.example.com' 0
check "unrecognised request is reported"      'not an event request: GET sgtm.example.com/healthz' 1
check "undecodable firebase body is kept"     'could not decode upload' 1
check "user property printed once"            'user_property  non_personalized_ads = 1' 1
check "no redundant note after a decode error" 'not an event request: POST app-measurement.com' 0
reqs=$(grep -c ': ' "$TMP/logs/requests.log")
[ "$reqs" -eq 7 ] && echo "ok    requests.log has $reqs lines" || { echo "FAIL  requests.log has $reqs lines"; fail=1; }
saved=$(grep -cE '\[[a-z]+\]$' "$TMP/saved.log")
[ "$saved" -ge 6 ] && echo "ok    saved file has $saved events" || { echo "FAIL  saved file has $saved events"; fail=1; }
[ "$fail" = 0 ] && echo "all integration checks passed" || { echo "--- output"; echo "$OUT"; }
exit $fail
