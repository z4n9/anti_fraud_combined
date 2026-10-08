"""Check the live database connection without depending on curl."""
import json
from urllib.request import urlopen


if __name__ == "__main__":
    with urlopen("http://127.0.0.1:8000/api/readiness", timeout=4) as response:
        data = json.load(response)
        if response.status != 200 or data.get("status") != "ok" or data.get("bank") != "ready" or data.get("analyst") != "ready":
            raise SystemExit(1)
