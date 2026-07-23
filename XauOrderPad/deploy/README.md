# MT5 + XauOrderPad on AWS — operator runbook

**This file is what you *do*. [`../DEPLOY_AWS.md`](../DEPLOY_AWS.md) is *why it is built this way*.**
Anything operational belongs here; if the two ever disagree, this file wins and the other is stale.

Everything is already built. The instance exists and is **stopped**. Read the whole thing before
acting.

| | |
|---|---|
| Instance | `i-0758b54d7a39def48` — `m7i-flex.large`, 2 vCPU / 8 GB |
| OS | Windows Server 2022 — Windows is **mandatory**, and not for a reason you can config around ([why](../DEPLOY_AWS.md#why-windows)) |
| Region | `eu-west-2` (London) |
| Disk | 30 GB gp3 |
| Auto-stop | Currently **60 min** after every boot. `autostop <mins>` changes it; ceiling ~390 |
| Open ports | 22 (SSH) + 3389 (RDP) — **your home IP only**. 8443 (mTLS front door) — `0.0.0.0/0`, but every connection without a client certificate is dropped at the TLS handshake. **8765 is never exposed.** |
| Broker | Exness demo `472104398` @ `Exness-MT5Trial16` |
| Key | `deploy\mt5-london-key.pem` — the **only** way to decrypt the Windows password. Back it up. |

Control script: `deploy\mt5_ec2.py`. Every command below has a double-click wrapper in `deploy\bat\`
(`start.bat`, `stop.bat`, `status.bat`, `password.bat`, `tunnel.bat`, `fixfw.bat`, `autostop.bat`,
`eip.bat`, `ship.bat`, `caddy.bat`, `create.bat`, `terminate.bat` — all except `ip`, which is
`python mt5_ec2.py ip`).
The `.bat` files are plain wrappers: they run the Python command and pause. **Nothing is installed
on your laptop and no scheduled task is created there** — the `xauorderpad` task lives on the *box*,
which is what keeps the server alive after you disconnect SSH.

The **public IP changes on every start** — never hardcode it; always re-read it.

> **Secrets live here but are never committed.** `mt5-london-key.pem` (the private SSH key — it
> also decrypts the Windows Administrator password) and `state.json` (the live instance id + IP)
> sit beside `mt5_ec2.py` and are both gitignored. Only the scripts reach the repo.
>
> On a fresh clone, drop those two files in beside `mt5_ec2.py` to make this runnable. Without
> them the scripts fail safe: `status`/`start` report *"No instance in state.json"*, and `create`
> refuses rather than launching a duplicate instance.

---

## Bring it up (~5 min)

```powershell
cd D:\llm\ios\mt5plus\XauOrderPad\deploy

python mt5_ec2.py start      # boots, re-points firewall at your current IP, waits for RDP,
                             # prints the NEW public IP. Auto-stop arms for the current value.
python mt5_ec2.py tunnel     # prints the exact ssh -L command for the current IP
```

Run the `ssh` command it prints, **leave the window open**, then browse the
`http://127.0.0.1:<local>` URL. Log into MT5 from the web UI's **Login** panel — see below.

`start` self-heals the firewall: your home IP is dynamic, so it revokes the stale `/32` rules on
22 + 3389 and authorizes whatever IP you have now. If SSH/RDP hang mid-session because your IP
moved again, `python mt5_ec2.py fixfw`.

### The tunnel

`tunnel` picks a free local port for you — **use the one it gives you, don't assume 8765.**

> **Don't reuse 8765 if XauOrderPad also runs locally.** Your desktop copy already owns
> `127.0.0.1:8765`, so `-L 8765:...` dies with `bind: Address already in use` — and if you then
> browse `http://127.0.0.1:8765` **you are looking at your local MT5, not the box.** Live prices, a
> healthy account, everything looks fine — while the tunnel never opened at all. `tunnel` detects
> this and forwards to 8766 instead, telling you so.
>
> To be certain which server you are on: the box reports `logged_out: true` until you log in
> through *its* UI.

> **Use foreground `ssh -N`.** `ssh -f -N -L ...` on Windows exits 0 and binds the local port but
> does **not** reliably forward — curl hangs and times out. This wasted a debugging round; don't
> repeat it. A `curl` fired immediately after the tunnel opens can also return `000` — retry once
> before concluding anything is broken.

### Starting MT5 — you should not need RDP

**The app launches the terminal itself.** Submitting the Login panel in the web UI calls
`mt5.initialize(path=…, login=…, password=…, server=…)` (`mt5_worker.py:474-485`), which starts
`terminal64.exe` if it isn't running *and* logs it into the account in one step. No credential is
stored on the box. The FastAPI server is already up before any of this — it is a Scheduled Task on
an AtStartup trigger — and if MT5 isn't running yet the worker just retries, so boot order does not
matter.

> **Known gap — this path is not yet proven headless.** Whether MT5 can complete a *broker login*
> while running in **session 0** (launched by the Scheduled Task rather than from a desktop) has
> never been confirmed. The GIL freeze that used to block the test is fixed, but the login itself is
> unverified.
>
> **If the login fails, the fallback is RDP — once.** `python mt5_ec2.py password` decrypts the
> Administrator password; RDP to `<ip>:3389`, launch
> `C:\Program Files\MetaTrader 5\terminal64.exe`, log in with **"Save password"** ticked (that
> writes `accounts.dat`, so future headless launches auto-login), confirm **AutoTrading** is on and
> `XAUUSD` — Exness may name it `XAUUSDm` — is in Market Watch. Then **disconnect** RDP. Do **not**
> log off: logging off kills MT5.

---

## Multiple accounts on one box

One account per **MT5 terminal**, one terminal per **server**, all behind the **same** Caddy on
8443. The terminal is the isolation boundary: `positions_get()` only ever returns the attached
account's book, so a close-all physically cannot reach another account.

**The instance is NAMED after the account, and the name is enforced.** Because the name is numeric
it becomes `expect_login`: the server refuses to log in any other account, and if its terminal is
switched to one by hand it goes unhealthy and the order path shuts. So the URL you are looking at
cannot drift from the account you are trading — a label on a screen with a CLOSE ALL button has to
be a fact, not a mnemonic.

| Path | Port | Terminal | Task | Pinned to |
|---|---|---|---|---|
| `/` | 8765 | `C:\Program Files\MetaTrader 5` | `xauorderpad` | *(unpinned — legacy single-account)* |
| `/472200942` | 8766 | `C:\mt5\472200942` (`/portable`) | `xauorderpad-472200942` | 472200942 |
| `/472103079` | 8767 | `C:\mt5\472103079` | `xauorderpad-472103079` | 472103079 |
| `/472104398` | 8768 | `C:\mt5\472104398` | `xauorderpad-472104398` | 472104398 |

Renaming an instance retires its old task automatically — otherwise the previous, *unpinned* server
would keep holding the same port and quietly serve the account you thought was now guarded.

**One TLS port, not one per account.** The server certificate's SAN is the Elastic *IP* — no port,
no DNS name — so it already covers every port. N proxies would mean N copies of the same private
key, and every extra TLS port is another security-group rule to open and audit on an
internet-facing box. Accounts are selected by **path**, so adding one touches no firewall at all.

### Configure

`deploy/instances.ec2.json` is the box's registry — **not** `XauOrderPad/instances.json`, which is
the laptop's and is deliberately excluded from the ship bundle. Same separation as `certs/` (box)
vs `certs-lan/` (laptop): two machines, two configs, and no path by which one silently becomes the
other. Edit it, then:

```powershell
python mt5_ec2.py ship     # installs the registry + one scheduled task per instance
python mt5_ec2.py caddy    # regenerates routes.caddy and reloads the front door
```

`ship` mints a `.token.<name>.local` per instance if absent. **Each instance gets its own token** —
it is the only credential the trade endpoints check, so a shared one would let a phone profile
saved for one account drive every other. Add one Android *Switch server* profile per account:
`https://<eip>:8443/a1` with a1's token. No app rebuild — the client concatenates the path.

### First run of each portable terminal needs RDP — once

A freshly copied `/portable` terminal has never initialised. It starts, but never creates its
`logs\`, `MQL5\` or `Tester\` folders, and `mt5.initialize()` dies with **`-10005 IPC timeout`**.
That is not a credential problem and retrying does not help: MT5's first-run initialisation needs
an interactive desktop, and the box's autologon session is normally *disconnected*.

So, once per new terminal:

1. `python mt5_ec2.py password` → RDP to `<eip>:3389` as `Administrator`.
2. Launch `C:\mt5\a1\terminal64.exe /portable` (repeat for a2, a3), complete the first-run prompts,
   log the account in with **Save password**, enable **AutoTrading** (Ctrl+E), and confirm `XAUUSD`
   is in Market Watch.
3. **Disconnect** RDP — do *not* log off, which kills every terminal.

After that the API drives them normally and restarts need no RDP.

### Check it

```powershell
python mt5_ec2.py caddy     # its self-test proves a certificate-less client is still rejected
```

Prove the routing rather than assume it: because every instance has a *distinct* token, dialling
`/aN` with token N must return 200 and with any other token must return 401. A full diagonal of
200s is the proof that each prefix reaches exactly one backend.

> **The account lock does not span machines.** `instance_lock` is a local OS file lock, so it stops
> two servers *on this box* driving one account — it cannot see your laptop. If the same account is
> logged in both places, both have an independent close-all path and neither knows about the other.

---

## Reach it from the Android app (mutual TLS)

The phone reaches the box over the public internet on **8443**, guarded by **mutual TLS**: it must
present a client certificate signed by our own private CA, or it is dropped **during the TLS
handshake** — before a single HTTP byte is parsed, before the token is checked, before any MT5 code
is touched.

### First time (per box)

```powershell
python mt5_ec2.py start                     # boot
python mt5_ec2.py eip                       # STABLE public IP + opens 8443 (only 8443)
python make_certs.py --ip <that elastic ip> # mints the CA, server cert, client.p12
python mt5_ec2.py ship                      # source + certs -> box; rebuilds the venv
python mt5_ec2.py caddy                     # the mTLS front door
```

`bat\eip.bat`, `bat\ship.bat`, `bat\caddy.bat` are double-click wrappers for the same thing.

**The Elastic IP is not optional.** A certificate is bound to an *address*, and the auto-assigned
public IP changes on every stop/start — so a cert minted today stops verifying tomorrow, and it
fails as an *opaque TLS handshake error* that looks like a network fault rather than the
expired-address problem it is.

### What the phone needs

| | |
|---|---|
| Android | **14+** (`minSdk 34`) |
| Server address | `https://<elastic-ip>:8443` |
| `ca.crt` | so the phone trusts our private CA — **and only ours** |
| `client.p12` | the phone's own identity, password-protected. **It IS a trading credential.** |
| API token | second factor, checked by FastAPI behind the proxy |

`ca.crt` and `client.p12` are **uploaded in the app's Settings**, not baked into the APK — so
rotating a certificate does not mean rebuilding and reinstalling the app.

### The invariants — do not break these

- **8765 is never opened.** uvicorn stays on `127.0.0.1`. Caddy terminates TLS on 8443 and proxies
  to loopback. This makes the deployment **fail CLOSED**: if Caddy dies or is misconfigured, the
  trading API is *unreachable*, rather than reachable without TLS. `ship.ps1` and `caddy_setup.ps1`
  both assert the loopback bind and refuse to report success if it has moved.
- **8443 is open to `0.0.0.0/0`, deliberately.** The phone roams onto mobile data, so a home-IP rule
  would lock it out. What makes an internet-wide rule defensible is that **mTLS drops everything
  without a client cert at the handshake** — the exposed surface is the TLS stack, not the app.
- **`ca.key` never leaves your laptop.** It can mint new client identities: anyone holding it can
  issue themselves a certificate this server will accept. It is not on the box, and it is gitignored.
- **The token must not reach a log.** `/ws?token=…` carries it in the query string (a browser cannot
  set headers on a WebSocket handshake), so Caddy's access log is switched off — otherwise it writes
  a live trading credential to disk in cleartext. `server.py` already redacts the uvicorn side.

### Rotating a certificate

```powershell
python make_certs.py --ip <elastic ip> --force   # invalidates every client.p12 already issued
python mt5_ec2.py ship
python mt5_ec2.py caddy
```

Then upload the new `ca.crt` + `client.p12` in the app's Settings. `--force` is required precisely
because a silent regeneration would strand the phone with an identity the server no longer accepts.

### A second cert for a LOCAL LAN box (does not touch this deployment)

`make_certs.py` can mint a **second server cert** — for a laptop running the order pad on the LAN —
that reuses **this same CA**, so the phone authenticates to both with **one** `client.p12`:

```powershell
python make_certs.py --ip 192.168.0.116 --server-only --out certs-lan
```

`--server-only` reuses the existing `ca.crt`/`ca.key`, writes **only** `server.crt`/`server.key`, and
**never touches** the CA or `client.p12` (the box's material is unchanged). It refuses to write into
`certs/`, and the output lands in `certs-lan/` — so `ship` (which copies only `deploy/certs/`) can
never send it to the box. Belt-and-braces: **`ship` now refuses outright if `certs/server.crt`'s SAN
is a private IP**, so you cannot accidentally hand the box the LAN cert.

The local run flow (mode switch, Caddy on the laptop, phone URL) lives in
**[../HOW_TO_RUN.md](../HOW_TO_RUN.md)** under *Encrypted LAN (TLS)* — it is not repeated here.

---

## Take it down

```powershell
python mt5_ec2.py stop       # disk kept, public IP released
python mt5_ec2.py status     # confirm: State = stopped
```

Stopped cost is the 30 GB disk only, ~$2.78/mo. The instance also stops itself once the auto-stop
timer expires.

## Change the auto-stop timer

```powershell
python mt5_ec2.py autostop 60      # box must be RUNNING; applies now AND to every future boot
```

`autostop` refuses under 5 min, and over **390** without `--force` — past that a session overruns
the free-tier credits ([the arithmetic](../DEPLOY_AWS.md#cost-guard)).

> **Editing `auto_stop_minutes` in `config.json` by hand does nothing.** The timer lives in the
> `ec2-autostop` scheduled task **on the box**, with its seconds value baked into the task action at
> `create` time by `user_data.ps1` — which has `<persist>false</persist>` and never runs again. Its
> trigger is AtStartup, so it re-arms that same baked-in value on every boot.
>
> `config.json` is read only for the `start` banner and the `status` countdown. Change it alone and
> those will confidently print a number **the box does not honour**. `autostop` is the only thing
> that moves the real timer; it writes `config.json` back afterwards so the display stays honest.

---

## Verify it actually works (do this, don't assume)

`/api/state` returns **HTTP 200 even when MT5 is completely disconnected** — so a 200 proves
nothing. The only honest liveness test is that the price *moves*:

```powershell
# 1. Server alive?
ssh ... 'Get-ScheduledTask -TaskName xauorderpad'                 # State: Running
ssh ... 'Get-NetTCPConnection -LocalPort 8765 -State Listen'      # 127.0.0.1:8765

# 2. Real end-to-end: laptop -> tunnel -> FastAPI -> MT5 IPC -> Exness
curl -s http://127.0.0.1:<local>/api/state    # note bid/ask
curl -s http://127.0.0.1:<local>/api/state    # bid/ask MUST differ
```

A healthy response has a `symbol`, a `bid`/`ask` **that changes between two calls**, and
`account.is_demo = true`. A static bid/ask means MT5 is not connected — the JSON still returns.

**Test orders on the DEMO account only.** `POST /order` 0.01 lot, confirm the position appears in
both the web UI and the MT5 terminal, then close it.

> **A failed bulk close still returns HTTP 200.** `/close_where` answers `{ok: false, remaining: N}`
> with a 200, because that is a well-formed answer rather than a protocol error. **Check the body,
> not the status code** — otherwise a failed emergency close reads as a success.

---

## Server control

Do **not** run `python server.py` over SSH — Windows OpenSSH kills the process tree when the SSH
session closes, and the server dies silently with it. It is a Scheduled Task:

```powershell
Get-ScheduledTask   -TaskName xauorderpad     # State: Running
Start-ScheduledTask -TaskName xauorderpad
Stop-ScheduledTask  -TaskName xauorderpad
```

Config: AtStartup trigger, principal `Administrator` / S4U / Highest, `ExecutionTimeLimit = 0`
(unlimited), 3 restarts on failure. This is also why it comes back by itself after every reboot.

There are **two** tasks. `xauorderpad` is uvicorn (loopback); `xauorderpad-caddy` is the mTLS front
door. Both are AtStartup, `ExecutionTimeLimit = 0`, 3 restarts on failure.

```powershell
Get-ScheduledTask -TaskName xauorderpad,xauorderpad-caddy   # both must be Running
```

**The tasks are created by `ship.ps1` and `caddy_setup.ps1`.** The `xauorderpad` task used to exist
only as a hand-made task on the box — described in detail in these docs and registered by **no
committed script** — so a rebuilt box silently had no server at all. Its action is
`C:\app\run_server.ps1`, a wrapper generated by `ship.ps1` that sets `XAUORDERPAD_HOST/PORT/TOKEN`
and launches the venv's Python; see Security below for why the environment is written there rather
than inherited.

Code lives at `C:\app\XauOrderPad` with its venv at `C:\app\XauOrderPad\.venv`; TLS material in
`C:\app\certs` (Administrators + SYSTEM only). **`ca.key` is deliberately NOT there** — the box has
no business being able to issue new trading identities.

`ship.bat` re-pushes the current source and rebuilds the venv. **The box does not update itself**:
run it after every code change, or you are testing yesterday's server.

**Logs and tools on the box:**

- App log: `C:\Users\Administrator\Documents\XauOrderPad\XauOrderPad-<date>.log` (JSONL)
- `C:\app\diag.py` — prints config invariants, then MT5 `initialize()` / `terminal_info()` /
  `account_info()` / tick. Run it with `C:\app\XauOrderPad\.venv\Scripts\python.exe C:\app\diag.py`
- First-boot bootstrap log: `C:\bootstrap.log`, and `C:\bootstrap-done.txt` if it completed

---

## Security — the operating rules

`HOST`, `PORT` and `API_TOKEN` come from the **environment**, so no secret sits in a git-tracked
file (`config.py:110-117`):

```python
HOST      = os.environ.get("XAUORDERPAD_HOST",  "127.0.0.1")
PORT      = int(os.environ.get("XAUORDERPAD_PORT", "8765"))
API_TOKEN = os.environ.get("XAUORDERPAD_TOKEN", "")
```

**On the box, `ship.ps1` sets these — do not try to set them by hand.** `HOST` stays `127.0.0.1`
there **on purpose**: Caddy is the only thing on a public address, so uvicorn being loopback-only is
what makes the deployment fail closed.

> ⚠ **This section used to say "set both in the Scheduled Task's environment."** That was not
> executable advice: **Task Scheduler has no environment field** — an action is exe + arguments +
> working directory, nothing more.
>
> The tempting fix, a machine-level env var, is a trap. A task inherits the environment block held
> by the *Schedule service*, and that service captured it **at boot** — so a variable set now is not
> seen by a task started now. It fails **silently**, and it fails **open**: `XAUORDERPAD_TOKEN` comes
> back `""`, which *disables authentication* on the trading API.
>
> So `ship.ps1` writes the values into `C:\app\run_server.ps1`, the wrapper the task actually
> executes. They are set in the process that serves, at the moment it starts. No inheritance,
> nothing to go stale.

- **The defaults are loopback + NO AUTHENTICATION.** That is harmless behind the SSH tunnel and
  dangerous anywhere else — this process places **real MT5 orders**.
- **A token is MANDATORY the moment `HOST` is not loopback.** Set `XAUORDERPAD_TOKEN`. The web UI
  understands it: it prompts you once at load (via the unauthenticated `GET /api/config` probe,
  which reports *that* a token is needed, never what it is), sends `x-token` on every trade-capable
  call (`webui/app.js:81`), and puts `?token=` on the `/ws` URL (`webui/app.js:1249`).
- **`/ws` is token-checked too.** A bad token closes the socket with code **4401**. Without this it
  streamed balance, equity and every open position to anyone who could reach the port — *even with
  `API_TOKEN` set on every other route*. ([Why the close lands *after* `accept()`, and why that
  ordering is load-bearing](../DEPLOY_AWS.md#security-model))
- **Never set `HOST = "0.0.0.0"` on the box.** On a public cloud host that publishes the trading API
  to the internet *without TLS and without client-cert auth*, bypassing Caddy entirely. The only
  legitimate bind here is `127.0.0.1`; the proxy is the front door. Both `ship.ps1` and
  `caddy_setup.ps1` assert this and refuse to report success if the bind has moved.
- **mTLS is the outer gate; the token is the inner one.** The certificate authenticates the
  *device*, the token authorises the *call*. Keep both: the token can be rotated without reissuing
  certificates, and a stolen token alone is useless without a client certificate.
- **`ca.key` must never reach the box or the repo.** It mints client identities — whoever holds it
  can issue themselves a certificate this server accepts.
- Never bake broker credentials into `user_data.ps1` — it is readable via the instance metadata
  service.
- **`*.pem`, `state.json` and `certs/` are gitignored — leave them that way.** A leaked key cannot
  be un-published; you would have to rebuild the instance and rotate.

> ⚠ **This section used to say the opposite** — that `API_TOKEN = ""` was deliberate, and that
> setting a token gave you *"a healthy-looking UI with live quotes and a dead Buy button"*. That was
> true only while `webui/app.js` never sent the header. **It does now.** Setting a token is the
> correct, supported configuration; not setting one on a non-loopback bind is a live vulnerability.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| UI won't load; requests hang 25 s+ | Worker calling `initialize()` with no creds → a ~65 s GIL stall that freezes uvicorn | `mt5_worker.py:61` must be `_session_active = False`. Log shows `mt5_init_failed` every ~65 s. [Why](../DEPLOY_AWS.md#why-the-worker-does-not-auto-connect-at-boot) |
| HTTP 200 but `bid`/`ask` are `null` | Terminal running but **not logged in** | Log in via the UI. `initialize()` → `(-10005, 'IPC timeout')` is the signature. |
| SSH / RDP hang | Your home IP changed | `fixfw.bat`, or just `start.bat` (self-heals) |
| `InvalidInstanceID.NotFound` | `state.json` points at a terminated instance | Delete `state.json`, then `create.bat` |
| `ship`: *"REFUSING TO SHIP … a PRIVATE address"* | `certs/server.crt` is the **LAN** cert, not the box's | Re-mint the box cert: `make_certs.py --ip <elastic ip>`. The LAN cert belongs in `certs-lan/` (see *A second cert for a LOCAL LAN box*). |
| Server vanished after you disconnected SSH | It wasn't started as the Scheduled Task | `Start-ScheduledTask -TaskName xauorderpad` |
| `curl` through the tunnel returns `000` | Raced the SSH forward coming up | Retry |
| Buy/Sell return 401 | The UI has no token, or a stale one | Reload the pad — it prompts. Or clear it: `localStorage.removeItem('xop.token')`. Check `XAUORDERPAD_TOKEN` on the box matches. **Do not "fix" this by blanking `API_TOKEN`** on a non-loopback bind — that publishes an unauthenticated trading API. |
| UI reconnects forever, "disconnected" | `/ws` rejected the token (close 4401) | Same as above. |

## Known gap: unattended restart

After a stop/start the `xauorderpad` task relaunches itself, but **MT5's broker login is unproven
headless** (see *Starting MT5* above). Making it fully unattended requires auto-logon plus a startup
entry for MT5, which puts the Windows password in the registry. Not done; ask before adding it.

## Traps already hit (all fixed — don't reintroduce)

- `schtasks /Create /TR "cmd with ""quotes"""` fails **silently** through PowerShell. The auto-stop
  task is never created, no error is logged, and the box runs until the credits are gone.
  `user_data.ps1` now uses `Register-ScheduledTask` and asserts the task exists afterwards.
- Windows OpenSSH kills the process tree on disconnect → background servers must be Scheduled Tasks.
- `ssh -f -N -L` on Windows: exits 0, binds locally, doesn't forward. Use foreground `-N`.
- `sshd` caches the machine PATH from before Python was installed → `user_data.ps1` restarts sshd.
- Inline `python -c "..."` over bash→ssh→PowerShell gets its quotes mangled. Use a script file.
- Grepping `config.py` for `LAUNCH_BROWSER = False` matches the **comment** on line 52, not the
  assignment. Anchor to `^LAUNCH_BROWSER\s*=`, or better: `import config` and read the value.
  A patcher that greps will report success while changing nothing.
