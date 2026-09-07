# `mt5/tools/` — operator utilities

## `reset_demo_balance.bat` — set the demo account back to $1

`RecoveryGridScalper` is tested from a **$1 base**, repeatedly: the whole question is how the
compounding progression behaves starting from $1, so every run needs the account put back. Exness
exposes this in the personal area ("Set balance"); this replays the same request.

**Double-click `reset_demo_balance.bat`.** That is the whole normal workflow.

```
reset_demo_balance.bat                  # set balance to $1
reset_demo_balance.bat 10               # some other amount (positional)
reset_demo_balance.bat -Amount 10       # same thing, spelled out
```

MT5 picks the new balance up within a second — the EA panel's `acct : bal` line refreshes at 1 Hz.

---

## The session expires every ~6 hours. Plan for it.

This is the one thing to understand about this tool. The Exness personal area authenticates with a
**JWT valid for about 6 hours**, so a script that merely replays a captured `curl` works this
afternoon and returns an opaque `403` tomorrow morning.

So the script **decodes the token's own `exp` claim and refuses to send** once it is expired or
within 5 minutes of it, and tells you to re-capture. A clear refusal beats a confusing failure.

### Refreshing it — a paste, not an edit

1. Firefox → <https://my.exness.com/pa/trading/accounts> (logged in)
2. **F12 → Network**
3. Click **Set balance** on the demo account and submit it once
4. Right-click the `set_balance` request → **Copy → Copy as cURL**
5. Paste into a scratch file, e.g. `%TEMP%\exness.txt`
6. `reset_demo_balance.bat -ImportCurl %TEMP%\exness.txt -DeleteSource`

The import keeps **only** the `Cookie:` header and throws the rest away. It handles the Windows
"Copy as cURL (cmd)" form, where every quote arrives as `^"` and `|`, `%`, `$`, `{`, `}` are
`^`-escaped — strip that first or the cookie is unfindable. It also refuses an already-expired
capture rather than storing a dead token.

---

## Where the secret lives, and why it is not here

**Nothing in `mt5/tools/` contains a credential, and nothing here ever should.** The session is in
exactly one file, outside the repository:

```
%USERPROFILE%\Documents\MilkyAppData\MyCred\exness_session.txt
```

It is a **curl config file** (`curl --config`) with one line:

```
header = "Cookie: country=AE; cf_clearance=...; JWT=eyJ..."
```

Three deliberate choices:

* **A config file, not `-H` on the command line.** A command line is readable by every process on
  the machine — Task Manager's *Command line* column, `Get-CimInstance Win32_Process`, PowerShell
  history, console scrollback. `--config` keeps the token out of all of them.
* **ACL-locked on every run.** The script runs `icacls /inheritance:r /grant:r <you>:(F)` each time,
  so a file created by hand with default permissions heals itself.
* **Redacted output.** Every line the script prints passes through a filter that replaces anything
  matching `eyJ…` or `cf_clearance=…`, so an unexpected error dump cannot echo a usable token.

### What that cookie actually grants

Treat it like `XauOrderPad/.token.local` or the deploy `.pem`:

| item | what it is |
|---|---|
| `JWT=eyJ…` | a signed session for the **whole Exness account**, not just the demo one |
| `cf_clearance=…` | Cloudflare bot clearance, bound to your **IP and User-Agent** |

Because `cf_clearance` is IP-bound, a VPN or network change produces a `403` *before* the JWT
expires. The script names that case specifically so it is not mistaken for an auth problem.

The `User-Agent` in the script must stay byte-identical to the browser the token was captured from,
or the clearance stops matching. It is not a secret, so it lives in the script.

---

## Safety properties

* **The account number is a script constant** (`472627873`, Exness-MT5Trial16 **demo**). There is
  deliberately **no `-Account` parameter** — a switch that lets it point elsewhere is a switch that
  can point it at the wrong account by accident.
* **Amounts above 100 are refused** unless `-Force`, so a fat-finger cannot set 100000.
* **This places no orders.** Setting a demo balance touches no position and no order path, so the
  no-auto-trade rule in `CLAUDE.md` is not in play here. Nothing in this folder can trade.

### Exit codes

| code | meaning |
|---|---|
| 0 | balance set |
| 2 | no session file (or it has no `Cookie:` line) |
| 3 | session expired / expiring — **nothing was sent** |
| 4 | the request was sent and failed (HTTP status is printed) |
| 5 | bad argument, or a bad `-ImportCurl` file |
