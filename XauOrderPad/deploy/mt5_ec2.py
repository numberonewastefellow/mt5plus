#!/usr/bin/env python3
"""
MT5 + XauOrderPad on a Windows EC2 box.

    python mt5_ec2.py create      # key pair + security group + Windows Server 2022 + bootstrap
    python mt5_ec2.py status      # state, public IP, bootstrap progress, minutes to auto-stop
    python mt5_ec2.py start       # start; re-points firewall at your current home IP
    python mt5_ec2.py stop        # stop now (keeps disk; auto-assigned IP is released)
    python mt5_ec2.py password    # decrypt the Windows Administrator password (for RDP)
    python mt5_ec2.py tunnel      # print the ssh -L command for the browser UI
    python mt5_ec2.py ip          # current public IP
    python mt5_ec2.py fixfw       # re-point firewall (22 + 3389) at your current home IP
    python mt5_ec2.py autostop 60 # re-arm the shutdown timer (this session + every future boot)
    python mt5_ec2.py terminate   # DELETE everything

The auto-stop timer lives in the `ec2-autostop` scheduled task ON THE BOX, with its seconds
value baked in at `create`. Editing auto_stop_minutes in config.json does NOT move it - only
`autostop` does. config.json is written back by `autostop` so status/start stay honest.

Only 22 (SSH) and 3389 (RDP) are ever opened, and only to your home IP. Port 8765 is
NEVER exposed: XauOrderPad binds 127.0.0.1 and its trading API is unauthenticated by
default. Reach the UI with `tunnel`.

Structure deliberately mirrors D:\\Soft\\aws\\aws_try\\aws_ec2.py.
"""

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 is not installed. Run:  python -m pip install --user boto3")

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    sys.exit("cryptography is not installed. Run:  python -m pip install --user cryptography")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
USERDATA_PATH = os.path.join(HERE, "user_data.ps1")
AUTOSTOP_PATH = os.path.join(HERE, "autostop.ps1")
SHIP_PS1_PATH = os.path.join(HERE, "ship.ps1")
CADDY_PS1_PATH = os.path.join(HERE, "caddy_setup.ps1")
CADDYFILE_PATH = os.path.join(HERE, "Caddyfile")
AUTOLOGON_PS1_PATH = os.path.join(HERE, "autologon.ps1")

RDP_PORT = 3389
SSH_PORT = 22

# The mTLS front door (Caddy). This is the ONLY port ever exposed to the internet.
#
# 8765 -- uvicorn -- is NEVER opened, and that is load-bearing rather than cautious. uvicorn
# speaks plain HTTP and the API token is a bearer secret that places orders on a live account.
# Caddy terminates TLS on 8443, REQUIRES a client certificate signed by our private CA, and
# proxies to 127.0.0.1:8765. Because uvicorn stays on loopback, the deployment FAILS CLOSED: if
# Caddy is down or misconfigured, the trading API is not exposed at all -- rather than exposed
# without TLS, which is what binding uvicorn to 0.0.0.0 would give you.
TLS_PORT = 8443

# Above ~390 min/session the box overruns the $200 free-tier credits (see README cost guard).
AUTO_STOP_CEILING_MIN = 390


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def ec2_client(cfg):
    return (boto3.client("ec2", region_name=cfg["region"]),
            boto3.client("ssm", region_name=cfg["region"]))


def resolve_windows_ami(ssm):
    param = "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base"
    return ssm.get_parameter(Name=param)["Parameter"]["Value"]


def my_public_ip():
    import urllib.request
    try:
        return urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5).read().decode().strip()
    except Exception:
        return None


def current_public_ip(ec2, iid):
    d = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    return d.get("PublicIpAddress")


def require_instance(state):
    if not state.get("instance_id"):
        sys.exit("No instance in state.json. Run 'create' first.")
    return state["instance_id"]


def pem_path(cfg):
    return os.path.join(HERE, f"{cfg['key_name']}.pem")


def load_private_key(cfg):
    p = pem_path(cfg)
    if not os.path.exists(p):
        sys.exit(f"Private key missing: {p}")
    with open(p, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def openssh_pubkey(private_key):
    return private_key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def lock_key_perms(p):
    """Restrict the .pem so OpenSSH accepts it."""
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return
    user = os.environ.get("USERNAME") or "%USERNAME%"
    for args in (["icacls", p, "/inheritance:r"], ["icacls", p, "/grant:r", f"{user}:(R)"]):
        try:
            subprocess.run(args, capture_output=True, text=True)
        except Exception:
            pass


def wait_for_port(ip, port, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=5):
                print(f"\n✅ port {port} open on {ip}")
                return True
        except Exception:
            print(f"   ...waiting for port {port}", end="\r")
            time.sleep(10)
    print(f"\n⚠️  port {port} on {ip} did not open within {timeout}s.")
    print("   If your home IP changed, run 'fixfw' and retry.")
    return False


# ---------------- create ----------------

def ensure_key_pair(ec2, cfg):
    name = cfg["key_name"]
    p = pem_path(cfg)
    existing = ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [name]}])["KeyPairs"]
    if existing:
        if not os.path.exists(p):
            sys.exit(f"Key pair '{name}' exists in AWS but {p} is missing. "
                     f"Delete the key pair in AWS or restore the .pem - the Windows "
                     f"Administrator password can only be decrypted with it.")
        print(f"Key pair '{name}' already exists (using it).")
        return name
    key = ec2.create_key_pair(KeyName=name, KeyType="rsa", KeyFormat="pem")
    with open(p, "w", encoding="utf-8") as f:
        f.write(key["KeyMaterial"])
    lock_key_perms(p)
    print(f"Created key pair '{name}' -> {p}")
    print("  BACK THIS FILE UP. It is the only way to recover the Windows password.")
    return name


def ensure_security_group(ec2, cfg):
    name = f"{cfg['project_tag']}-sg"
    existing = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [name]}])["SecurityGroups"]
    if existing:
        return existing[0]["GroupId"]
    sg_id = ec2.create_security_group(GroupName=name, Description="MT5 box - managed by mt5_ec2.py")["GroupId"]
    ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "Project", "Value": cfg["project_tag"]}])
    print(f"Created security group {name} ({sg_id})")
    myip = my_public_ip()
    if not myip:
        sys.exit("Could not detect your public IP; refusing to open RDP/SSH to the world.")
    src = f"{myip}/32"
    for port, desc in ((SSH_PORT, "SSH from home"), (RDP_PORT, "RDP from home")):
        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                "IpRanges": [{"CidrIp": src, "Description": desc}]}])
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
    print(f"  allowed {src} on {SSH_PORT} + {RDP_PORT}")
    return sg_id


def find_security_group(ec2, cfg):
    sgs = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [f"{cfg['project_tag']}-sg"]}])["SecurityGroups"]
    if not sgs:
        sys.exit(f"No security group {cfg['project_tag']}-sg. Was the box created by this script?")
    return sgs[0]["GroupId"]


def ensure_tls_ingress(ec2, cfg):
    """Open 8443 -- and ONLY 8443 -- to the internet.

    0.0.0.0/0 is deliberate and is the whole reason mTLS exists in this deployment: the phone
    roams onto mobile data, so a home-IP rule (like the one guarding SSH/RDP) would lock it out.
    What makes an internet-wide rule defensible is that Caddy demands a client certificate signed
    by our private CA and drops everything else DURING THE TLS HANDSHAKE -- a scanner never
    reaches FastAPI, never reaches the token check, never touches MT5.

    Note refresh_firewall() only ever revokes rules on ports {22, 3389}, so it will not strip
    this one out from under us.
    """
    sg_id = find_security_group(ec2, cfg)
    try:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": TLS_PORT, "ToPort": TLS_PORT,
            "IpRanges": [{"CidrIp": "0.0.0.0/0",
                          "Description": "XauOrderPad mTLS front door (Caddy)"}]}])
        print(f"  opened {TLS_PORT}/tcp to 0.0.0.0/0 (mTLS-guarded)")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        print(f"  {TLS_PORT}/tcp already open")
    return sg_id


def cmd_eip(cfg, args):
    """Give the box a STABLE public IP, and open the mTLS port.

    Without this the whole certificate design is impossible. The auto-assigned public IP changes
    on every stop/start, and the server certificate is bound to an address (SAN = IP:...). A cert
    minted for today's IP stops verifying tomorrow, and it fails as an opaque TLS handshake error
    that looks like a network fault -- not like the expired-address problem it actually is.

    Cost, stated plainly: since Feb-2024 AWS bills EVERY public IPv4, including the auto-assigned
    one already in use. So an EIP attached to a RUNNING box costs exactly what today costs. The
    only new charge is while the box is STOPPED -- an idle EIP is ~$0.005/hr (~$3.65/mo).
    terminate.bat releases it; if it is ever orphaned it bills forever, which is why release is
    wired into teardown rather than left as a note.
    """
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)

    alloc_id = state.get("eip_alloc_id")
    if alloc_id:
        try:
            addr = ec2.describe_addresses(AllocationIds=[alloc_id])["Addresses"][0]
        except ClientError:
            print(f"state.json points at {alloc_id}, which no longer exists. Allocating a new one.")
            alloc_id = None
    if not alloc_id:
        a = ec2.allocate_address(Domain="vpc",
                                 TagSpecifications=[{"ResourceType": "elastic-ip",
                                                     "Tags": [{"Key": "Project",
                                                               "Value": cfg["project_tag"]}]}])
        alloc_id = a["AllocationId"]
        addr = ec2.describe_addresses(AllocationIds=[alloc_id])["Addresses"][0]
        print(f"Allocated Elastic IP {addr['PublicIp']}")

    eip = addr["PublicIp"]
    if addr.get("InstanceId") != iid:
        # The box must be running to associate; a stopped instance has no ENI attachment to bind.
        st = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["State"]["Name"]
        if st != "running":
            sys.exit(f"Instance is {st}. Run 'start' first, then 'eip' -- an Elastic IP can only "
                     f"be associated with a running instance.")
        ec2.associate_address(AllocationId=alloc_id, InstanceId=iid)
        print(f"Associated {eip} -> {iid}")
    else:
        print(f"{eip} is already associated with {iid}")

    ensure_tls_ingress(ec2, cfg)

    state["eip_alloc_id"] = alloc_id
    state["elastic_ip"] = eip
    state["public_ip"] = eip
    save_state(state)

    print(f"""
  Elastic IP: {eip}   (STABLE -- survives stop/start)

  This address is what the server certificate is bound to. Next:

      python make_certs.py --ip {eip}
      python mt5_ec2.py ship
      python mt5_ec2.py caddy

  The phone then connects to  https://{eip}:{TLS_PORT}
""")


def build_user_data(cfg, pubkey):
    with open(USERDATA_PATH, encoding="utf-8") as f:
        script = f.read()
    mins = int(cfg["auto_stop_minutes"])
    return (script
            .replace("{{SSH_PUBKEY}}", pubkey)
            .replace("{{PYTHON_URL}}", cfg["python_url"])
            .replace("{{MT5_SETUP_URL}}", cfg["mt5_setup_url"])
            .replace("{{AUTO_STOP_SECONDS}}", str(mins * 60))
            .replace("{{AUTO_STOP_MINUTES}}", str(mins)))


def cmd_create(cfg, args):
    ec2, ssm = ec2_client(cfg)
    state = load_state()
    if state.get("instance_id"):
        sys.exit(f"State already has instance {state['instance_id']}. Run 'terminate' first.")

    ami = resolve_windows_ami(ssm)
    print(f"Windows Server 2022 AMI: {ami}")
    ensure_key_pair(ec2, cfg)
    sg_id = ensure_security_group(ec2, cfg)
    pubkey = openssh_pubkey(load_private_key(cfg))

    print(f"Launching {cfg['instance_type']} ...")
    inst = ec2.run_instances(
        ImageId=ami,
        InstanceType=cfg["instance_type"],
        MinCount=1, MaxCount=1,
        KeyName=cfg["key_name"],
        SecurityGroupIds=[sg_id],
        UserData=build_user_data(cfg, pubkey),
        InstanceInitiatedShutdownBehavior="stop",
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1",
                              "Ebs": {"VolumeSize": int(cfg["volume_size_gb"]),
                                      "VolumeType": "gp3", "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "Project", "Value": cfg["project_tag"]},
                                     {"Key": "Name", "Value": cfg["instance_name"]}]}],
    )["Instances"][0]
    iid = inst["InstanceId"]
    print(f"Instance {iid} created; waiting for 'running' ...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])

    ip = current_public_ip(ec2, iid)
    state = {"instance_id": iid, "public_ip": ip, "app_port": cfg["app_port"],
             "key_file": pem_path(cfg)}
    save_state(state)

    print("\n" + "=" * 62)
    print(f"  Instance : {iid}")
    print(f"  Public IP: {ip}  (auto-assigned; changes on stop/start)")
    print(f"  RDP      : {ip}:{RDP_PORT}")
    print("=" * 62)
    print("\nWindows first boot + bootstrap takes ~6-10 min. Waiting for RDP ...")
    wait_for_port(ip, RDP_PORT, timeout=900)
    print("\nGet the Administrator password with:  python mt5_ec2.py password")
    print("Then check bootstrap progress:        python mt5_ec2.py status")


# ---------------- password ----------------

def cmd_password(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    data = ec2.get_password_data(InstanceId=iid)["PasswordData"].strip()
    if not data:
        sys.exit("Password not available yet (Windows takes ~4 min after first launch). Retry shortly.")
    pw = load_private_key(cfg).decrypt(base64.b64decode(data), padding.PKCS1v15()).decode()
    ip = current_public_ip(ec2, iid)
    print(f"Host     : {ip}:{RDP_PORT}")
    print("User     : Administrator")
    print(f"Password : {pw}")


# ---------------- tunnel ----------------

def cmd_tunnel(cfg, args):
    """Print the ssh -L command that exposes the box's loopback app on this PC.

    Two traps this deliberately avoids:
      * Use pem_path(cfg) (derived from HERE), NOT state["key_file"] - that field is an
        absolute path baked in at create time and goes stale if this folder ever moves.
      * If XauOrderPad also runs LOCALLY it already owns the app port, so `-L 8765:...`
        dies with "bind: Address already in use" - and every request then silently hits
        the LOCAL app instead of the box, which looks healthy and fools you. So bind a
        free local port and say so.
    """
    state = load_state()
    require_instance(state)
    ec2, _ = ec2_client(cfg)
    ip = current_public_ip(ec2, state["instance_id"])
    if not ip:
        sys.exit("No public IP - is the instance running? (run 'start')")
    remote_port = state.get("app_port", cfg["app_port"])

    local_port = remote_port
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", remote_port))
        except OSError:
            local_port = remote_port + 1   # 8765 taken (local app) -> 8766
            print(f"NOTE: 127.0.0.1:{remote_port} is already in use on this PC "
                  f"(a local XauOrderPad?), so forwarding to {local_port} instead.\n"
                  f"      Browsing {remote_port} would show you the LOCAL app, not the box.\n")

    print(f"Run this, leave it open, then browse to http://127.0.0.1:{local_port}\n")
    print(f'  ssh -i "{pem_path(cfg)}" -N -L {local_port}:127.0.0.1:{remote_port} Administrator@{ip}')


# ---------------- start / stop / status ----------------

def cmd_start(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ec2.start_instances(InstanceIds=[iid])
    print(f"Starting {iid} ... (fresh {cfg['auto_stop_minutes']}min auto-stop arms on boot)")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    home = refresh_firewall(ec2, cfg, quiet=True)
    ip = current_public_ip(ec2, iid)
    state["public_ip"] = ip
    save_state(state)
    print("\n" + "=" * 62)
    print(f"  NEW public IP : {ip}  (auto-assigned)")
    print(f"  RDP           : {ip}:{RDP_PORT}")
    print(f"  Firewall      : allows your current IP {home or '(unchanged)'}")
    print("=" * 62)
    wait_for_port(ip, RDP_PORT, timeout=300)
    print(f"\nBrowser UI: python mt5_ec2.py tunnel")


def cmd_stop(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ec2.stop_instances(InstanceIds=[iid])
    print(f"Stopping {iid}. (Disk kept; IP released - 'start' gives a NEW IP.)")


def cmd_status(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    d = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    st = d["State"]["Name"]
    ip = d.get("PublicIpAddress")
    print(f"Instance : {iid}")
    print(f"State    : {st}")
    print(f"Type     : {d['InstanceType']}")
    print(f"Public IP: {ip or '(none - stopped)'}")
    if st == "running" and d.get("LaunchTime"):
        mins = (time.time() - d["LaunchTime"].timestamp()) / 60
        left = cfg["auto_stop_minutes"] - mins
        print(f"Up for   : {mins:.0f} min  (auto-stop in ~{max(0, left):.0f} min)")
    if ip:
        for port, label in ((SSH_PORT, "SSH"), (RDP_PORT, "RDP")):
            try:
                with socket.create_connection((ip, port), timeout=4):
                    print(f"{label:9s}: reachable")
            except Exception:
                print(f"{label:9s}: closed (booting, or your home IP changed -> 'fixfw')")


def cmd_ip(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ip = current_public_ip(ec2, iid)
    if not ip:
        sys.exit("No public IP - is the instance running? (run 'start')")
    state["public_ip"] = ip
    save_state(state)
    print(ip)


# ---------------- auto-stop ----------------

def ssh_opts(cfg, known_hosts):
    # The public IP is reassigned on every start, so the host key never matches the last boot's.
    # Without these, ssh either prompts (hanging a non-interactive run) or refuses outright.
    #
    # known_hosts must be a real, throwaway path. Windows OpenSSH does NOT honour `nul`/`NUL` as
    # the null device - it takes it as a relative filename and writes a literal file called `nul`
    # into the cwd. That litters the repo with a name git cannot even index (reserved device
    # name), so `git add` fails outright. os.devnull is 'nul' on Windows: do not pass it here.
    return ["-i", pem_path(cfg),
            "-o", "StrictHostKeyChecking=no",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "LogLevel=ERROR"]


def cmd_autostop(cfg, args):
    """Re-arm the box's shutdown timer, for this session AND every future boot.

    config.json alone cannot do this: its value is baked into the ec2-autostop scheduled task
    at `create` time by user_data.ps1, which never runs again. Editing config.json without
    running this just makes `start`/`status` print a number the box does not honour.
    """
    mins = args.minutes
    if mins is None:
        sys.exit("usage: python mt5_ec2.py autostop <minutes>   e.g. autostop 60")
    if mins < 5:
        sys.exit(f"{mins} min is too short - you'd lose the box mid-setup.")
    if mins > AUTO_STOP_CEILING_MIN and not args.force:
        sys.exit(f"{mins} min exceeds the {AUTO_STOP_CEILING_MIN}-min ceiling that keeps this box "
                 f"inside the $200 free-tier credits (see README cost guard). Use --force to override.")

    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ip = current_public_ip(ec2, iid)
    if not ip:
        sys.exit("Instance is not running - 'start' it first.")

    with open(AUTOSTOP_PATH, encoding="utf-8") as f:
        script = f.read().replace("{{AUTO_STOP_SECONDS}}", str(mins * 60))
    # Keep both scratch files OUT of the repo dir - see ssh_opts on the `nul` trap.
    tmp = tempfile.mkdtemp(prefix="mt5_autostop_")
    local = os.path.join(tmp, "autostop.ps1")
    with open(local, "w", encoding="ascii", newline="\r\n") as f:
        f.write(script)
    opts = ssh_opts(cfg, os.path.join(tmp, "known_hosts"))

    # Ship it as a FILE. Inlining this over bash -> ssh -> PowerShell mangles the quoting in
    # -Argument "/s /t $secs" and the task silently never gets registered.
    try:
        target = f"Administrator@{ip}"
        r = subprocess.run(["scp", *opts, local, f"{target}:C:/autostop.ps1"],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            sys.exit(f"scp failed: {r.stderr.strip() or r.stdout.strip()}\n"
                     f"(SSH open? your home IP may have changed -> 'fixfw')")
        r = subprocess.run(["ssh", *opts, target,
                            "powershell -ExecutionPolicy Bypass -File C:\\autostop.ps1"],
                           capture_output=True, text=True, timeout=90)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        sys.exit(f"Re-arming failed: {(r.stderr or '').strip()}\n"
                 f"The box is still on its OLD timer ({cfg['auto_stop_minutes']} min).")

    # Only now is the number true, so only now do we record it.
    cfg["auto_stop_minutes"] = mins
    save_config(cfg)
    print(f"\nconfig.json updated -> auto_stop_minutes = {mins}")


# ---------------- ship the app + certs ----------------

APP_DIR = os.path.dirname(HERE)          # ...\XauOrderPad
REMOTE_APP = "C:/app/XauOrderPad"
REMOTE_CERTS = "C:/app/certs"

# What NOT to send. .venv is Windows-native but rebuilt on the box anyway; certs/ holds ca.key,
# which must NEVER leave this laptop (it mints trading identities).
SHIP_EXCLUDE_DIRS = {".venv", "__pycache__", "certs", "testing", "deploy", "audit", ".pytest_cache"}

# Files excluded by PATTERN, not by exact name. This used to be the single literal
# ".token.local", with a comment explaining that the LAN dev token has nothing to do with the
# box -- correct reasoning, but it only matched one filename. The moment multi-account added
# `.token.a1.local` / `.token.a2.local`, those fell straight through and were shipped: the
# laptop's live trading tokens ended up on an internet-facing box, verified by identical file
# hashes on both machines.
#
# Same failure for `instances.json`: it names LOCAL terminal paths (D:\mt5\...), which do not
# exist on the box, so shipping it overwrites the box's own config with the developer's -- the
# very mistake config.py's comment already records ("a deploy that clobbers the target's config
# with the developer's is not a deploy step, it is a regression generator"). The BOX's instance
# list is `deploy/instances.ec2.json`, installed separately below.
# accounts.md belongs at the repo ROOT (outside this walk) precisely so it cannot be shipped.
# Listed here anyway as belt and braces: if someone ever moves it under XauOrderPad/, it must
# not silently hand the box every laptop token.
SHIP_EXCLUDE_FILES = {"instances.json", "accounts.md"}
SHIP_EXCLUDE_GLOBS = (".token.*.local", ".token.local")

# The box's OWN instance registry, kept beside the box's certs and shipped INTO place as
# XauOrderPad/instances.json. Same separation as certs/ (box) vs certs-lan/ (laptop): two
# machines, two configs, and no path by which one can silently become the other.
EC2_INSTANCES = os.path.join(HERE, "instances.ec2.json")


def _ship_files():
    import fnmatch
    out = []
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in SHIP_EXCLUDE_DIRS and not d.startswith(".")]
        for f in files:
            if f in SHIP_EXCLUDE_FILES or f.endswith((".pyc", ".log")):
                continue
            if any(fnmatch.fnmatch(f, g) for g in SHIP_EXCLUDE_GLOBS):
                continue
            full = os.path.join(root, f)
            out.append((full, os.path.relpath(full, APP_DIR).replace("\\", "/")))
    return out


def cmd_ship(cfg, args):
    """Push the CURRENT source + the TLS material to the box, then rebuild the venv.

    Nothing in this repo ever did this. `create`/`user_data.ps1` install OpenSSH, Python, MT5 and
    the autostop task -- and then leave an EMPTY C:\\app. The app itself was hand-copied once, so
    the box drifts silently behind the repo: strategy.py and accounts.py are newer than it, and
    requirements.txt gained numpy (which used to arrive only as a transitive dep of MetaTrader5).
    """
    certs_dir = os.path.join(HERE, "certs")
    need = ["server.crt", "server.key", "ca.crt"]
    missing = [n for n in need if not os.path.exists(os.path.join(certs_dir, n))]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in {certs_dir}.\n"
                 f"Run:  python make_certs.py --ip <the elastic ip>")

    # Guard against shipping the WRONG server cert. There are now two: the box's (SAN = the public
    # Elastic IP, here in certs/) and the LAN one (SAN = a private 192.168.x.x, in certs-lan/, minted
    # by `make_certs.py --server-only`). Shipping the LAN cert would hand the box a certificate that
    # never verifies for its own public address -- an opaque handshake failure that looks like a
    # network fault. The two live in different dirs so this cannot happen by path, but this is the
    # belt to that braces: refuse if certs/server.crt's SAN is not a PUBLIC address.
    import ipaddress as _ip
    from cryptography import x509 as _x509
    _crt = _x509.load_pem_x509_certificate(open(os.path.join(certs_dir, "server.crt"), "rb").read())
    try:
        _sans = _crt.extensions.get_extension_for_class(_x509.SubjectAlternativeName).value
        _ips = [g.value for g in _sans if isinstance(g, _x509.IPAddress)]
    except _x509.ExtensionNotFound:
        _ips = []
    if not _ips:
        sys.exit(f"{certs_dir}\\server.crt has no IP-SAN. The phone dials the box by IP, so this cert "
                 f"cannot verify. Re-mint:  python make_certs.py --ip <the elastic ip>")
    if not any(_ip.ip_address(str(a)).is_global for a in _ips):
        sys.exit(f"REFUSING TO SHIP: {certs_dir}\\server.crt is bound to {[str(a) for a in _ips]}, a "
                 f"PRIVATE address -- that is the LAN cert, not the box's. The box needs the cert "
                 f"whose SAN is the Elastic IP. Re-mint:  python make_certs.py --ip <the elastic ip>")

    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ip = current_public_ip(ec2, iid)
    if not ip:
        sys.exit("Instance is not running - 'start' it first.")

    files = _ship_files()
    tmp = tempfile.mkdtemp(prefix="mt5_ship_")
    try:
        opts = ssh_opts(cfg, os.path.join(tmp, "known_hosts"))
        target = f"Administrator@{ip}"

        # tar the tree and ship ONE file: scp-ing ~40 files individually over a fresh SSH
        # handshake each is minutes of round-trips, and Windows OpenSSH has no rsync.
        import tarfile
        bundle = os.path.join(tmp, "app.tar")
        with tarfile.open(bundle, "w") as t:
            for full, rel in files:
                t.add(full, arcname=rel)
        print(f"Shipping {len(files)} files ...")
        r = subprocess.run(["scp", *opts, bundle, f"{target}:C:/app/app.tar"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            sys.exit(f"scp failed: {r.stderr.strip() or r.stdout.strip()}\n"
                     f"(SSH open? your home IP may have changed -> 'fixfw')")

        for n in need:
            r = subprocess.run(["scp", *opts, os.path.join(certs_dir, n),
                                f"{target}:C:/app/_cert_{n}"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                sys.exit(f"scp of {n} failed: {r.stderr.strip()}")

        # The BOX's instance registry, installed as XauOrderPad/instances.json by ship.ps1.
        # Sent separately because the laptop's own instances.json is excluded from the bundle:
        # it names D:\ paths that do not exist here, and shipping it would overwrite the box's
        # config with the developer's. Two machines, two registries, no path between them.
        if os.path.exists(EC2_INSTANCES):
            r = subprocess.run(["scp", *opts, EC2_INSTANCES,
                                f"{target}:C:/app/_instances.ec2.json"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                sys.exit(f"scp of instances.ec2.json failed: {r.stderr.strip()}")
            print("Shipping the box instance registry (instances.ec2.json)")

        print("Unpacking + rebuilding the venv on the box (this takes a minute) ...")
        _run_ps1_on_box(opts, target, SHIP_PS1_PATH, "ship.ps1", timeout=900)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nShipped. Next:  python mt5_ec2.py caddy")


def cmd_autologon(cfg, args):
    """Configure Windows autologon on the box + drop an MT5 Startup shortcut.

    Why: the `xauorderpad` task runs S4U/session-0, which has no interactive desktop -- and MT5 broker
    logins hang ~60s on an IPC timeout there. Booting into a real logged-in Administrator desktop makes
    logins instant, lets AutoTrading persist, and makes boot auto-login clean. Pair this with the
    Interactive/AtLogOn task change in ship.ps1 (re-run `ship` to pick it up).

    The Administrator password (decrypted from the EC2 key, same as `password`) is scp'd to a temp file
    on the box and deleted by autologon.ps1 after Sysinternals Autologon stores it as an ENCRYPTED LSA
    secret. It is never passed as an argv element.
    """
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ip = current_public_ip(ec2, iid)
    if not ip:
        sys.exit("Instance is not running - 'start' it first.")

    data = ec2.get_password_data(InstanceId=iid)["PasswordData"].strip()
    if not data:
        sys.exit("Password not available yet (Windows takes ~4 min after first launch). Retry shortly.")
    pw = load_private_key(cfg).decrypt(base64.b64decode(data), padding.PKCS1v15()).decode()

    tmp = tempfile.mkdtemp(prefix="mt5_autologon_")
    try:
        opts = ssh_opts(cfg, os.path.join(tmp, "known_hosts"))
        target = f"Administrator@{ip}"
        pwfile = os.path.join(tmp, "pw.txt")
        with open(pwfile, "w", newline="") as f:
            f.write(pw)
        r = subprocess.run(["scp", *opts, pwfile, f"{target}:C:/app/_autologon_pw.txt"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            sys.exit(f"scp of the password file failed: {r.stderr.strip()}\n"
                     f"(SSH open? your home IP may have changed -> 'fixfw')")
        _run_ps1_on_box(opts, target, AUTOLOGON_PS1_PATH, "autologon.ps1", timeout=300)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nAutologon set. Next:")
    print("  1. Make sure the interactive task is shipped:  python mt5_ec2.py ship")
    print("  2. Reboot the box (from RDP, or: ssh Administrator@<ip> shutdown /r /t 0)")
    print("  3. RDP in ONCE to enable AutoTrading + tick 'Save password' (both persist).")


def cmd_caddy(cfg, args):
    """Install/refresh the mTLS front door and (re)start it."""
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = require_instance(state)
    ip = current_public_ip(ec2, iid)
    if not ip:
        sys.exit("Instance is not running - 'start' it first.")
    if not state.get("elastic_ip"):
        print("WARNING: no Elastic IP in state.json. The server cert is bound to an ADDRESS; on an\n"
              "         auto-assigned IP it stops verifying after the next stop/start. Run 'eip'.")

    ensure_tls_ingress(ec2, cfg)

    # Regenerate the per-account routes from THIS laptop's copy of the box registry, so the
    # Caddyfile's `import C:/app/routes.caddy` can never resolve to a stale list -- a prefix
    # pointing at a port that now belongs to a different account is the worst failure here,
    # and it would look like a working proxy.
    routes = os.path.join(HERE, "routes.caddy")
    gen = os.path.join(HERE, "gen_routes.py")
    if os.path.exists(gen) and os.path.exists(EC2_INSTANCES):
        r = subprocess.run([sys.executable, gen, "--ec2"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"gen_routes.py --ec2 failed, refusing to ship possibly-stale routes:\n"
                     f"{r.stdout}{r.stderr}")
        print(r.stdout.strip())
    elif not os.path.exists(routes):
        # No multi-account config on this laptop: write an empty route file so the import
        # still resolves and only the catch-all applies. Keeps single-account deploys working.
        with open(routes, "w", encoding="ascii") as f:
            f.write("# No instances.ec2.json: single-account box, catch-all only.\n")

    tmp = tempfile.mkdtemp(prefix="mt5_caddy_")
    try:
        opts = ssh_opts(cfg, os.path.join(tmp, "known_hosts"))
        target = f"Administrator@{ip}"
        r = subprocess.run(["scp", *opts, CADDYFILE_PATH, f"{target}:C:/app/Caddyfile"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            sys.exit(f"scp of Caddyfile failed: {r.stderr.strip()}")
        # The Caddyfile imports this; ship them together or validate fails on the box.
        r = subprocess.run(["scp", *opts, routes, f"{target}:C:/app/routes.caddy"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            sys.exit(f"scp of routes.caddy failed: {r.stderr.strip()}")
        _run_ps1_on_box(opts, target, CADDY_PS1_PATH, "caddy_setup.ps1", timeout=600)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- talking to the deployed server, over mTLS ----------------

def _mtls_context(cfg, p12_password=None):
    """An SSL context that trusts ONLY our CA and presents the client identity.

    Same material the phone uses, so this exercises the real path rather than a privileged
    side door: if this cannot connect, neither can the app.
    """
    import getpass
    import ssl
    import atexit
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    certs = os.path.join(HERE, "certs")
    ca = os.path.join(certs, "ca.crt")
    p12 = os.path.join(certs, "client.p12")
    if not (os.path.exists(ca) and os.path.exists(p12)):
        sys.exit(f"No client certificate in {certs}. Run:  python make_certs.py --ip <elastic ip>")

    pw = p12_password or getpass.getpass("client.p12 password: ")
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(open(p12, "rb").read(), pw.encode())
    except Exception:
        sys.exit("Wrong client.p12 password.")

    # Python's ssl needs the identity as a FILE. Write it to a temp dir that is deleted on exit --
    # never into the repo, and never left behind: it is an unencrypted trading private key.
    tmp = tempfile.mkdtemp(prefix="mt5_mtls_")
    atexit.register(shutil.rmtree, tmp, True)
    pem = os.path.join(tmp, "client.pem")
    with open(pem, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    ctx = ssl.create_default_context(cafile=ca)
    ctx.load_cert_chain(pem)
    return ctx


def _api(cfg, ctx, path, method="GET", body=None, token=None, timeout=30):
    import urllib.error
    import urllib.request

    state = load_state()
    eip = state.get("elastic_ip") or state.get("public_ip")
    if not eip:
        sys.exit("No address in state.json. Run 'eip'.")

    url = f"https://{eip}:{TLS_PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("x-token", token)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def _box_token(cfg):
    """Read the API token off the box. It is machine env there, never in this repo."""
    ec2, _ = ec2_client(cfg)
    state = load_state()
    ip = current_public_ip(ec2, require_instance(state))
    if not ip:
        sys.exit("Instance is not running - 'start' it first.")
    tmp = tempfile.mkdtemp(prefix="mt5_tok_")
    try:
        opts = ssh_opts(cfg, os.path.join(tmp, "known_hosts"))
        r = subprocess.run(
            ["ssh", *opts, f"Administrator@{ip}",
             'powershell -Command "[Environment]::GetEnvironmentVariable(\'XAUORDERPAD_TOKEN\',\'Machine\')"'],
            capture_output=True, text=True, timeout=90)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tok = (r.stdout or "").strip()
    if not tok:
        sys.exit("No XAUORDERPAD_TOKEN on the box. Run 'ship'.")
    return tok


def cmd_health(cfg, args):
    """Is the deployed server ACTUALLY working? Not 'does it return 200'.

    /api/state answers HTTP 200 even when MT5 is completely disconnected -- the JSON comes back,
    just with no prices. So a 200 proves the web stack is alive and NOTHING about the broker. I
    shipped this box and reported it healthy on the strength of a 200; it was logged out and
    serving no prices at all. This command exists so that cannot happen twice.

    The only honest liveness test for a trading server is: call it twice, and the PRICE MUST MOVE.
    A present-but-frozen bid means the terminal is attached and the feed is dead, which looks
    identical to healthy in any single snapshot.
    """
    ctx = _mtls_context(cfg, args.p12_password)
    token = _box_token(cfg)
    fails = []

    def check(ok, label, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -- ' + detail) if detail else ''}")
        if not ok:
            fails.append(label)

    st, cfg_resp = _api(cfg, ctx, "/api/config")
    check(st == 200, "TLS + client certificate accepted", f"HTTP {st}")
    check(bool(cfg_resp and cfg_resp.get("auth_required")), "server requires a token")

    st, _ = _api(cfg, ctx, "/api/state", token="definitely-not-the-token")
    check(st == 401, "a WRONG token is rejected behind the cert gate", f"HTTP {st}")

    st, a = _api(cfg, ctx, "/api/state", token=token)
    check(st == 200, "authenticated /api/state", f"HTTP {st}")
    a = a or {}
    check(a.get("logged_out") is not True, "MT5 is logged in",
          a.get("error") or "logged_out=true -> run 'login'")
    check(a.get("connected") is True, "worker connected to the terminal")
    check(a.get("healthy") is True, "worker reports healthy")

    time.sleep(3)
    st, b = _api(cfg, ctx, "/api/state", token=token)
    b = b or {}
    bid_a, bid_b = a.get("bid"), b.get("bid")
    if bid_a is None:
        check(False, "prices are flowing", "bid is null -- no feed at all")
    else:
        check(bid_a != bid_b, "PRICE MOVED between two polls (the only honest liveness test)",
              f"{bid_a} -> {bid_b}" + ("  FROZEN: terminal attached, feed dead" if bid_a == bid_b else ""))

    acct = a.get("account") or {}
    check(acct.get("is_demo") is True, "account is a DEMO account",
          f"login={acct.get('login')} server={acct.get('server')}"
          if acct else "no account in the snapshot")

    # The invariant the whole design rests on: uvicorn must NOT be reachable from the internet.
    state = load_state()
    eip = state.get("elastic_ip") or state.get("public_ip")
    s = socket.socket()
    s.settimeout(6)
    try:
        s.connect((eip, int(cfg["app_port"])))
        check(False, f"port {cfg['app_port']} is closed to the internet", "*** OPEN -- uvicorn is exposed ***")
    except Exception:
        check(True, f"port {cfg['app_port']} is closed to the internet")
    finally:
        s.close()

    print()
    if fails:
        print(f"  UNHEALTHY -- {len(fails)} check(s) failed: {', '.join(fails)}")
        sys.exit(1)
    print(f"  HEALTHY. phone -> mTLS -> FastAPI -> MT5 -> broker, prices moving "
          f"({acct.get('server')}, demo).")


def cmd_login(cfg, args):
    """Log the box's MT5 into an account, over the same mTLS channel the phone uses.

    POST /api/login calls mt5.initialize(path=..., login=..., password=..., server=...), which
    LAUNCHES terminal64.exe if it is not running and logs it in, in one step. Nothing is stored
    on the box.

    The password is read with getpass: never echoed, never written to a file, and never passed as
    an argv element (argv is readable by other processes on the machine).
    """
    import getpass

    login = args.login or input("MT5 login (account number): ").strip()
    server = args.server or input("MT5 server (e.g. Exness-MT5Trial16): ").strip()
    if not login or not server:
        sys.exit("login and server are both required.")
    password = getpass.getpass("MT5 password (not echoed): ")
    if not password:
        sys.exit("A password is required.")

    ctx = _mtls_context(cfg, args.p12_password)
    token = _box_token(cfg)

    print("\nLogging in (this launches terminal64.exe on the box; it can take ~30s) ...")
    st, resp = _api(cfg, ctx, "/api/login", method="POST", token=token, timeout=120,
                    body={"login": int(login), "password": password, "server": server})
    del password

    if st != 200 or not (resp or {}).get("ok", True):
        print(f"\n  LOGIN FAILED (HTTP {st}): {resp}")
        print("\n  If this is an IPC timeout, MT5 may be unable to complete a broker login while")
        print("  running in session 0 (launched by a Scheduled Task rather than an RDP desktop).")
        print("  Fallback: 'password' -> RDP in -> log MT5 in with 'Save password' ticked, which")
        print("  writes accounts.dat so future headless launches auto-login.")
        sys.exit(1)

    print(f"  {resp}")
    print("\nNow prove it actually feeds prices:  python mt5_ec2.py health")


# ---------------- helpers for the on-box scripts ----------------

def _run_ps1_on_box(opts, target, local_path, remote_name, ps_args="", timeout=600):
    """scp a .ps1 to the box and run it. Ships a FILE, never an inline command.

    Inlining PowerShell through bash -> ssh -> PowerShell mangles the quoting; cmd_autostop
    hit exactly this and the scheduled task silently never got registered.
    """
    r = subprocess.run(["scp", *opts, local_path, f"{target}:C:/{remote_name}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit(f"scp of {remote_name} failed: {r.stderr.strip() or r.stdout.strip()}\n"
                 f"(SSH open? your home IP may have changed -> 'fixfw')")

    r = subprocess.run(
        ["ssh", *opts, target,
         f"powershell -ExecutionPolicy Bypass -File C:\\{remote_name} {ps_args}"],
        capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        sys.exit(f"\n{remote_name} FAILED:\n{(r.stderr or '').strip()}")
    return out


# ---------------- firewall ----------------

def refresh_firewall(ec2, cfg, quiet=False):
    """Re-point 22 + 3389 at the CURRENT home public IP (it's dynamic)."""
    myip = my_public_ip()
    if not myip:
        if not quiet:
            print("Could not detect your public IP; leaving firewall unchanged.")
        return None
    cidr = f"{myip}/32"
    sgs = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [f"{cfg['project_tag']}-sg"]}])["SecurityGroups"]
    if not sgs:
        if not quiet:
            print("Security group not found.")
        return None
    sg_id = sgs[0]["GroupId"]
    ports = {SSH_PORT, RDP_PORT}
    for perm in sgs[0].get("IpPermissions", []):
        if perm.get("IpProtocol") == "tcp" and perm.get("FromPort") in ports:
            for rng in perm.get("IpRanges", []):
                old = rng["CidrIp"]
                if old != cidr:
                    try:
                        ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=[{
                            "IpProtocol": "tcp", "FromPort": perm["FromPort"],
                            "ToPort": perm["ToPort"], "IpRanges": [{"CidrIp": old}]}])
                        if not quiet:
                            print(f"port {perm['FromPort']}: removed stale {old}")
                    except ClientError as e:
                        print(f"revoke {old}: {e.response['Error']['Code']}")
    for p in sorted(ports):
        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": p, "ToPort": p,
                "IpRanges": [{"CidrIp": cidr, "Description": "home"}]}])
            if not quiet:
                print(f"port {p}: allow {cidr}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
    return myip


def cmd_fixfw(cfg, args):
    ec2, _ = ec2_client(cfg)
    ip = refresh_firewall(ec2, cfg)
    if not ip:
        sys.exit("Could not update firewall.")
    print(f"Firewall now points to your current home IP: {ip}")


# ---------------- terminate ----------------

def cmd_terminate(cfg, args):
    ec2, _ = ec2_client(cfg)
    state = load_state()
    iid = state.get("instance_id")
    if not iid:
        print("Nothing to terminate (no state).")
        return
    if not args.yes:
        print(f"This DELETES instance {iid}, its 30GB disk, the key pair, the security group")
        print("and RELEASES the Elastic IP -- so the server certificate, which is bound to that")
        print("address, becomes worthless and every client must be re-issued.")
        if input("Type 'yes' to confirm: ").strip().lower() != "yes":
            print("Aborted.")
            return
    ec2.terminate_instances(InstanceIds=[iid])
    print(f"Terminating {iid} ... waiting.")
    ec2.get_waiter("instance_terminated").wait(InstanceIds=[iid])

    # Release the Elastic IP. An orphaned EIP is billed FOREVER (~$3.65/mo) with no instance to
    # show for it, and nothing else in this teardown would ever reclaim it. Do this AFTER the
    # instance is gone: releasing an associated address fails.
    alloc_id = state.get("eip_alloc_id")
    if alloc_id:
        try:
            ec2.release_address(AllocationId=alloc_id)
            print(f"Released Elastic IP {state.get('elastic_ip', alloc_id)}.")
        except ClientError as e:
            print(f"EIP cleanup FAILED ({e.response['Error']['Code']}). "
                  f"Release {alloc_id} by hand or it bills forever.")
    try:
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [f"{cfg['project_tag']}-sg"]}])["SecurityGroups"]
        if sgs:
            ec2.delete_security_group(GroupId=sgs[0]["GroupId"])
            print("Deleted security group.")
    except ClientError as e:
        print(f"SG cleanup: {e.response['Error']['Code']}")
    try:
        ec2.delete_key_pair(KeyName=cfg["key_name"])
        p = pem_path(cfg)
        if os.path.exists(p):
            os.remove(p)
        print("Deleted key pair.")
    except ClientError as e:
        print(f"Key cleanup: {e.response['Error']['Code']}")
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print("Done. All resources cleaned up.")


COMMANDS = {"create": cmd_create, "status": cmd_status, "start": cmd_start, "stop": cmd_stop,
            "password": cmd_password, "tunnel": cmd_tunnel, "ip": cmd_ip,
            "fixfw": cmd_fixfw, "autostop": cmd_autostop,
            "eip": cmd_eip, "ship": cmd_ship, "caddy": cmd_caddy,
            "autologon": cmd_autologon,
            "login": cmd_login, "health": cmd_health,
            "terminate": cmd_terminate}


def main():
    p = argparse.ArgumentParser(description="MT5 on Windows EC2")
    p.add_argument("command", choices=COMMANDS.keys())
    p.add_argument("minutes", nargs="?", type=int, help="autostop: minutes until the box stops")
    p.add_argument("--yes", action="store_true", help="Skip confirmation on terminate")
    p.add_argument("--force", action="store_true",
                   help=f"autostop: allow > {AUTO_STOP_CEILING_MIN} min (overruns the credits)")
    p.add_argument("--token", help="API token. Omit to have a strong one generated on the box.")
    p.add_argument("--login", help="login: MT5 account number")
    p.add_argument("--server", help="login: MT5 broker server, e.g. Exness-MT5Trial16")
    # NOT --mt5-password. A password in argv is readable by every other process on the machine
    # and lands in your shell history; cmd_login reads it with getpass instead.
    p.add_argument("--p12-password", help="client.p12 password (omit to be prompted)")
    args = p.parse_args()
    cfg = load_config()
    try:
        COMMANDS[args.command](cfg, args)
    except NoCredentialsError:
        sys.exit("No AWS credentials. Run 'aws configure' first.")
    except ClientError as e:
        sys.exit(f"AWS error: {e.response['Error']['Code']}: {e.response['Error']['Message']}")


if __name__ == "__main__":
    main()
