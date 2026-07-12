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

RDP_PORT = 3389
SSH_PORT = 22

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
        print(f"This DELETES instance {iid}, its 30GB disk, the key pair and the security group.")
        if input("Type 'yes' to confirm: ").strip().lower() != "yes":
            print("Aborted.")
            return
    ec2.terminate_instances(InstanceIds=[iid])
    print(f"Terminating {iid} ... waiting.")
    ec2.get_waiter("instance_terminated").wait(InstanceIds=[iid])
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
            "fixfw": cmd_fixfw, "autostop": cmd_autostop, "terminate": cmd_terminate}


def main():
    p = argparse.ArgumentParser(description="MT5 on Windows EC2")
    p.add_argument("command", choices=COMMANDS.keys())
    p.add_argument("minutes", nargs="?", type=int, help="autostop: minutes until the box stops")
    p.add_argument("--yes", action="store_true", help="Skip confirmation on terminate")
    p.add_argument("--force", action="store_true",
                   help=f"autostop: allow > {AUTO_STOP_CEILING_MIN} min (overruns the credits)")
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
