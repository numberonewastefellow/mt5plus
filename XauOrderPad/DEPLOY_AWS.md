# Deploying XauOrderPad to AWS EC2

Run the order pad on a cloud Windows box instead of your desktop. You keep using it from your own
browser — over an SSH tunnel, so nothing is exposed to the internet.

```
Your laptop                          │  AWS eu-west-2 (London)
                                     │
Chrome ──► 127.0.0.1:8765 ═══SSH tunnel═══► 127.0.0.1:8765  FastAPI (server.py)
                                     │                        │ single worker thread
                                     │                        ▼  (official IPC)
                                     │                   terminal64.exe ──► Exness
```

The FastAPI server still binds **127.0.0.1 only**, exactly as it does locally. The tunnel is what
makes it reachable from your machine — and the only thing that does.

## Where the scripts live

Everything is in **`XauOrderPad\deploy\`** — one copy, no duplicates:

```
XauOrderPad\deploy\
    mt5_ec2.py           config.json          user_data.ps1        README.md
    mt5-london-key.pem   state.json           <- gitignored, never commit
    bat\  create · start · stop · status · password · tunnel · fixfw · terminate
```

Two files are **gitignored** and so are absent from a fresh clone:

| File | What it is |
|---|---|
| `mt5-london-key.pem` | Private SSH key. Grants SSH to the box and decrypts the Windows Administrator password. |
| `state.json` | The live instance id + public IP. |

**On a fresh machine**, restore them beside `mt5_ec2.py`. With the `.pem` but no `state.json`,
`create.bat` builds a new box from scratch. Without the `.pem`, the scripts **fail safe**: `create`
refuses (*"Key pair exists in AWS but …pem is missing"*) rather than launching a duplicate instance.

## Why Windows, not Linux

`mt5_worker.py` imports `MetaTrader5`, which ships **only** as `_core.cp312-win_amd64.pyd` — a
Windows DLL. MetaQuotes publishes no Linux wheel, and this app has no ZeroMQ/socket/EA bridge to
route around it; it makes ~15 MT5 API calls over Windows IPC. Linux would mean running the whole
stack under Wine, or rewriting `mt5_worker.py`. **This is not a config change — don't try.**

## What's deployed

| | |
|---|---|
| Instance | `i-0758b54d7a39def48` — `m7i-flex.large`, 2 vCPU / 8 GB |
| OS | Windows Server 2022 |
| Region | `eu-west-2` (London — near the Exness/LD4 broker DC) |
| Disk | 30 GB gp3 |
| App | `C:\app\XauOrderPad` (own venv at `.venv`) |
| Auto-stop | Currently **60 min** after every boot — the cost guard. `autostop.bat` changes it; ceiling ~390. |
| Open ports | 22 (SSH) + 3389 (RDP), **your home IP only**. 8765 is *never* opened. |

**The public IP changes on every start.** Never hardcode it; always re-read it from `start.bat` or
`status.bat`.

---

## Daily use

```powershell
cd D:\llm\ios\mt5plus\XauOrderPad\deploy

start.bat        # boot; re-points firewall at today's home IP; prints the NEW public IP
tunnel.bat       # prints the exact ssh -L command + which local port to browse
                 # run that command, LEAVE IT OPEN, then browse the URL it printed
                 # log into MT5 from the web UI's Login panel
stop.bat         # when done (or let the auto-stop do it)

status.bat       # state, IP, auto-stop countdown, port reachability
password.bat     # Administrator password, if you want RDP
fixfw.bat        # re-point the firewall at your current home IP
autostop.bat     # change how long the box stays up (box must be RUNNING)
```

> **Hand-editing `auto_stop_minutes` in `config.json` does nothing.** The real timer is the
> `ec2-autostop` scheduled task **on the box**, with its seconds value baked in at `create` by
> `user_data.ps1` (which has `<persist>false</persist>` and never runs again). `config.json` only
> feeds the `start` banner and the `status` countdown — change it alone and those print a number the
> box ignores. `autostop.bat` is the only thing that moves the real timer, and it writes
> `config.json` back so the display stays honest.

Your home IP is dynamic. `start` self-heals the firewall automatically; `fixfw.bat` is for when it
changes mid-session and SSH/RDP suddenly hang.

## Tunnel configuration

**Just run `tunnel.bat`** — it prints the right command and picks a free local port for you.
Manually, it's:

```powershell
ssh -i "mt5-london-key.pem" -N -L <local>:127.0.0.1:8765 Administrator@<current-ip>
```

Your laptop's `<local>` port is forwarded over the encrypted SSH connection to the box's loopback,
where uvicorn is listening.

> ### ⚠ The trap: if XauOrderPad also runs LOCALLY, do not use 8765
>
> Your desktop copy of the app already owns `127.0.0.1:8765`. So `-L 8765:127.0.0.1:8765` dies with
> `bind: Address already in use` — and if you then browse `http://127.0.0.1:8765` **you are looking
> at your local MT5, not the box.** It shows live prices and a healthy account, so it looks like the
> cloud deployment is working when the tunnel never opened at all.
>
> `tunnel.bat` detects this and forwards to **8766** instead, telling you so. Browse the port it
> prints. To be certain which server you're on: the box shows `logged_out: true` until you log in
> through *its* UI.

> **Use foreground `-N`.** `ssh -f -N -L ...` on Windows exits 0 and binds the local port but does
> **not** reliably forward — curl hangs, the browser spins, and everything looks like a server
> problem. Keep the window open for the session.

A `curl` fired immediately after opening the tunnel can return `000`; the forward isn't up yet.
Retry once before concluding anything is broken.

---

## How MT5 is launched on the remote

**You do not RDP in and start it by hand.** The app launches the terminal itself.

When you submit the Login panel in the web UI, `POST /api/login` reaches `_login`
(`mt5_worker.py:474-485`), which calls:

```python
mt5.initialize(path=config.MT5_PATH, login=…, password=…, server=…)
```

That call **launches `terminal64.exe` if it isn't running** and logs it into the account in one
step. `MT5_PATH` on the box points at `C:\Program Files\MetaTrader 5\terminal64.exe`.

So the sequence is: `start.bat` → tunnel → open the UI → log in. Nothing is stored on the box, and
no credential lives in any config file.

### Why the worker does *not* auto-connect at boot

`mt5_worker.py:61` sets `self._session_active = False` at construction. **This line is
load-bearing.** If it were `True`, the worker would call `mt5.initialize()` with no credentials at
boot; the terminal it spawns never logs in, so the IPC handshake never completes, and
`initialize()` blocks ~65 s **inside the C extension without releasing the GIL** — freezing the
entire Python process, uvicorn included. The UI can't load, so you can't log in, so it never stops
freezing. See Diagnosis.

`_login` ignores that flag and sets it `True` on success; `_logout` sets it back to `False`.

## How the FastAPI server is hosted

It runs as a **Windows Scheduled Task** named `xauorderpad` — AtStartup trigger, principal
`Administrator` (S4U), `ExecutionTimeLimit = 0` (unlimited), 3 restarts on failure.

```powershell
Get-ScheduledTask   -TaskName xauorderpad     # State: Running
Start-ScheduledTask -TaskName xauorderpad
Stop-ScheduledTask  -TaskName xauorderpad
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

> **Do not start it over SSH with `python server.py`.** Windows OpenSSH kills the process tree when
> the SSH session closes, so the server dies silently the moment you disconnect. Task Scheduler owns
> it instead, which is also why it comes back by itself after every reboot.

---

## Security model — read before changing anything

- **Never set `HOST = "0.0.0.0"`.** That exposes the trading API to the internet. Port 8765 is never
  in the security group. There are exactly two legitimate binds:
  - `127.0.0.1` (the default) — desktop use, reachable only through the SSH tunnel.
  - the box's **Tailscale** address (`100.x.y.z`) — so the Android client can reach it over
    WireGuard. **8765 still stays closed in the security group**; the tailnet is the only path in.
    A Tailscale address is *not* a public bind. `0.0.0.0` still never is.

- **`HOST` and `API_TOKEN` now come from the environment**, so no secret lives in a git-tracked file.
  (`mt5plus` is a repo — a committed token is one `git add .` from publication, which is exactly why
  `*.pem` is already ignored.)

  ```python
  HOST      = os.environ.get("XAUORDERPAD_HOST",  "127.0.0.1")
  API_TOKEN = os.environ.get("XAUORDERPAD_TOKEN", "")
  ```

  The defaults reproduce the old behaviour exactly — loopback bind, no auth — so a plain
  `python server.py` on the desktop is unchanged. On the box, set both in the `xauorderpad`
  Scheduled Task's environment.

- **`API_TOKEN` is REQUIRED whenever `HOST` is not loopback**, and the web UI now understands it.
  > ⚠ **This reverses the advice that used to live here.** The old text said a blank `API_TOKEN` was
  > deliberate, and that setting one gave you *"a healthy-looking UI with live quotes and a dead Buy
  > button"*. That was true **only because `webui/app.js` never sent the header**. It now sends
  > `x-token` on every trade-capable call, puts `?token=` on the `/ws` URL, and prompts you for the
  > token once at load (via the unauthenticated `GET /api/config` probe, which reports *that* a token
  > is needed, never what it is). Setting a token is now the correct, supported configuration.

- **`/ws` is token-checked too — it was not before.** It streamed balance, equity and every open
  position to anyone who could reach the port, *even with `API_TOKEN` set on every other route*. A
  bad token now closes the socket with code **4401**.
  > The check deliberately runs **after** `accept()`. Closing *before* `accept()` makes Starlette
  > reject the HTTP handshake with a 403, and a browser cannot read a close code from a failed
  > handshake — it reports `1006`. The client's "stop retrying, the token is wrong" branch keys on
  > 4401, so a pre-accept close would send it into an infinite 1 s reconnect loop against a server
  > that will never let it in. Accepting costs nothing: no state is sent before the close.

- Never bake broker credentials into `user_data.ps1` — it's readable via the EC2 instance
  metadata service.
- `mt5-london-key.pem` is the **only** way to decrypt the Windows Administrator password, and it
  grants SSH to the box. Back it up **outside** the repo.
- **`*.pem` is gitignored — leave it that way.** `mt5plus` is a git repo, so an un-ignored key is
  one `git add .` away from being published, and a leaked key can't be un-published: you'd have to
  rebuild the instance and rotate the key pair. `deploy/state.json` (instance id + IP) is ignored
  too. The scripts, `.bat` launchers and `config.json` *are* committed — only the secrets are not.

---

## Diagnosis

Verify with a **real** check, not a hopeful one. `/api/state` returns HTTP 200 even when MT5 is
completely disconnected — so a 200 proves nothing.

```powershell
# The only honest liveness test: call twice, prices must CHANGE.
curl -s http://127.0.0.1:8765/api/state    # note bid/ask
curl -s http://127.0.0.1:8765/api/state    # bid/ask must differ; account.is_demo must be true
```

| Symptom | Cause | Fix |
|---|---|---|
| UI won't load; requests hang 25 s+ | Worker calling `initialize()` with no creds → ~65 s GIL stall freezing uvicorn | Ensure `mt5_worker.py:61` is `_session_active = False`. Log shows `mt5_init_failed` every ~65 s. |
| HTTP 200 but `bid`/`ask` are `null` | Terminal running but **not logged in** | Log in via the UI. `initialize()` → `(-10005, 'IPC timeout')` is the signature. |
| SSH / RDP hang | Your home IP changed | `fixfw.bat`, or just `start.bat` (self-heals) |
| `InvalidInstanceID.NotFound` | `state.json` points at a terminated instance | Delete `state.json` in the live `deploy\`, then `create.bat` |
| Server vanished after you disconnected SSH | It wasn't started as the Scheduled Task | `Start-ScheduledTask -TaskName xauorderpad` |
| `curl` through the tunnel returns `000` | Raced the SSH forward coming up | Retry |
| Buy/Sell return 401 | The UI has no token, or a stale one | Reload the pad — it prompts. Or clear it: `localStorage.removeItem('xop.token')`. Check `XAUORDERPAD_TOKEN` on the box matches. **Do not "fix" this by blanking `API_TOKEN`** if `HOST` is non-loopback — that publishes an unauthenticated trading API to the tailnet. |
| UI reconnects forever, "disconnected" | `/ws` rejected the token (close 4401) | Same as above. If it retries in a tight 1 s loop instead of prompting, the `/ws` close is landing pre-`accept()` as a 403 — see Security. |

**Logs and tools on the box:**

- App log: `C:\Users\Administrator\Documents\XauOrderPad\XauOrderPad-<date>.log` (JSONL)
- `C:\app\diag.py` — prints config invariants, then MT5 `initialize()` / `terminal_info()` /
  `account_info()` / tick. Run with `C:\app\XauOrderPad\.venv\Scripts\python.exe C:\app\diag.py`
- Bootstrap log from first boot: `C:\bootstrap.log`, and `C:\bootstrap-done.txt` if it completed

**Still unproven:** whether MT5 can complete a broker **login while running in session 0** (i.e.
launched by the Scheduled Task rather than from an RDP desktop). The GIL freeze that blocked this
test is fixed, but the login itself hasn't been confirmed headless. If it fails, RDP in once and
log into MT5 with **"Save password"** ticked — that writes `accounts.dat` and the terminal will
auto-login on every future headless launch.

---

## First-time deploy (already done once)

`create.bat` builds everything from nothing:

1. Resolves the latest Windows Server 2022 AMI from an SSM public parameter
2. Creates key pair `mt5-london-key` → saves `mt5-london-key.pem`
3. Creates security group `mt5plus-sg` — opens **only** 22 + 3389, **only** to your home IP
4. `run_instances` with `user_data.ps1` and
   `InstanceInitiatedShutdownBehavior = stop` (so a Windows shutdown *stops* the instance rather
   than destroying it — this is what makes the auto-stop safe)

`user_data.ps1` runs once on first boot and installs: OpenSSH Server + your authorized key,
Python 3.12, the MT5 terminal (silent), and the `ec2-autostop` scheduled task.

Then ship the app: `scp` the `XauOrderPad\` folder (minus `.venv\`) to `C:\app\`, create the venv,
`pip install -r requirements.txt`. On the box, `config.py` differs from local in exactly two ways:
`LAUNCH_BROWSER = False` (headless) and `MT5_PATH` set to the installed terminal.

> `create.bat` refuses to run while `state.json` exists. `terminate.bat` **deletes** the instance,
> its disk, the key pair and the security group — the MT5 install and the whole `C:\app` setup go
> with it.

## Cost guard

The AWS account is on the **restricted Free Tier plan** ($200 credits, ~6 months), which rejects
any instance type not free-tier-eligible. The only x86 (Windows-capable) types allowed, in every
region:

    t3.micro (1G) → t3.small (2G) → c7i-flex.large (4G) → m7i-flex.large (8G)

`t3.medium` is **blocked** despite costing ~⅓ as much — eligibility tracks instance *generation*,
not price. `t4g.*` are ARM and can't run Windows.

At 132 h/month (6 h/day × 22) this box costs **$177.58 of the $200 over six months**. The ceiling is
**~142 h/month**, and the `ec2-autostop` task on the box is what enforces it.
**A session above ~390 min overruns the credits** — which is why `autostop` refuses past that
without `--force`.

`POLL_HZ = 15` also drives ~0.1–0.3 GB/hr of egress per open browser tab (the `/ws` state push).
That's free under the 100 GB/month allowance; drop it to 5 if you leave tabs open for long periods.

Clients can now also ask for a **slower push per connection** — `/ws?hz=5` — clamped to
`1..POLL_HZ`. The Android app uses `hz=5`, because at 15 Hz the full-snapshot stream is a lot of
mobile data for a screen showing a quote and a few rows. Unchanged snapshots are skipped entirely,
so a quiet or logged-out server sends almost nothing.
