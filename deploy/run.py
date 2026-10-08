"""One worker is intentional: SQLite and analytical jobs share this process."""
import uvicorn
from app.core.deployment import deployment_settings, trusted_proxy_ips


if __name__ == "__main__":
    deployment_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1,
                proxy_headers=True, forwarded_allow_ips=trusted_proxy_ips())
