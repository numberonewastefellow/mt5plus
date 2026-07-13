# XauOrderPad — Android client

Kotlin + Jetpack Compose phone client for the XAUUSD order pad. Live quote, order form, positions
grid with live P&L, and three one-tap buttons that **flatten the book**. Sideloaded; no Play Store.

**Build happens in Docker. Install happens from Windows.** That split is deliberate and is the one
thing to understand before reading anything else.

---

## Quick start

```bat
cd android

deploy.bat                    :: build -> copy APK out -> install -> launch
deploy.bat logs               :: logcat, filtered to this app
```

That is the whole loop. First build is slow (SDK download + cold Gradle); afterwards it is seconds.

---

## Why Docker, and why adb is *not* in Docker

**No Android Studio is installed, and none will be.** Two things get conflated; only the first is
needed:

| | Size | |
|---|---|---|
| Android **SDK** + Gradle + JDK 17 | ~2–3 GB, **in the container** | compiles the APK — required |
| Android **Studio** | ~8–10 GB, on Windows | an IDE on top of that SDK — not used |

So the SDK lives in the container and nothing heavy lands on Windows — except `platform-tools`
(~15 MB), which is needed because:

> **Docker Desktop on Windows cannot pass USB through.**

That is a platform limit, not a configuration mistake. A container can never see a USB-attached
phone. So `deploy.bat` **builds in the container** and **installs from Windows adb**, which handles
USB *and* wireless with one binary. Do not try to "fix" this by moving adb into the container.

The bridge between the two halves is one file copy — see below.

---

## The APK is invisible from Windows until it is copied

`app/build/` is a **named Docker volume**, not a bind-mount. That is why incremental builds take
~3 s: Gradle does tens of thousands of small file operations, and every one of them would otherwise
cross Docker Desktop's gRPC-FUSE boundary.

The cost is that **`android\app\build\` on Windows is empty**. If you go looking for the APK there
you will find nothing and conclude the build failed. It did not.

`deploy.bat` copies it across:

```
container  /workspace/app/build/outputs/apk/debug/app-debug.apk
              |  docker compose cp
              v
container  /out/app-debug.apk
              |  (bind-mount)
              v
Windows    E:\temp\mt5_data\app-debug.apk
```

---

## `deploy.bat` — every sub-command

| Command | What it does |
|---|---|
| `deploy.bat` *(no args)* | Same as `all`. |
| `deploy.bat all` | The hot loop: build in the container → copy the APK to `E:\temp\mt5_data` → `adb install -r` → launch the app. |
| `deploy.bat build` | Build only. `docker compose exec build sh ./gradlew :app:assembleDebug`, then copies the APK out. |
| `deploy.bat install` | Install the **existing** APK. Fails if there is none. Warns if it is more than 10 minutes old — that stale-APK warning exists because installing yesterday's build and debugging it for an hour is a genuinely easy mistake. |
| `deploy.bat pair <ip:port> <code>` | **Wireless only.** One-time pairing. |
| `deploy.bat connect <ip:port>` | **Wireless only.** Connects, and remembers the address in `.deploy-phone`. |
| `deploy.bat status` | Container state, adb path, `adb devices -l`, the remembered wireless address, the installed versionCode on the phone, and the APK on Windows. Start here when something is wrong. |
| `deploy.bat logs` | `logcat` for the app's PID. If the app is not running, shows crashes only (`AndroidRuntime:E`). |
| `deploy.bat uninstall` | Removes the app **and wipes its stored data** — including the saved server URL and API token. Prompts first. |

### USB vs wireless

**USB needs no pairing at all.** Enable USB debugging, plug in, accept the RSA prompt on the phone,
and `deploy.bat` just works.

**Wireless needs `pair` once, then `connect`.** The trap, and it catches everyone:

> The **pairing dialog** shows an `ip:port` **and** a 6-digit code. That port is a **different port**
> from the one on the main Wireless debugging screen. Use the dialog's port for `pair`, and the main
> screen's port for `connect`. The dialog also expires after about a minute.

The connect port **changes every time wireless debugging is toggled off and on**, which is why
`connect` writes it to `android/.deploy-phone` (gitignored — it is machine-local noise). Override it
with the `XAU_PHONE` environment variable, which wins over the file.

---

## The stack, and why

| | | |
|---|---|---|
| Gradle | 8.13 | AGP 8.13's minimum |
| AGP | 8.13.0 | supports `compileSdk 36`; AGP 8.7 was only tested to 35 |
| JDK | 17 | AGP 8.13's minimum *and* default |
| compileSdk / targetSdk | 36 | |
| **minSdk** | **34** | see below |
| Kotlin | 2.1.0 | the Compose compiler ships inside Kotlin 2.x |

### `minSdk 34` — the phone must be Android 14+

The foreground service is `specialUse`. That service type, its `FOREGROUND_SERVICE_SPECIAL_USE`
permission, and `ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE` **all landed in API 34**. At
`minSdk 31` every one of them is a `NewApi` lint violation — and lint's `NewApi` is **fatal on
release**, so `assembleRelease` fails outright. (`assembleDebug` passes, because debug does not run
`lintVital`. That asymmetry has already misled people here.)

`specialUse` itself is non-negotiable: `dataSync` is capped at a **cumulative 6 hours per 24 h** on
Android 15, which would silently sever the price feed mid-session.

> **An older phone will refuse to install the APK with a message that does not explain why.**

---

## Pre-filling the server URL and token (dev convenience)

Create `android/local.properties` — **gitignored**, so the token cannot be committed:

```properties
xau.baseUrl=http://192.168.0.116:8765
xau.token=<the token the server was started with>
```

These are compiled into `BuildConfig` and used to pre-fill the Connect screen, so the app opens
straight on the Trade screen after a reinstall.

**Debug builds only. The release variant compiles them as `""`.** A signed release APK with a live
trading token inside it is a secret you cannot rotate — anyone holding the APK can `strings` it out,
and that token places orders on a real account.

`Secrets.kt` seeds from these defaults **only when nothing is stored**, so a value typed on the
Connect screen always wins and is never silently reverted by the next install.

---

## Pointing the app at a server

**No rebuild.** The base URL is a *setting* — `Secrets.normalizeBaseUrl` takes whatever you type,
defaulting a bare host to `http://host:8765` and preserving an explicit `https://`. One build reaches
**both** servers:

| Target | What you type | Certificate |
|---|---|---|
| LAN dev server | `192.168.0.116:8765` (plain HTTP) | **none** |
| EC2 box | `https://<elastic-ip>:8443` (mutual TLS) | **required** — uploaded in Settings |

**The app now does mutual TLS** (`net/Tls.kt`, `data/CertStore.kt`). For the EC2 box:

1. Generate the certs on the laptop: `python XauOrderPad\deploy\make_certs.py --ip <elastic-ip>`.
2. Move `ca.crt` and `client.p12` to the phone **over USB/MTP** — `client.p12` is a trading
   credential; do not email it.
3. In the app: **Settings → Certificates (mTLS)** → import both files + the p12 password → LOAD.
4. Connect to `https://<elastic-ip>:8443` + the API token.

The client trusts **our private CA only** (`Tls.kt`), so no public CA can impersonate the box, and it
presents `client.p12` as its identity — Caddy drops any connection without it at the TLS handshake.
Uploading a rotated cert is a Settings action, not a reinstall (`Feed.reloadTls()` rebuilds the HTTP
client live). Certs are **not** baked into the APK.

Having a cert loaded does **not** affect the LAN HTTP path: OkHttp only uses the TLS factory for
`https://` URLs. The broker password, however, is **blocked** from being sent over plain HTTP
(`loginWith` refuses when `passwordInClear`) — add accounts over https or on the desktop.

Do **not** reach the box by opening 8765 on its public IP: that is uvicorn, plain HTTP, and the token
grants order placement. 8443 (Caddy, mTLS) is the only way in. Set the box up with the
`eip` → `ship` → `caddy` flow in [XauOrderPad/deploy/README.md](../XauOrderPad/deploy/README.md).

Phone requirement: Android **14+** (`minSdk 34`).

---

## Release signing

Four environment variables, supplied via `android/.env` (gitignored) → docker compose:

```
XAU_KEYSTORE=/out/keystore/xau-release.jks     # host: E:\temp\mt5_data\keystore\
XAU_KEYSTORE_PASSWORD=...
XAU_KEY_ALIAS=...
XAU_KEY_PASSWORD=...
```

The keystore lives **outside the repo** on purpose. If the vars are absent, the release
`signingConfig` is simply **not registered** and `assembleRelease` fails loudly. That is deliberate:
a "release" that silently fell back to the debug key would be worse than a build failure, because
Android refuses to update an installed APK signed with a different key — recovery means
uninstalling, which wipes the stored token.

**Back the keystore up somewhere outside this repo.** Lose it and you can never ship an update over
the installed app.

---

## Building the release APK — two forms

```
deploy.bat release         # CLEAN: no URL/token/cert baked in. The shippable, secret-free build.
deploy.bat release demo    # embeds URL + token + demo cert (-Pxau.embedSecrets=true).
```

Both are signed with the `xau` release key and are **not** `debuggable`; the APK lands at
`E:\temp\mt5_data\app-release.apk`. The difference is only what is compiled in:

- **`release`** — `DEFAULT_BASE_URL` / `DEFAULT_TOKEN` / `DEFAULT_P12_PASSWORD` are `""` and **no**
  `client.p12` is packaged (the demo certs live in `app/src/demoCerts/`, added to the release assets
  *only* under `-Pxau.embedSecrets`). Nothing to extract — safe to distribute. The user types the URL +
  token and imports a cert manually.
- **`release demo`** — self-contained: the Connect screen is pre-filled and `CertStore.seedFromAssetsIfEmpty`
  auto-loads the bundled cert on first launch, so it connects to the box with **no manual setup**.

## Giving the `release demo` APK to someone else

Verified end-to-end (clean uninstall → install → first-launch CONNECT reaches a live box feed, cert
auto-loaded, no import, no trust error):

1. Send **only** `app-release.apk` (the `demo` build). They install it, open it, tap **CONNECT**. That is
   the whole setup — no certificate to send separately.
2. They share the **same** demo account the box is logged into (`472200942`). The box must be **running**
   with MT5 logged in and **AutoTrading ON**, or they connect but the feed is flat / orders are refused.

**The `demo` APK IS a trading credential.** Anyone holding the file can unzip it and pull out the client
cert + token and trade that demo account. Hand it to one trusted person — never a public link. It is a
*demo* account, so the blast radius is demo funds; the client cert is still not individually revocable
(no CRL), so if it leaks the only remedy is regenerating the CA (`make_certs.py --force`), which
invalidates every device including your own phone.

## Planned: hardening the demo APK (NOT yet implemented — see `TODO.md`)

To raise the bar against a leaked `demo` APK while keeping the cert/token baked in, the agreed *future*
work is:

1. **Time-derived login code** — the friend enters a short code computed from the current clock on each
   connect (e.g. `23:07` → `2+3+7 = 12`); the app computes the same from its own clock and refuses to
   connect on a mismatch. Use an **hourly bucket** (or ±1 minute tolerance) so he isn't racing a
   per-minute change and to survive clock skew between phone and box.
2. **R8 obfuscation** (`isMinifyEnabled = true` + `shrinkResources`) to scramble the baked strings/classes.
3. **Android Keystore** for the unlocked cert + token (non-exportable) instead of plain files/prefs.
4. Keep the bundled cert + token (current `release demo`) — the code is an *added gate*, not a replacement.

**Be honest about what this buys.** It is **layered obfuscation, not a lock.** The time-code rule is a
fixed algorithm compiled into the app; R8 obfuscates it but does not hide it — a determined attacker reads
it, reproduces it, and still extracts the baked cert. Two consequences:

- To make the code *actually* gate access, fold in a **secret only the friend knows** — e.g.
  `sum-of-time + a shared PIN` — so knowing the public algorithm is not enough.
- **Only a secret that is NOT in the APK** (a passphrase/activation code, or a server-provisioned
  per-device cert) gives real leak resistance. This stack is best-effort for a *demo* credential; do **not**
  rely on it for a real-money account.

Also: R8 cannot be turned on blind — it needs **keep rules for kotlinx.serialization** generated
serializers (the exact reason it is off today, `app/build.gradle.kts:108`), or WebSocket frames silently
fail to parse.

---

## Machine-specific paths

`E:/temp/mt5_data` is hardcoded in **two** places and will not exist on another machine:

- `docker-compose.yml` → the `/out` bind-mount
- `deploy.bat` → `OUT_W`

Change both together, or nothing will find the APK.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `android\app\build\` is empty | Not a failure. It is a named volume — see above. |
| Build suddenly takes 2 minutes | The container was recreated, killing the warm Gradle daemon. Never use `docker compose run --rm`; `deploy.bat` only starts the container if it is not already up, precisely to avoid this. |
| `adb` not found | `winget install Google.PlatformTools` |
| Install fails, no clear reason | The phone is older than Android 14. See `minSdk 34` above. |
| Wireless `connect` fails after it worked yesterday | The port changed when wireless debugging was toggled. Re-run `pair` + `connect`. |
| App can't reach the server | The server is probably bound to `127.0.0.1`, which a phone can never reach. This is **not** a firewall problem. See [../XauOrderPad/HOW_TO_RUN.md](../XauOrderPad/HOW_TO_RUN.md). |
