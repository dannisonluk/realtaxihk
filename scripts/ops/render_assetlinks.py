"""Generate Android `assetlinks.json` for password-reset App Links.

The file is printed to stdout, never written or logged with the cert
fingerprint treated as a secret. The repository deliberately leaves the
fingerprint out: it depends on the APK signing artifact, which is not available
until deployment.

Usage:
    PACKAGE_NAME=com.hkfastdc.mobile \
    SHA256_CERT_FINGERPRINT=AA:BB:... .venv/Scripts/python.exe \
        scripts/ops/render_assetlinks.py --site-host hkfastdc.com
    scripts/ops/render_assetlinks.py --validate
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_DEFAULT_HOST = "hkfastdc.com"


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-name",
        default=os.environ.get("PACKAGE_NAME", ""),
        help="Android package name (env: PACKAGE_NAME)",
    )
    parser.add_argument(
        "--sha256-cert-fingerprint",
        default=os.environ.get("SHA256_CERT_FINGERPRINT", ""),
        help="APK signing SHA-256 fingerprint, colon-separated (env: SHA256_CERT_FINGERPRINT)",
    )
    parser.add_argument(
        "--site-host",
        default=os.environ.get("SITE_HOST", _DEFAULT_HOST),
        help="Host serving assetlinks.json (env: SITE_HOST, default: %(default)s)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Only check that required values are present; print nothing else",
    )
    return parser


def _assetlinks(package_name: str, fingerprint: str, host: str) -> list[dict[str, object]]:
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package_name,
                "sha256_cert_fingerprints": [fingerprint],
            },
        }
    ]


def main() -> int:
    args = _arg_parser().parse_args()
    missing = [
        name
        for name, value in (
            ("PACKAGE_NAME", args.package_name),
            ("SHA256_CERT_FINGERPRINT", args.sha256_cert_fingerprint),
        )
        if not value
    ]
    if missing:
        print(f"missing required value(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.validate:
        print("assetlinks configuration is present")
        return 0
    print(
        json.dumps(
            _assetlinks(args.package_name, args.sha256_cert_fingerprint, args.site_host),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
