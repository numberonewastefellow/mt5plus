"""Mint the mTLS material for the public EC2 deployment.

    python make_certs.py --ip 51.24.89.34

Produces, in deploy/certs/ (gitignored):

    ca.crt      the private CA. PUBLIC. Goes to the box AND the phone.
    ca.key      *** THE CROWN JEWEL. Laptop only. Never the box, never the repo. ***
    server.crt  the box's identity, SAN = IP:<elastic-ip>
    server.key  the box's private key -> the box
    client.p12  the phone's identity, password-protected -> the phone

WHY A PRIVATE CA AND NOT LET'S ENCRYPT
--------------------------------------
Because the Android app is configured to trust THIS CA AND NOTHING ELSE, no public CA can
mis-issue a certificate for this service -- not a compromised one, not a coerced one. That is
strictly stronger than a public cert. The cost is that we must distribute ca.crt ourselves,
which is exactly what the app's "upload certificate" screen is for.

WHY THE SAN MUST BE AN IP, NOT A NAME
-------------------------------------
The app connects to https://<elastic-ip>:8443 -- an IP literal. Java/OkHttp then verifies the
address against the certificate's `iPAddress` SAN entries. A cert carrying only a CN, or only a
DNS SAN, FAILS hostname verification even though it is otherwise perfectly valid, and it fails as
an opaque handshake error that looks like a network fault. So: IPAddress SAN, always.

That is also why this whole design needs an ELASTIC IP. The auto-assigned public IP changes on
every stop/start, and the certificate is bound to the address.

WHY A CLIENT CERTIFICATE AT ALL
-------------------------------
The API token is a BEARER secret: whoever holds it can trade. The client cert's private key never
leaves the phone, and the server rejects a connection without one DURING THE TLS HANDSHAKE -- so
an internet scanner that finds the open port never reaches FastAPI, never reaches the token check,
and never touches any MT5 code. The token stays as a second factor; it is cheap and it can be
rotated without reissuing certificates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import secrets
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit("cryptography is not installed. Run:  python -m pip install --user cryptography")

HERE = os.path.dirname(os.path.abspath(__file__))
CERTS = os.path.join(HERE, "certs")

CA_DAYS = 3650      # 10y. Rotating the CA means re-uploading to the phone, so make it rare.
# ~90 days, NOT 825. There is no CRL/OCSP here, so a leaf cert cannot be revoked -- the ONLY way
# to invalidate a lost phone's trading identity is to rotate the CA (make_certs --force) and
# re-issue. At 825 days a stolen phone holds a valid client cert for 27 months; at 90 the exposure
# is bounded and rotation is cheap (re-run make_certs, ship, re-upload the new client.p12). The
# server cert is on the same clock, which is fine -- ship re-installs it in the same step.
LEAF_DAYS = 90

# P-256 rather than RSA-2048: markedly faster handshakes on a phone, and the handshake is the
# only TLS cost that matters here (the WebSocket is one long-lived connection).
def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _name(cn: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "XauOrderPad"),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


def _write(path: str, data: bytes, secret: bool = False) -> None:
    with open(path, "wb") as f:
        f.write(data)
    if secret and os.name == "nt":
        # Strip inherited ACLs and grant only the current user. Without this the key is readable
        # by every account on the machine, which quietly undoes the point of having one.
        #
        # (F), not (R,W): (R,W) omits the DELETE right, so the file cannot be removed or replaced
        # -- which breaks the ONE operation a key file must always support, rotation. Locking the
        # key down so hard you cannot revoke it is not security.
        os.system(f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%:(F)" >nul 2>&1')


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the mTLS CA, server and client certs.")
    ap.add_argument("--ip", required=True,
                    help="the box's ELASTIC IP (must be stable; a cert is bound to it)")
    ap.add_argument("--p12-password",
                    help="password for client.p12. Omit to generate and print one.")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing CA. This INVALIDATES every issued client.")
    args = ap.parse_args()

    try:
        ip = ipaddress.ip_address(args.ip)
    except ValueError:
        sys.exit(f"--ip {args.ip!r} is not a valid IP address.")
    if not ip.is_global:
        print(f"WARNING: {ip} is not a public address. If this is not the box's Elastic IP, "
              f"the phone will not be able to reach it.", file=sys.stderr)

    os.makedirs(CERTS, exist_ok=True)
    ca_crt_p = os.path.join(CERTS, "ca.crt")
    ca_key_p = os.path.join(CERTS, "ca.key")

    if os.path.exists(ca_key_p) and not args.force:
        # Silently regenerating the CA would invalidate the client identity already on the phone,
        # and the failure would look like a network problem, not a cert problem.
        sys.exit(f"A CA already exists at {ca_key_p}.\n"
                 f"Re-run with --force ONLY if you accept that every client.p12 already issued "
                 f"(i.e. the one on your phone) stops working and must be re-uploaded.")

    now = dt.datetime.now(dt.timezone.utc)

    # ---- CA -----------------------------------------------------------------
    ca_key = _key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("XauOrderPad Private CA"))
        .issuer_name(_name("XauOrderPad Private CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))   # tolerate a little clock skew
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    _write(ca_crt_p, _pem(ca))
    _write(ca_key_p, _key_pem(ca_key), secret=True)

    # ---- server: SAN = IP -----------------------------------------------------
    srv_key = _key()
    srv = (
        x509.CertificateBuilder()
        .subject_name(_name(str(ip)))
        .issuer_name(ca.subject)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # THE line that makes an IP-literal URL verify. Without it: opaque handshake failure.
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write(os.path.join(CERTS, "server.crt"), _pem(srv))
    _write(os.path.join(CERTS, "server.key"), _key_pem(srv_key), secret=True)

    # ---- client: the phone's identity -----------------------------------------
    pw = args.p12_password or secrets.token_urlsafe(12)
    cli_key = _key()
    cli = (
        x509.CertificateBuilder()
        .subject_name(_name("xauorderpad-phone"))
        .issuer_name(ca.subject)
        .public_key(cli_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # CLIENT_AUTH, not SERVER_AUTH. Caddy verifies this EKU; a server cert will be rejected.
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    p12 = pkcs12.serialize_key_and_certificates(
        name=b"xauorderpad-phone",
        key=cli_key,
        cert=cli,
        cas=[ca],
        encryption_algorithm=serialization.BestAvailableEncryption(pw.encode()),
    )
    _write(os.path.join(CERTS, "client.p12"), p12, secret=True)

    print(f"""
  Wrote {CERTS}

    ca.crt      -> the box AND the phone   (public; safe to copy around)
    ca.key      -> STAYS ON THIS LAPTOP    *** it can mint new trading identities ***
    server.crt  -> the box
    server.key  -> the box                 (private)
    client.p12  -> the phone               (private; it IS a trading identity)

  client.p12 password:  {pw}

  Server certificate is bound to IP {ip}. If the box's address ever changes, this cert
  stops verifying and the app fails with an opaque handshake error -- which is why the
  deploy uses an Elastic IP. Do not go back to the auto-assigned one.

  Next:  python mt5_ec2.py ship        (source + certs -> the box)
         python mt5_ec2.py caddy       (start the mTLS front door)
""")


if __name__ == "__main__":
    main()
