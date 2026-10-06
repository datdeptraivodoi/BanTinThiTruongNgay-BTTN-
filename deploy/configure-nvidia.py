"""Run interactively as root on the VPS; key entry is hidden and never logged."""
import getpass
import os
import sys
import tempfile
from pathlib import Path


def main():
    sys.stdin.reconfigure(encoding="utf-8", errors="surrogateescape")
    path = Path("/etc/bttn/bttn.env")
    metadata = path.stat()
    lines = path.read_text(encoding="utf-8").splitlines()
    key = getpass.getpass("NVIDIA API key (hidden; Enter keeps the existing value): ").strip()
    if not key:
        print("No change. No email sent.")
        return
    if not key.startswith("nvapi-") or not key.isascii() or any(not (c.isalnum() or c in "-_") for c in key):
        raise ValueError("Invalid NVIDIA key format; use the English keyboard")
    lines = [line for line in lines if not line.startswith("NVIDIA_API_KEY=")]
    lines.append('NVIDIA_API_KEY="' + key + '"')
    handle, temporary = tempfile.mkstemp(prefix=".bttn-env-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("NVIDIA key saved securely. No email sent and no publication scheduled.")


if __name__ == "__main__":
    main()
