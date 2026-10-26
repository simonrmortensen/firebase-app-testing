# firebase-app-testing

Two command-line tools for watching analytics events while testing a mobile app by hand on macOS. Both print events in the same layout, so Android and iOS runs can be compared or diffed.

| Command | Target | How it sees events |
|---|---|---|
| `android-testing` | Android emulator (AVD) | Starts the emulator, enables Firebase DebugView for one app, reads `adb logcat` tags `FA` / `FA-SVC`. With `--proxy` it also routes the emulator through the shared proxy |
| `ios-testing` | Physical iPhone | Wi-Fi proxy on the Mac (mitmproxy) that decodes Firebase uploads and requests to server-side tagging endpoints |

## Layout

```
install.sh                       symlinks the commands into ~/.local/bin, installs mitmproxy
android-testing/
  android-testing                POSIX sh: emulator + DebugView + logcat events (+ optional proxy)
  android-testing-events         python3 (stdlib): logcat -> readable events; reads stdin
ios-testing/
  ios-testing                    POSIX sh: starts the relay and mitmdump with the shared addon
  README.md                      human instructions for the iPhone side (proxy + certificate)
event-proxy/                     shared by both commands
  addon.py                       mitmproxy addon: host filter, request routing, printing
  hosts.py                       which hosts are decrypted (defaults + custom domains)
  decoders.py                    Firebase protobuf, GA4 /g/collect, Measurement Protocol JSON
  relay.py                       stdlib TCP relay, run by /usr/bin/python3 (see below)
tests/
  test_hosts.py, test_decoders.py   unit tests, stdlib only
  integration.sh                 real mitmdump in reverse mode, spoofed Host headers
```

## Install on a Mac

```sh
git clone <this repo> ~/dev/firebase-app-testing
~/dev/firebase-app-testing/install.sh
```

`install.sh` symlinks the three commands into `~/.local/bin` (which must be on `PATH`, since `android-testing` calls `android-testing-events` by name) and, if `mitmdump` is missing, runs `uv tool install mitmproxy --exclude-newer "3 days"`. The commands find `event-proxy/` through their symlink, so keep the repo layout intact. Re-running is safe. `./install.sh --uninstall` removes the symlinks.

Prerequisites it does not install:
- **uv** (`brew install uv`) for mitmproxy.
- **Android only**: Homebrew `android-commandlinetools`, with `platform-tools`, `emulator` and a system image, and an AVD. The defaults are the AVD `Pixel_8_API_35` (Pixel 8, Android 35, `google_apis_playstore`, arm64-v8a) and `ANDROID_HOME=$(brew --prefix)/share/android-commandlinetools`. Create the AVD with `avdmanager create avd -n Pixel_8_API_35 -k "system-images;android-35;google_apis_playstore;arm64-v8a" -d pixel_8`.
- **iOS only**: nothing besides mitmproxy on the Mac, plus the phone setup in `ios-testing/README.md`.

Per-user defaults live outside the repo, in shell files: `~/.config/android-testing/config` (`DEFAULT_DEBUG_APP`, `DEFAULT_AVD`, `DEFAULT_DOMAINS`) and `~/.config/ios-testing/config` (`DEFAULT_DOMAINS`). Never commit app identifiers, domains of real projects, or captured event logs; `.gitignore` excludes `*-events-*.log`. Use `example.com` / `com.example.app` in docs and tests.

## Running the tests

```sh
python3 -I -m unittest discover -s tests          # unit tests, no dependencies
tests/integration.sh                              # needs mitmdump on PATH; nothing leaves the machine
```

Run both after changing anything in `event-proxy/`. `sh -n` every shell script you touch.

## Conventions and gotchas

- **Package age**: install dependencies only from releases at least 3 days old (`uv ... --exclude-newer "3 days"`, or the equivalent for npm/pip). Keep this when adding any dependency.
- **Shell**: scripts are POSIX `sh` and must stay `sh -n` clean. A `'` inside a `${VAR:-default}` word within double quotes breaks parsing in macOS `/bin/sh`.
- **Python**: `android-testing-events` and `relay.py` are stdlib-only. `relay.py` runs under the system `/usr/bin/python3` (3.9), so no newer syntax. `addon.py` runs inside mitmproxy's Python; `hosts.py` and `decoders.py` are pure stdlib so they can be unit-tested without mitmproxy.
- **Output format** is shared: `HH:MM:SS.mmm  name  [origin]` followed by aligned `key  value` lines; `--save-events [FILE]` writes the same text without colour, and every event title ends in `[origin]` (the launchers count saved events with that). Keep the iOS and Android formatters consistent, including the long parameter names (`ga_screen`, `ga_event_origin`, ...) that `decoders.py` maps from the SDK's short names.
- **Client neutrality**: nothing in this repo may name a client, app or real domain.

### Which traffic is decrypted

The proxy decides per connection, from the host name alone, so request features such as a path cannot be used for that. `hosts.py` holds the rules:
- Firebase upload hosts (`app-measurement.com`, `app-analytics-services.com`), `analytics.google.com`, and any host with a label starting with `googleanalytics`, `google-analytics`, `sgtm`, `sst`, `gtmss`, `data` or `track` (`sgtm.example.com`, `track1.example.com`, ...). Any port.
- Custom domains with `--domain` (repeatable; both commands) or `DEFAULT_DOMAINS` in the config file. `example.com` covers the domain and its subdomains; a `*` turns it into a glob over the whole host name.
- Everything else is tunnelled untouched, which keeps apps that pin certificates working. Every host the device connects to is logged to `~/Library/Logs/event-proxy/hosts.log`: read it before suspecting the decoders, and use it to find the right `--domain`.
- The addon sets mitmproxy's `allow_hosts` itself and skips that in reverse mode (the integration test), where the filter would apply to the upstream address.

Requests to decrypted hosts are then recognised **by content**, since server-side endpoints can use custom paths:
1. Not a Firebase host and JSON with an `events` array: Measurement Protocol (`[mp]`).
2. Not a Firebase host and the query has `tid` or `v=2` plus an `en` event (also one event per line in a POST body): GA4 collect (`[collect]`). Items (`pr1=...`) are expanded.
3. A POST whose body decodes as the Firebase protobuf: always tried on Firebase hosts; on other hosts only if every event has a plausible name and timestamp, so random bytes are not shown as events.
4. Otherwise ignored; `--debug` (ios) / `EVENT_PROXY_DEBUG=1` reports such requests so you can see what an endpoint really receives.

### android-testing

- Debug properties only take effect when the app starts, so the script force-stops the app after setting `debug.firebase.analytics.app`. Only one app can be flagged at a time.
- Events print only while `log.tag.FA` / `FA-SVC` are `VERBOSE`; the script sets them after boot. Logcat shows what the Firebase SDK logs, whatever the endpoint; it cannot see events an app sends over its own HTTP.
- `--proxy` (implied by `--domain`) starts mitmdump on `127.0.0.1:$PROXY_PORT` (default 8085) and starts the emulator with `-http-proxy`. Loopback needs no firewall relay. The emulator must trust the mitmproxy certificate: the script copies it to the emulator's Downloads folder, but the Google Play images cannot be rooted, so it can only be installed as a *user* CA, which apps trust only if they are debug builds with a network security config allowing it (or target SDK 23 or lower). Without that the app's HTTPS requests fail or the app ignores the proxy.
- Emulator output goes to `~/Library/Logs/android-testing-emulator.log` while events print.

### ios-testing

- **Why a relay**: the macOS application firewall blocks incoming connections to the Python that uv installs for mitmproxy, and changing that needs admin rights. `relay.py` runs under `/usr/bin/python3`, which is normally allowed, listens on `PORT` (default 8080) and forwards to mitmproxy on `127.0.0.1:PORT+1`. If you can allow the uv Python in the firewall instead, the relay becomes unnecessary.
- Current Firebase iOS SDKs upload to `region1.app-analytics-services.com`; older ones to `app-measurement.com`. Both are covered.
- **Decoder**: uploads are gzipped protobuf POSTs. `decoders.py` has the field numbers: batch > bundle(1) > event(2) / user property(3); event: param(1), name(2), timestamp ms(3); param: name(1), string(2), int(3), float(4), double(5), nested(6). They were reconstructed from memory, not taken from a published schema, and have been confirmed on real uploads from an iPhone: `POST /a` to `region1.app-analytics-services.com` with `Content-Type: application/x-www-form-urlencoded` (the body is protobuf regardless, so never detect it by content type), a few KB per batch and often many dozens of events, since the SDK sends everything since its last upload. Unusual fields may still be missed. If a capture decodes wrongly, the body is kept in `~/Library/Logs/event-proxy/raw/` (written when decoding fails or finds no events); fix the decoder from that.
- **Diagnosing silence** (learned on a real device): read `~/Library/Logs/event-proxy/requests.log` first. A line `TLS handshake with the device failed ... does not trust the proxy's certificate` means the certificate profile is installed but its trust switch (Settings > General > About > Certificate Trust Settings) is off. `TLS handshake ... client disconnected during the handshake` is the same problem as seen from a pinning or untrusting client. A host in `hosts.log` but no request line means the connection was not decrypted (not matched by `hosts.py`). If the Mac is on a VPN, the app's traffic leaves through it, so the backend may block the unfamiliar IP: disconnect the VPN during sessions.
- **Timing**: the SDK batches uploads, so events appear when the app uploads, not when they fire. Each batch is announced with an `-- host/path received HH:MM:SS --` line; GA4 collect and Measurement Protocol events carry no timestamp and are shown with the arrival time. User properties are printed only when their value changes, since every Firebase upload repeats them all.
- **No DebugView on a physical device** without Xcode: the `-FIRAnalyticsDebugEnabled` launch argument cannot be passed to an installed build.
- The phone side is documented for humans in `ios-testing/README.md`; keep it in sync when behaviour changes.
