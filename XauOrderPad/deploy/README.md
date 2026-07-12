# MT5 + XauOrderPad on AWS — operator runbook

Everything is already built. The instance exists and is **stopped**. This file is the complete
procedure to bring it up, use it, and take it down again. Read the whole thing before acting.

| | |
|---|---|
| Instance | `i-0758b54d7a39def48` — `m7i-flex.large`, 2 vCPU / 8 GB |
| OS | Windows Server 2022 (Windows is **mandatory**, see below) |
| Region | `eu-west-2` (London) |
| Disk | 30 GB gp3 |
| Auto-stop | Currently **60 min** after every boot. Change with `autostop <mins>`; ceiling ~390 (see Cost guard) |
| Open ports | 22 (SSH) + 3389 (RDP), **your home IP only**. Port 8765 is never exposed. |
| Broker | Exness demo `472104398` @ `Exness-MT5Trial16` |
| Key | `deploy\mt5-london-key.pem` — **only** way to decrypt the Windows password. Back it up. |

Control script: `deploy\mt5_ec2.py` (double-click equivalents in `deploy\bat\`).
The **public IP changes on every start** — never hardcode it; always re-read it.

> **Secrets live here but are never committed.** `mt5-london-key.pem` (the private SSH key — it
> also decrypts the Windows Administrator password) and `state.json` (the live instance id + IP)
> sit beside `mt5_ec2.py` and are both gitignored. Only the scripts reach the repo.
>
> On a fresh clone, drop those two files in beside `mt5_ec2.py` to make this runnable. Without
> them the scripts fail safe: `status`/`start` report *"No instance in state.json"*, and `create`
> refuses rather than launching a duplicate instance.

---

## Bring it up (~5 min, then a manual MT5 step)

```powershell
cd D:\llm\ios\mt5plus\XauOrderPad\deploy

python mt5_ec2.py start      # boots, re-points firewall at your current IP, waits for RDP,
                             # prints the NEW public IP. Auto-stop arms for the current value.
python mt5_ec2.py password   # decrypts the Administrator password for RDP
```

`start` self-heals the firewall: your home IP is dynamic, so it revokes the stale `/32` rules on
22 + 3389 and authorizes whatever IP you have now. If you're ever locked out, `python mt5_ec2.py fixfw`.

### Then the one manual step: start MT5 over RDP

MT5 is a GUI app and needs an interactive session. It cannot be started over SSH.

1. RDP to `<new-ip>:3389` as `Administrator` with the password from above.
2. Launch `C:\Program Files\MetaTrader 5\terminal64.exe`. It should remember the Exness account.
3. Confirm **AutoTrading** is on and `XAUUSD` (Exness may name it `XAUUSDm`) is in Market Watch.
4. **Disconnect** RDP. Do **not** log off — logging off kills MT5.

The FastAPI server does **not** need this step. It runs as a Scheduled Task (`xauorderpad`,
trigger AtStartup) and is already running before you log in. If MT5 isn't up yet the worker just
retries, so boot order does not matter.

### Use it

```powershell
python mt5_ec2.py tunnel     # prints the exact ssh -L command for the current IP
```

Run that command, **leave the window open**, then browse the `http://127.0.0.1:<local>` URL it
prints. `tunnel` picks a free local port for you — use the one it gives you, don't assume 8765.

> **Don't reuse 8765 if XauOrderPad also runs locally.** Your desktop copy already owns
> `127.0.0.1:8765`, so `-L 8765:...` dies with `bind: Address already in use` — and if you then
> browse `http://127.0.0.1:8765` **you are looking at your local MT5, not the box.** Live prices, a
> healthy account, everything looks fine — while the tunnel never opened at all.
>
> **Use foreground `ssh -N`.** `ssh -f -N -L ...` on Windows exits 0 and binds the local port but
> does **not** reliably forward — curl hangs and times out. This wasted a debugging round; don't
> repeat it.

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

> **Editing `auto_stop_minutes` in `config.json` by hand does nothing.** The timer lives in the
> `ec2-autostop` scheduled task **on the box**, with its seconds value baked into the task action at
> `create` time by `user_data.ps1` — which has `<persist>false</persist>` and never runs again. Its
> trigger is AtStartup, so it re-arms that same baked-in value on every boot.
>
> `config.json` is read only for the `start` banner and the `status` countdown. Change it alone and
> those will confidently print a number **the box does not honour**. `autostop` is the only thing
> that moves the real timer; it writes `config.json` back afterwards so the display stays honest.

`autostop` refuses under 5 min, and over 390 without `--force` (see Cost guard).

---

## Verify it actually works (do this, don't assume)

From the box, or through the tunnel from your laptop:

```powershell
# 1. Server alive?
ssh ... 'Get-ScheduledTask -TaskName xauorderpad'                 # State: Running
ssh ... 'Get-NetTCPConnection -LocalPort 8765 -State Listen'      # 127.0.0.1:8765

# 2. Real end-to-end: laptop -> tunnel -> FastAPI -> MT5 IPC -> Exness
curl -s http://127.0.0.1:8765/api/state
```

A healthy response has a `symbol`, a `bid`/`ask` **that changes between two calls**, and
`account.is_demo = true`. A static bid/ask means MT5 is not connected — the JSON still returns.

```
POST /order with {}  ->  HTTP 422   (validation)  = trading path OK
POST /order with {}  ->  HTTP 401                 = a token got set; see Security
```

**Test orders on the DEMO account only.** `POST /order` 0.01 lot, confirm the position appears in
both the web UI and the MT5 terminal, then `POST /close_all`.

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
(unlimited), 3 restarts on failure. App logs: `C:\Users\Administrator\Documents\XauOrderPad\*.log`.

Code lives at `C:\app\XauOrderPad` with its venv at `C:\app\XauOrderPad\.venv`.

---

## Security — read before changing anything

- **`HOST = "127.0.0.1"`. Never set `0.0.0.0`.** The trading API would be exposed to the internet.
  The SSH tunnel is the only intended path in; port 8765 is never in the security group.
- **`API_TOKEN = ""` is deliberate, not an oversight.** `webui/app.js` never sends the `x-token`
  header. Setting a token 401s every `/buy`, `/sell`, `/order`, `/close`, `/close_all` — while
  `/`, `/api/state` and `/ws` keep working. Result: a healthy-looking UI with live quotes and a
  **dead Buy button**, discovered only when you press it. To use a token, patch the UI's `fetch`
  calls first.
- Never bake broker credentials into `user_data.ps1` — it is readable via the instance metadata
  service.

---

## Cost guard

Account `835191025788` is on AWS's **restricted Free Tier plan** ($200 credits, ~6 months). It
refuses any instance type not free-tier-eligible. The only x86 (Windows-capable) types allowed,
identical in every region:

    t3.micro (1G) -> t3.small (2G) -> c7i-flex.large (4G) -> m7i-flex.large (8G)

`t3.medium` is **blocked** despite being ~⅓ the price — eligibility tracks instance generation,
not cost. `t4g.*` are ARM and cannot run Windows.

At 132 h/mo (6 h/day × 22) this box costs **$177.58 of the $200 over six months**. The ceiling is
**~142 h/mo**. The `ec2-autostop` task on the box is what enforces it — **a session above ~390 min
overruns the credits**, which is why `autostop` refuses past that without `--force`. Reserve ~$10 for
the Mumbai VPN box (`d:\Soft\aws\aws_try`).

After the credits expire this is ~₹2,813/mo. That is the moment to reconsider upgrading the
account plan (unlocks `t3.medium`, ~₹1,151/mo) — first confirm in the Billing console that
remaining credits survive the switch.

`POLL_HZ = 15` in `XauOrderPad/config.py` pushes ~0.1–0.3 GB/hr of egress per open browser tab
(free under the 100 GB/mo allowance). Drop to 5 if you leave tabs open.

---

## Why Windows (do not try to "fix" this)

`XauOrderPad` imports `MetaTrader5`, whose wheel is `_core.cp312-win_amd64.pyd` — a Windows DLL.
MetaQuotes ships **no Linux wheel**, and the app has no ZeroMQ/socket/EA bridge to fall back on;
it makes ~15 MT5 API calls over Windows IPC into `terminal64.exe`. Linux would mean running the
whole stack under Wine, or rewriting `mt5_worker.py`. It is not a config change.

Related: `mt5.initialize()` returns `(-10005, 'IPC timeout')` when **no account is logged in**.
Once the terminal is up and logged in, the IPC *does* cross sessions — a session-0 process (SSH,
or a Scheduled Task) can drive a terminal running in RDP session 2. Only MT5 needs the interactive
session; the server does not.

---

## Known gap: unattended restart

After a stop/start, `xauorderpad` relaunches itself, but **MT5 does not** — it needs an interactive
logon that doesn't exist on a fresh boot. The worker retries harmlessly until you RDP in and start
the terminal. Making it fully unattended requires auto-logon plus a startup entry for MT5, which
puts the Windows password in the registry. Not done; ask before adding it.

---

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
