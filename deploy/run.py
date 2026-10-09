"""One worker is intentional: SQLite and analytical jobs share this process."""
import os
from pathlib import Path
import tempfile
import uvicorn
from app.core.deployment import deployment_settings, trusted_proxy_ips


def prepare_temp_storage():
    """Multipart files spool before the route can stage/validate the upload."""
    configured = os.getenv("TMPDIR")
    if configured:
        directory = Path(configured)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tempfile.tempdir = None
        if Path(tempfile.gettempdir()).resolve() != directory.resolve():
            raise RuntimeError("Configured upload temporary directory is not writable")


if __name__ == "__main__":
    prepare_temp_storage()
    deployment_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1,
                proxy_headers=True, forwarded_allow_ips=trusted_proxy_ips())
