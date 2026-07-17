# XauOrderPad — iOS client

A native **Swift / SwiftUI** port of the Android XauOrderPad phone client: a thin remote control for
the MT5 order-pad server (live `/ws` feed + REST commands, mutual-TLS + token). Built on Windows via a
**local Docker macOS VM** (see [build-vm/README.md](build-vm/README.md)) and sideloaded with Sideloadly.

> This is not in git upstream (like `android/`). It exists on this machine only.

## Source layout

```
ios/
├── build-vm/                 # the macOS-in-Docker build environment (docker-compose + runbook)
├── project.yml               # XcodeGen spec → XauOrderPad.xcodeproj (scheme "XauOrderPad")
└── XauOrderPad/
    ├── App/    XauOrderPadApp.swift (entry + RootView router), Info.plist (ATS + local-network)
    ├── Net/    Frames.swift, Api.swift, TradingClient.swift, TradingURLSessionDelegate.swift, SessionProvider.swift
    ├── Data/   AppSettings.swift, CertStore.swift, Keychain.swift, Feed.swift
    └── UI/     TradeViewModel.swift, ConnectView.swift, TradeView.swift, CertsView.swift
```

## Layer map (Android → iOS)

| Android (source of truth) | iOS |
|---|---|
| `net/Frames.kt` (Serializable models) | `Net/Frames.swift` (Codable; all-optional/tolerant decoders) |
| `net/Api.kt` (OkHttp REST, `ApiResult`) | `Net/Api.swift` (`URLSession` async, `ApiResult` enum) |
| `net/TradingClient.kt` (OkHttp WebSocket) | `Net/TradingClient.swift` (`URLSessionWebSocketTask`) |
| `net/Tls.kt` (KeyManager + X509TrustManager) | `Net/TradingURLSessionDelegate.swift` (challenge handler) |
| `data/Feed.kt` (shared client, app-scoped) | `Data/Feed.swift` + `Net/SessionProvider.swift` |
| `data/CertStore.kt` / `data/Secrets.kt` | `Data/CertStore.swift` / `Data/AppSettings.swift` + `Keychain.swift` |
| `ui/UiState.kt` + `ui/TradingViewModel.kt` | `UI/TradeViewModel.swift` |
| Compose screens | `UI/ConnectView.swift`, `TradeView.swift`, `CertsView.swift` |

**Ported gotchas (do not "simplify" away):** every wire field optional/tolerant; `magic` is `Int64`
(uint32 overflow would freeze a dead book); a failed bulk close returns **HTTP 200** (check the body);
`ApiResult.timedOut` ≠ `.failed` (a timed-out order may still fill); SL/TP are **point distances** in
requests but **absolute prices** in positions; `Link.up` means "first frame arrived", not "socket open".

## Low-latency stack (all first-party Apple frameworks — zero third-party deps)

- **`URLSessionWebSocketTask`** — the 5 Hz `/ws` feed (`wss://host:8443/ws?hz=5`); manual ~5 s ping for
  dead-socket detection on carrier NAT.
- **`URLSession` (async) + `SocketsHttp`-equivalent** — REST commands, sharing one session/delegate.
- **`Codable`** — JSON, snake_case via explicit `CodingKeys`.
- **Security framework** (`SecPKCS12Import`, `SecIdentity`, `SecTrust*`) — mTLS client identity + CA pinning.
- **Combine** (`CurrentValueSubject`, `@Published`) — conflated feed + per-slice UI updates so a price
  tick doesn't re-render the order form.

## Build

Inside the macOS VM (see [build-vm/README.md](build-vm/README.md) for the VM itself):

```bash
brew install xcodegen              # once
cd /path/to/ios
xcodegen generate                  # -> XauOrderPad.xcodeproj (scheme "XauOrderPad")
# then: open in Xcode, or archive headless per build-vm/README.md §3
```

**Manual fallback (no XcodeGen):** in Xcode, File → New → Project → iOS App (SwiftUI), product name
`XauOrderPad`; delete the generated `ContentView`/`App`; drag the `XauOrderPad/` folder groups in; set
the target's Info.plist to `XauOrderPad/App/Info.plist`; deployment target iOS 16.

Then export an **unsigned** `.ipa` to `/storage/out`, and install from Windows with **Sideloadly**
(free Apple ID). Full pipeline: [build-vm/README.md](build-vm/README.md).

## Verify (end-to-end, TRADE-FREE)

Per the repo safety rule, **no order is placed automatically** — including from any test. Prove the app
without sending trades:

1. **Reachable** — point at the LAN server (`http://<lan-ip>:8765` + token); Connect advances, `/api/config` returns.
2. **Feed** — Trade screen shows bid/ask ticking (~5 Hz), spread in points, equity, and any *already-open* positions (read-only).
3. **mTLS** — upload `ca.crt` + `client.p12` on the Certs screen, point at `https://<host>:8443`, confirm the banner reaches **Live** (handshake + first frame). No server change.
4. **Command path** — exercise a read/no-op only: pull-to-nothing, or toggle a strategy **off**. Do **not** automate BUY/SELL or a live close; leave any real order for a human tap.
5. **Resilience** — restart the server → backoff/reconnect banner → Live; enter a bad token → **Token rejected** routes back to Connect.
