# firebase-app-testing

Command-line tools for watching analytics events while testing a mobile app by hand on a Mac. Android and iOS print events in the same layout, so runs can be compared.

| Command | Target | What it does |
|---|---|---|
| `android-testing` | Android emulator | Boots the emulator, turns on Firebase DebugView for your app and prints events live. `--proxy` also catches events the app sends over HTTP |
| `ios-testing` | Physical iPhone | Runs a Wi-Fi proxy on the Mac that decrypts only analytics traffic and prints the events |

Both understand Firebase uploads, GA4 `/g/collect` requests and Measurement Protocol calls, so events sent to a server-side GTM endpoint are visible too. Firebase and Google Analytics hosts and hosts named `sgtm`, `sst`, `gtmss`, `data` or `track` are watched by default; add your own with `--domain example.com`. Everything else passes through untouched. Use `--save-events` to keep a plain-text log of a session.

## Install

```sh
git clone https://github.com/simonrmortensen/firebase-app-testing ~/dev/firebase-app-testing
~/dev/firebase-app-testing/install.sh
```

This links the commands into `~/.local/bin` and installs mitmproxy with `uv`. The Android prerequisites and details are in [AGENTS.md](AGENTS.md).

## Use

```sh
android-testing --save-events            # emulator + live events
ios-testing --save-events                # iPhone events; first set up the phone, see below
ios-testing --domain sgtm.example.com    # also watch your own endpoint
```

The iPhone needs a one-time setup (Wi-Fi proxy and a trusted certificate), and a clean-up afterwards: see [ios-testing/README.md](ios-testing/README.md), including how to turn it off and uninstall.

## Tests

```sh
python3 -I -m unittest discover -s tests
tests/integration.sh        # needs mitmdump on PATH
```

[AGENTS.md](AGENTS.md) has the design notes for people and agents changing the code.
