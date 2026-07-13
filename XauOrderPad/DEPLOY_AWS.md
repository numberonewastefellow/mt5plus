# XauOrderPad on AWS — architecture and decisions

**Operating it day to day → [`deploy/README.md`](deploy/README.md).** That is the runbook: bring it
up, tunnel in, log into MT5, tear it down, troubleshoot. **This file is why it is built that way.**

Keep the split. These two files used to overlap, drifted, and ended up contradicting each other on
the API token — with the runbook, the file an operator actually opens, carrying the wrong advice.
Anything you *do* belongs in the runbook; anything you must *understand before changing something*
belongs here.

## The shape of it

Run the order pad on a cloud Windows box instead of your desktop. You keep using it from your own
browser — over an SSH tunnel, so nothing is exposed to the internet.

```
Your laptop                          │  AWS eu-west-2 (London)
                                     │
Chrome ──► 127.0.0.1:<local> ═══SSH tunnel═══► 127.0.0.1:8765  FastAPI (server.py)
                                     │                        │ single worker thread
                                     │                        ▼  (official IPC)
                                     │                   terminal64.exe ──► Exness
```

The FastAPI server binds **127.0.0.1 only**, exactly as it does locally. The tunnel is what makes it
reachable from your machine — and the only thing that does. Port 8765 is never in the security
group.

The instance spec, the region, the auto-stop value and the open ports are all in the runbook's table.
They are operating facts; they change. The reasoning below does not.

## Why Windows

`mt5_worker.py` imports `MetaTrader5`, which ships **only** as `_core.cp312-win_amd64.pyd` — a
Windows DLL. MetaQuotes publishes no Linux wheel, and this app has no ZeroMQ/socket/EA bridge to
route around it; it makes ~15 MT5 API calls over Windows IPC into `terminal64.exe`. Linux would mean
running the whole stack under Wine, or rewriting `mt5_worker.py` against a different transport.

**This is not a config change — don't try.**

Related, and useful: `mt5.initialize()` returns `(-10005, 'IPC timeout')` when **no account is
logged in**. Once the terminal is up and logged in, the IPC *does* cross Windows sessions — a
session-0 process (an SSH command, or a Scheduled Task) can drive a terminal running in RDP session
2. Only MT5 needs the interactive session; the server does not.

## Why the worker does not auto-connect at boot

`mt5_worker.py:61` sets `self._session_active = False` at construction. **This line is
load-bearing.**

If it were `True`, the worker would call `mt5.initialize()` with no credentials at boot. The terminal
it spawns never logs in, so the IPC handshake never completes, and `initialize()` blocks for ~65 s
**inside the C extension without releasing the GIL** — freezing the entire Python process, uvicorn
included. The UI cannot load, so you cannot log in, so it never stops freezing. The symptom is a UI
that hangs for 25 s+ and an app log repeating `mt5_init_failed` every ~65 s.

`_login` ignores the flag and sets it `True` on success; `_logout` sets it back to `False`. Login is
therefore always user-initiated, from the web UI's Login panel, which calls
`mt5.initialize(path=…, login=…, password=…, server=…)` — that one call launches `terminal64.exe` if
it isn't running *and* logs it into the account. No broker credential is ever stored on the box.

> **Still unproven:** whether MT5 can complete a broker login while running in **session 0** (i.e.
> the terminal launched by the Scheduled Task rather than from an RDP desktop). The GIL freeze that
> blocked this test is fixed; the login itself has never been confirmed headless. The runbook
> documents the RDP + "Save password" fallback.

## How the server is hosted

A **Windows Scheduled Task** named `xauorderpad` — AtStartup trigger, principal `Administrator`
(S4U), `ExecutionTimeLimit = 0`, 3 restarts on failure — not a plain `python server.py`.

The reason is not aesthetic: **Windows OpenSSH kills the process tree when the SSH session closes**,
so a server started over SSH dies silently the moment you disconnect. Task Scheduler owns it instead,
which is also what brings it back after every reboot. Commands to control it are in the runbook.

## Security model

**`HOST`, `PORT` and `API_TOKEN` come from the environment**, so no secret lives in a git-tracked
file (`config.py`). `mt5plus` is a repo — a committed token is one `git add .` from publication,
which is exactly why `*.pem` is already ignored.

```python
HOST      = os.environ.get("XAUORDERPAD_HOST",  "127.0.0.1")
PORT      = int(os.environ.get("XAUORDERPAD_PORT", "8765"))
API_TOKEN = os.environ.get("XAUORDERPAD_TOKEN", "")
```

The defaults reproduce the original behaviour exactly — loopback bind, no auth — so a plain
`python server.py` on the desktop is unchanged.

**On the EC2 box there is exactly ONE legitimate bind: `127.0.0.1`.**

Caddy is the only process on a public address. uvicorn stays on loopback, and that is what makes the
deployment fail closed (see below). On a *desktop*, `0.0.0.0` is reasonable — it means "the LAN",
behind your router's NAT, and `HOW_TO_RUN.md` documents it with a mandatory token. **On the box it
means "the internet"**, bypassing TLS and client-cert auth entirely. Never set it there.

### Reaching the box from the phone — mutual TLS

The phone reaches the box **over the public internet**, on port **8443**, and must present a
**client certificate** signed by our own private CA. Without one it is dropped **during the TLS
handshake**.

That last sentence is the whole design. A scanner that finds the open port never speaks HTTP, never
reaches FastAPI, never reaches the token check, and never touches a line of MT5 code. The exposed
attack surface is the TLS stack, not the application.

**Why not just a server certificate + the API token?** Because the token is a **bearer** secret:
anything that obtains it — a log line, a phone backup, a screenshot — can place orders. A client
certificate's private key never leaves the phone, so there is nothing replayable to intercept. The
token stays as a *second* factor: the certificate authenticates the **device**, the token authorises
the **call**, and the token can be rotated without reissuing certificates.

**Why it costs nothing at runtime**, which matters for a low-latency app: TLS 1.3 is a 1-RTT
handshake and a client certificate adds no extra round trip. The quote feed is a **single long-lived
WebSocket**, so the handshake is paid *once*; steady-state cost is AES-GCM, i.e. microseconds. It is
a connection-level control, not a per-request one.

#### TLS terminates in Caddy, not in uvicorn

```
phone ──TLS 1.3 + client cert──► :8443  Caddy  ──plain HTTP──► 127.0.0.1:8765  uvicorn
   (internet)                    (mTLS gate)      (loopback)
```

Three properties follow, and they are the reason for the split:

- **`server.py` is unchanged.** No `ssl_keyfile`, no new dependency, no new failure mode in the
  trading path.
- **The SSH tunnel still works** for the browser UI, which hits uvicorn directly. Had uvicorn itself
  enforced mTLS, the browser would need a client certificate too — uvicorn binds exactly ONE address.
- **It fails CLOSED.** uvicorn stays on `127.0.0.1`, so if Caddy is down, misconfigured, or not yet
  started, the trading API is **unreachable** — rather than reachable *without* TLS, which is what a
  `0.0.0.0` bind would give you. `ship.ps1` and `caddy_setup.ps1` both assert the loopback bind and
  refuse to report success if it has moved.

#### The private CA is a feature, not a compromise

The app is configured to trust **our CA and nothing else**, so **no public CA can mis-issue a
certificate for this service** — not a compromised one, not a coerced one. That is strictly stronger
than a Let's Encrypt certificate. The price is that we distribute `ca.crt` ourselves, which is what
the app's Settings → Certificates screen is for: **certificates are uploaded at runtime, never baked
into the APK**, so rotating one does not mean rebuilding and reinstalling the app.

**`ca.key` never leaves the laptop.** It mints client identities; anyone holding it can issue
themselves a certificate this server accepts. It is not on the box and it is gitignored.

#### Why an Elastic IP is mandatory

A certificate is bound to an **address** (`subjectAltName = IP:…`), and the auto-assigned public IP
**changes on every stop/start**. A certificate minted today would stop verifying tomorrow — failing
as an *opaque TLS handshake error* that reads like a network fault, not like the expired-address
problem it actually is. Hence `mt5_ec2.py eip`.

The SAN must be an **IP** entry, not a DNS name or a bare CN: OkHttp verifies an IP-literal URL
against `iPAddress` SANs only.

#### Two traps this design walked into

- **Caddy enables `strict_sni_host` automatically once client auth is configured.** It then requires
  the `Host` header to match the TLS SNI — but a client connecting to a bare **IP sends no SNI at
  all**. Every request is answered **421 Misdirected Request** while the handshake and the client
  certificate both succeed. It looks exactly like an application bug. `strict_sni_host insecure_off`
  is correct here: it guards against vhost confusion across *multiple name-based sites*, and there
  is one site, reached by IP.
- **The API token rides in the WebSocket query string** (`/ws?token=…` — a browser cannot set headers
  on a WS handshake). Caddy's access log records the full URI, so leaving it on writes a live trading
  credential to disk in cleartext. The log is switched off; `server.py` already redacts the uvicorn
  side (`_RedactToken`).

**`API_TOKEN` is REQUIRED whenever `HOST` is not loopback.** The web UI understands it: it prompts
once at load (via the unauthenticated `GET /api/config` probe, which reports *that* a token is
needed, never what it is), sends `x-token` on every trade-capable call (`webui/app.js:81`), and puts
`?token=` on the `/ws` URL (`webui/app.js:1249`).

> ⚠ **This reverses advice that used to live in both files.** The old text said a blank `API_TOKEN`
> was deliberate, and that setting one gave you *"a healthy-looking UI with live quotes and a dead
> Buy button"*. That was true **only because `webui/app.js` never sent the header**. It does now.
> Setting a token is the correct, supported configuration — and on a non-loopback bind, mandatory.

**`/ws` is token-checked too — it was not before.** It streamed balance, equity and every open
position to anyone who could reach the port, *even with `API_TOKEN` set on every other route*. A bad
token now closes the socket with code **4401**.

> **The check deliberately runs *after* `accept()`** (`server.py:564-573`). Closing *before*
> `accept()` makes Starlette reject the HTTP handshake with a 403, and **a browser cannot read a
> close code from a failed handshake** — it reports `1006`. The client's "stop retrying, the token is
> wrong" branch keys on 4401, so a pre-accept close sends it into an infinite 1 s reconnect loop
> against a server that will never let it in. Accepting costs nothing: no state is sent before the
> close.

Two more, briefly:

- Never bake broker credentials into `user_data.ps1` — it is readable via the EC2 instance metadata
  service.
- `mt5-london-key.pem` is the **only** way to decrypt the Windows Administrator password, and it
  grants SSH to the box. It and `deploy/state.json` are gitignored; keep them that way. A leaked key
  cannot be un-published — you would have to rebuild the instance and rotate the key pair.

## Cost guard

The AWS account is on the **restricted Free Tier plan** ($200 credits, ~6 months), which rejects any
instance type that is not free-tier-eligible. The only x86 (Windows-capable) types allowed, in every
region:

    t3.micro (1G) → t3.small (2G) → c7i-flex.large (4G) → m7i-flex.large (8G)

`t3.medium` is **blocked** despite costing ~⅓ as much — eligibility tracks instance *generation*, not
price. `t4g.*` are ARM and cannot run Windows.

At 132 h/month (6 h/day × 22) the `m7i-flex.large` box costs **$177.58 of the $200 over six months**.
The ceiling is **~142 h/month**, and the `ec2-autostop` task on the box is what enforces it. **A
single session above ~390 min overruns the credits** — which is why `autostop` refuses past that
without `--force`. Reserve ~$10 for the Mumbai VPN box (`d:\Soft\aws\aws_try`).

After the credits expire this is ~₹2,813/mo. That is the moment to reconsider upgrading the account
plan (it unlocks `t3.medium`, ~₹1,151/mo) — first confirm in the Billing console that remaining
credits survive the switch.

**Egress.** `POLL_HZ = 15` drives ~0.1–0.3 GB/hr per open browser tab (the `/ws` state push), free
under the 100 GB/month allowance. Clients can ask for a slower push per connection — `/ws?hz=5`,
clamped to `1..POLL_HZ`. The Android app uses `hz=5`, because at 15 Hz a full-snapshot stream is a lot
of mobile data for a screen showing a quote and a few rows. Unchanged snapshots are skipped, so a
quiet or logged-out server sends almost nothing — but a 1 Hz heartbeat still goes out regardless, so
the client can tell "nothing changed" from "the feed died".

## First-time deploy (already done once)

`create.bat` builds everything from nothing:

1. Resolves the latest Windows Server 2022 AMI from an SSM public parameter
2. Creates key pair `mt5-london-key` → saves `mt5-london-key.pem`
3. Creates security group `mt5plus-sg` — opens **only** 22 + 3389, **only** to your home IP
4. `run_instances` with `user_data.ps1` and `InstanceInitiatedShutdownBehavior = stop`, so a Windows
   shutdown *stops* the instance rather than destroying it — this is what makes the auto-stop safe

`user_data.ps1` runs once on first boot and installs OpenSSH Server + your authorized key, Python
3.12, the MT5 terminal (silent), and the `ec2-autostop` scheduled task.

Then ship the app: `scp` the `XauOrderPad\` folder (minus `.venv\`) to `C:\app\`, create the venv,
`pip install -r requirements.txt`. On the box, `config.py` differs from local in exactly two ways:
`LAUNCH_BROWSER = False` (headless) and `MT5_PATH` set to the installed terminal.

> `create.bat` refuses to run while `state.json` exists. `terminate.bat` **deletes** the instance, its
> disk, the key pair and the security group — the MT5 install and the whole `C:\app` setup go with it.
