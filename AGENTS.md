# firebase-app-testing

Two command-line tools for watching Firebase Analytics events while testing a mobile app by hand on macOS. Both print events in the same layout, so Android and iOS runs can be compared or diffed.

| Command | Target | How it sees events |
|---|---|---|
| `android-testing` | Android emulator (AVD) | Starts the emulator, enables Firebase DebugView for one app, reads `adb logcat` tags `FA` / `FA-SVC` |
| `ios-testing` | Physical iPhone | Wi-Fi proxy on the Mac (mitmproxy) that decodes Firebase's protobuf uploads |

## Layout

```
install.sh                       symlinks the commands into ~/.local/bin, installs mitmproxy
android-testing/
  android-testing                POSIX sh: emulator + DebugView + event printing
  android-testing-events         python3 (stdlib): logcat -> readable events; reads stdin
ios-testing/
  ios-testing                    POSIX sh: starts the relay and mitmdump
  firebase_events.py             mitmproxy addon: decodes uploads, prints events, logs hosts
  relay.py                       stdlib TCP relay, run by /usr/bin/python3 (see below)
  README.md                      human instructions for the iPhone side (proxy + certificate)
```

## Install on a Mac

```sh
git clone <this repo> ~/dev/firebase-app-testing
~/dev/firebase-app-testing/install.sh
```

`install.sh` symlinks the three commands into `~/.local/bin` (which must be on `PATH`, since `android-testing` calls `android-testing-events` by name) and, if `mitmdump` is missing, runs `uv tool install mitmproxy --exclude-newer "3 days"`. Re-running is safe. `./install.sh --uninstall` removes the symlinks.

Prerequisites it does not install:
- **uv** (`brew install uv`) for mitmproxy.
- **Android only**: Homebrew `android-commandlinetools`, with `platform-tools`, `emulator` and a system image, and an AVD. The defaults are the AVD `Pixel_8_API_35` (Pixel 8, Android 35, `google_apis_playstore`, arm64-v8a) and `ANDROID_HOME=$(brew --prefix)/share/android-commandlinetools`. Create the AVD with `avdmanager create avd -n Pixel_8_API_35 -k "system-images;android-35;google_apis_playstore;arm64-v8a" -d pixel_8`.
- **iOS only**: nothing besides mitmproxy on the Mac, plus the phone setup in `ios-testing/README.md`.

Per-user defaults live outside the repo in `~/.config/android-testing/config` (a shell file): `DEFAULT_DEBUG_APP=<package id>` and optionally `DEFAULT_AVD`. Never commit app identifiers or captured event logs; `.gitignore` already excludes `*-events-*.log`.

## Conventions and gotchas

- **Package age**: install dependencies only from releases at least 3 days old (`uv ... --exclude-newer "3 days"`, or the equivalent for npm/pip). Keep this when adding any dependency.
- **Shell**: scripts are POSIX `sh` and must stay `sh -n` clean. A `'` inside a `${VAR:-default}` word within double quotes breaks parsing in macOS `/bin/sh`.
- **Python**: `android-testing-events` and `relay.py` are stdlib-only. `relay.py` runs under the system `/usr/bin/python3` (3.9), so no newer syntax. `firebase_events.py` runs inside mitmproxy's Python.
- **Output format** is shared: `HH:MM:SS.mmm  name  [origin]` followed by aligned `key  value` lines; `--save-events [FILE]` writes the same text without colour. Keep the iOS and Android formatters consistent, including the long parameter names (`ga_screen`, `ga_event_origin`, ...) that `firebase_events.py` maps from the SDK's short names.

### android-testing

- Debug properties only take effect when the app starts, so the script force-stops the app after setting `debug.firebase.analytics.app`. Only one app can be flagged at a time.
- Events print only while `log.tag.FA` / `FA-SVC` are `VERBOSE`; the script sets them after boot.
- Emulator output goes to `~/Library/Logs/android-testing-emulator.log` while events print.

### ios-testing

- **Why a relay**: the macOS application firewall blocks incoming connections to the Python that uv installs for mitmproxy, and changing that needs admin rights. `relay.py` runs under `/usr/bin/python3`, which is normally allowed, listens on `PORT` (default 8080) and forwards to mitmproxy on `127.0.0.1:PORT+1`. If you can allow the uv Python in the firewall instead, the relay becomes unnecessary.
- **Only Firebase hosts are decrypted** (`--allow-hosts`): `app-measurement.com` and `app-analytics-services.com`. Everything else is tunnelled untouched, so apps that pin certificates keep working. Current Firebase iOS SDKs upload to `region1.app-analytics-services.com`; older ones to `app-measurement.com`. If events are missing, read `~/Library/Logs/ios-testing/hosts.log` (every host the phone connected to) before suspecting the decoder.
- **Decoder**: uploads are gzipped protobuf POSTs. `firebase_events.py` has the field numbers: batch > bundle(1) > event(2) / user property(3); event: param(1), name(2), timestamp ms(3); param: name(1), string(2), int(3), float(4), double(5), nested(6). They were reconstructed from memory and checked against synthetic payloads, not against a published schema. If a real capture decodes wrongly, the body is kept in `~/Library/Logs/ios-testing/raw/` (written when decoding fails or finds no events); fix the decoder from that.
- **Timing**: the SDK batches uploads, so events appear when the app uploads, not when they fire. Each batch is announced with an `-- upload received HH:MM:SS --` line. User properties are printed only when their value changes, since every upload repeats them all.
- **No DebugView on a physical device** without Xcode: the `-FIRAnalyticsDebugEnabled` launch argument cannot be passed to an installed build.
- To test the addon without a phone, run mitmdump in reverse mode with `--set keep_host_header=true --set connection_strategy=lazy` and POST a payload with a spoofed `Host: app-measurement.com` header. Nothing then leaves the machine.
- The phone side is documented for humans in `ios-testing/README.md`; keep it in sync when behaviour changes.
