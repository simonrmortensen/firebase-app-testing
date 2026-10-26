# ios-testing

Prints the analytics events an app on a **physical iPhone** sends, by routing the phone's Wi-Fi traffic through a proxy on your Mac. Only analytics hosts are decrypted; everything else passes through untouched.

```
ios-testing                                # print events as the app uploads them
ios-testing --save-events                  # also save them to ./ios-events-<timestamp>.log
ios-testing --domain sgtm.example.com      # also decrypt this domain (repeatable)
ios-testing --debug                        # also report requests to decrypted hosts that are not events
```

**What is monitored.** Firebase uploads (protobuf), GA4 `/g/collect` requests and Measurement Protocol JSON, on these hosts:
- Firebase and Google Analytics hosts (`app-measurement.com`, `app-analytics-services.com`, `google-analytics.com`, `analytics.google.com`).
- Any host with a label starting with `googleanalytics`, `sgtm`, `sst`, `gtmss`, `data` or `track`, for example `sgtm.example.com` or `track1.example.com`. These are the usual names of server-side tagging endpoints.
- Your own domains with `--domain`. `example.com` covers the domain and all its subdomains; a `*` makes it a pattern over the whole host name, e.g. `--domain 'collect*.example.*'`. Put permanent ones in `~/.config/ios-testing/config`: `DEFAULT_DOMAINS="sgtm.example.com other.example.org"`.

Not sure which host your app sends events to? Use the app for a minute and read `~/Library/Logs/event-proxy/hosts.log`, which lists every host the phone connected to, then pass the right one with `--domain`.

Requests are recognised by what they contain, not by their path, so a renamed endpoint still works as long as it is on a decrypted host.

The Mac and the iPhone must be on the same Wi-Fi network, and the network must allow devices to talk to each other (guest networks often don't).

Events appear when the app **uploads a batch**, not the moment they fire. Sending the app to the background usually triggers an upload; if nothing shows, keep it in the background for a while.

## Set up the iPhone (once)

1. **Start the proxy** on the Mac: run `ios-testing`. It prints the Mac's IP address and the port (default 8080).
2. **Point the phone at it**: Settings > Wi-Fi > tap the (i) next to your network > Configure Proxy > **Manual**. Enter the Mac's IP as Server, the port, and leave Authentication off. Tap Save.
3. **Download the certificate**: in Safari on the iPhone open **`http://mitm.it`**. Type the full address including `http://`. Without it Safari upgrades to HTTPS, the request bypasses the proxy and you get "If you can see this, traffic is not going through mitmproxy". Tap the Apple icon, then Allow to download the profile.
   - If the download link gives a 404, type `http://mitm.it/cert/pem` yourself, or AirDrop `~/.mitmproxy/mitmproxy-ca-cert.cer` from the Mac to the phone instead (`open -R ~/.mitmproxy/mitmproxy-ca-cert.cer` reveals it in Finder).
4. **Install the profile**: Settings > General > **VPN & Device Management** > the downloaded mitmproxy profile > Install.
5. **Trust it**: Settings > General > About > **Certificate Trust Settings** > switch on the **mitmproxy** certificate. Installing alone is not enough; until this switch is on, HTTPS through the proxy fails.

The certificate is generated on the Mac the first time `ios-testing` runs, in `~/.mitmproxy/`. It is unique to that Mac; keep the folder private.

## Each test session

1. Run `ios-testing` on the Mac (add `--save-events` to keep a log).
2. Make sure the phone's Wi-Fi proxy is still set to Manual.
3. Open the app, use it, then send it to the background.
4. Press Ctrl-C on the Mac when done.

## Turn it off

Do this after every session, or the phone has no internet whenever `ios-testing` is not running.

- **Disable the proxy**: Settings > Wi-Fi > (i) next to your network > Configure Proxy > **Off**.
- The certificate can stay installed between sessions; it does nothing while the proxy is off. If you do leave it, remember it lets anyone holding the Mac's `~/.mitmproxy/` key read your HTTPS traffic through a proxy, so prefer to remove it when you are done for a while.

## Uninstall

On the iPhone:
1. Turn the proxy off as above.
2. Settings > General > **VPN & Device Management** > the mitmproxy profile > **Remove Profile**. Removing the profile also removes the trust setting.

On the Mac:
- Remove the commands: `./install.sh --uninstall` in this repo.
- Remove mitmproxy: `uv tool uninstall mitmproxy`.
- Delete the certificate and key: `rm -r ~/.mitmproxy`. A new certificate is generated if you ever set it up again, and the phone must then install it again.
- Optional: delete the logs in `~/Library/Logs/event-proxy/`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `mitm.it` says traffic is not going through mitmproxy | Proxy not set on the phone, `ios-testing` not running, different network, or you typed `mitm.it` without `http://` |
| Phone has no internet | The Wi-Fi proxy is still on but `ios-testing` is not running. Turn it off or start it |
| No events, but the app works | Not uploaded yet (background the app and wait), or the certificate is installed but not trusted (step 5), or the app sends events to a host that is not decrypted. Check `~/Library/Logs/event-proxy/hosts.log` for the host and add it with `--domain` |
| `not an event request: ...` (with `--debug`) | A decrypted host received a request that does not look like an event, e.g. a health check. If it should be an event, the format is not supported yet; see `AGENTS.md` |
| `could not decode upload` | The Firebase decoder does not match the upload format. The raw body is kept in `~/Library/Logs/event-proxy/raw/`; open an issue with a sample or see `AGENTS.md` |
| The app's backend blocks or rejects requests while the proxy runs | The phone's traffic now leaves the network from the Mac. If the Mac is on a VPN, the backend sees the VPN's IP address instead of your usual one (check `netstat -rn -f inet \| grep default` for a `utun` route). Disconnect the VPN on the Mac during the session; the phone reaches the Mac over local Wi-Fi, so it keeps working |
| One feature of the app fails while the proxy runs | The app pins certificates for a host that is decrypted, for example its own `data.` or `track.` backend. Firebase uploads are not usually pinned. Everything not matching the lists above is not decrypted, so the pinned host is the one to look for in `~/Library/Logs/event-proxy/hosts.log` |

## Why there is a relay

The macOS firewall blocks incoming connections to the Python that runs mitmproxy, and allowing it needs admin rights. `ios-testing` therefore starts a tiny relay under the system Python, which the firewall normally allows, and mitmproxy listens only on the Mac's loopback address. Nothing to configure; see `AGENTS.md` for details.
