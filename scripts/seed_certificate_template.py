"""
seed_certificate_template.py
============================
Generate the default award-certificate background template and upload it to the
`certificate-templates` blob container as `default_certificate.png`.

The template is a *decorative frame only* (cream background + gold double
border + corner accents). All dynamic text — beneficiary name, award amount,
date, signatory — is drawn on top at generation time by
backend/utils/certificate.py, so this image carries no fonts or wording and is
safe to swap per tenant.

Usage
-----
  # Requires AZURE_STORAGE_ACCOUNT + AZURE_STORAGE_KEY in the environment (.env).
  python scripts/seed_certificate_template.py

  # Verbose: also dump the full Azure SDK HTTP request/response.
  python scripts/seed_certificate_template.py --verbose

  # Just write the PNG locally without uploading (preview / CI):
  python scripts/seed_certificate_template.py --out default_certificate.png --no-upload

Note
----
The app storage account is firewalled (default-deny + private endpoint). To
upload from your machine, your current public IP must be on the account's
allowed list, e.g.:

  az storage account network-rule add -g <resource-group> --account-name <account> --ip-address <your-public-ip>

Alternatively run with --no-upload to produce the PNG locally and upload it from
a host inside the VNet (or via the portal from an allowed network).
"""

import argparse
import io
import logging
import os
import socket
import sys
import time

from dotenv import find_dotenv, load_dotenv

_DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(_DOTENV_PATH)

from PIL import Image, ImageDraw

# Landscape A4 at ~150 DPI
_W, _H = 1754, 1240

_CREAM      = (252, 250, 244)
_GOLD       = (197, 160, 71)
_GOLD_LIGHT = (220, 193, 130)
_TEAL       = (31, 99, 94)


def build_template_png() -> bytes:
    img = Image.new("RGB", (_W, _H), _CREAM)
    d = ImageDraw.Draw(img)

    # Outer gold border
    d.rectangle([40, 40, _W - 40, _H - 40], outline=_GOLD, width=10)
    # Thin teal accent
    d.rectangle([62, 62, _W - 62, _H - 62], outline=_TEAL, width=2)
    # Inner light-gold border
    d.rectangle([80, 80, _W - 80, _H - 80], outline=_GOLD_LIGHT, width=4)

    # Corner flourishes (simple right-angle accents)
    c = 130
    for (cx, cy, dx, dy) in [
        (96, 96, 1, 1), (_W - 96, 96, -1, 1),
        (96, _H - 96, 1, -1), (_W - 96, _H - 96, -1, -1),
    ]:
        d.line([(cx, cy), (cx + dx * c, cy)], fill=_GOLD, width=6)
        d.line([(cx, cy), (cx, cy + dy * c)], fill=_GOLD, width=6)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _mask(secret: str) -> str:
    if not secret:
        return "MISSING"
    return f"set ({len(secret)} chars, ends ...{secret[-4:]})"


def _is_private(ip: str) -> bool:
    if ip.startswith("10.") or ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def _diagnostics(account: str, key: str, container: str, blob_name: str) -> None:
    host = f"{account}.blob.core.windows.net"
    print("── seed diagnostics ─────────────────────────────────────────")
    print(f"  .env loaded from        : {_DOTENV_PATH or '(none found — using process env)'}")
    print(f"  AZURE_STORAGE_ACCOUNT   : {account}")
    print(f"  endpoint                : https://{host}")
    print(f"  container               : {container}")
    print(f"  blob name               : {blob_name}")
    print(f"  AZURE_STORAGE_KEY       : {_mask(key)}")

    # DNS — the most useful signal. A private (10/172.16-31/192.168) answer means
    # the name points at the private endpoint and is unreachable from outside the VNet.
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        print(f"  DNS {host} -> {', '.join(ips)}")
        priv = [ip for ip in ips if _is_private(ip)]
        if priv:
            print(f"  ! resolves to PRIVATE IP {priv} — this name points at the private")
            print(f"    endpoint. From outside the VNet the TCP connect will hang/fail even")
            print(f"    with your public IP whitelisted. Seed from inside the VNet, or use")
            print(f"    --no-upload + portal upload.")
    except socket.gaierror as e:
        print(f"  DNS {host} -> FAILED ({e})")

    # Raw TCP probe: connected => reachable (any later 403 is auth/firewall at HTTP
    # layer); failed/timeout => network/DNS/firewall problem.
    t = time.time()
    try:
        with socket.create_connection((host, 443), timeout=8):
            print(f"  TCP {host}:443 -> connected in {time.time() - t:.2f}s")
    except Exception as e:
        print(f"  TCP {host}:443 -> FAILED after {time.time() - t:.2f}s ({e.__class__.__name__}: {e})")
    print("─────────────────────────────────────────────────────────────")


def _upload(png: bytes, account: str, key: str, container: str, blob_name: str, verbose: bool) -> None:
    from azure.core.exceptions import HttpResponseError, ServiceRequestError
    from azure.storage.blob import BlobServiceClient, ContentSettings

    conn = (
        f"DefaultEndpointsProtocol=https;AccountName={account};"
        f"AccountKey={key};EndpointSuffix=core.windows.net"
    )
    endpoint = f"https://{account}.blob.core.windows.net"
    cclient = BlobServiceClient.from_connection_string(
        conn, logging_enable=verbose
    ).get_container_client(container)

    try:
        print(f"-> checking container '{container}' on {account} ...")
        if not cclient.exists(logging_enable=verbose):
            print(f"-> container '{container}' missing; creating it ...")
            cclient.create_container(logging_enable=verbose)
        print(f"-> uploading '{blob_name}' ({len(png):,} bytes) ...")
        cclient.get_blob_client(blob_name).upload_blob(
            png, overwrite=True, logging_enable=verbose,
            content_settings=ContentSettings(content_type="image/png"),
        )
    except ServiceRequestError as e:
        _die(
            f"Could not reach {endpoint} ({e.__class__.__name__}: {e}).\n"
            "This is a connectivity problem, not an auth one. Most likely the storage\n"
            "account is firewalled (default-deny + private endpoint) and your machine\n"
            "is not on an allowed network, or DNS resolves the name to a private IP you\n"
            "cannot reach. See the DNS/TCP lines above. Run from inside the VNet, or\n"
            "use --no-upload."
        )
    except HttpResponseError as e:
        code = str(getattr(e, "error_code", None) or "")
        if "AuthenticationFailed" in code:
            _die(
                f"{endpoint} rejected the credentials (HTTP {e.status_code}, AuthenticationFailed).\n"
                "NOT a firewall problem — the request reached the service, but the Shared Key\n"
                "signature is invalid. Almost always a wrong / rotated / mismatched\n"
                "AZURE_STORAGE_KEY. Checks:\n"
                f"  - Is it the CURRENT key for THIS account ({account}) — not dev/prod or a\n"
                "    rotated value?\n"
                f"      az storage account keys list -g <rg> --account-name {account} --query \"[0].value\" -o tsv\n"
                "  - In .env the value must be ONE line, unquoted, no trailing space/newline.\n"
                "  - AZURE_STORAGE_ACCOUNT must match the account the key belongs to."
            )
        if "AuthorizationFailure" in code or e.status_code == 403:
            _die(
                f"{endpoint} refused the request (HTTP {e.status_code}, {code or 'AuthorizationFailure'}).\n"
                "TCP connected but the network ACL denied the request. Confirm your *public*\n"
                "IP is in my_ips and the rule is applied (terraform apply / az network-rule add)."
            )
        _die(f"{endpoint} returned HTTP {e.status_code} ({code}): {e.message}")

    print(f"OK: uploaded {blob_name} -> {container} ({len(png):,} bytes) on {account}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the default certificate template.")
    parser.add_argument("--out", default=None, help="Also write the PNG to this local path.")
    parser.add_argument("--no-upload", action="store_true", help="Skip the blob upload.")
    parser.add_argument("--verbose", action="store_true", help="Enable full Azure SDK HTTP logging.")
    parser.add_argument("--container", default=os.getenv("CERT_TEMPLATES_CONTAINER", "certificate-templates"))
    parser.add_argument("--blob-name", default="default_certificate.png")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.DEBUG)

    png = build_template_png()

    if args.out:
        with open(args.out, "wb") as f:
            f.write(png)
        print(f"Wrote template to {args.out} ({len(png):,} bytes)")

    if args.no_upload:
        return

    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    key     = os.getenv("AZURE_STORAGE_KEY")

    _diagnostics(account or "(unset)", key or "", args.container, args.blob_name)

    if not account or not key:
        _die(
            "AZURE_STORAGE_ACCOUNT and AZURE_STORAGE_KEY must be set (in the environment\n"
            "or a .env file). Use --no-upload to only generate the PNG locally."
        )

    _upload(png, account, key, args.container, args.blob_name, args.verbose)


if __name__ == "__main__":
    main()
