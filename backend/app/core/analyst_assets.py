"""Only built public assets are served; an absent dashboard does not stop the bank."""
from pathlib import Path
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


class AnalystAssets(StaticFiles):
    # These are React dashboard routes, not arbitrary filesystem paths. Unknown
    # paths retain StaticFiles' 404 behavior rather than receiving a SPA page.
    dashboard_paths = frozenset({
        "new-analysis", "bank-events", "overview", "risk-records",
        "transactions", "relationships", "model-quality", "data-quality",
    })

    async def check_config(self):
        if Path(self.directory).is_dir():
            await super().check_config()

    async def get_response(self, path, scope):
        if not Path(self.directory).is_dir():
            return HTMLResponse("<h1>Кабинет аналитика временно недоступен</h1>", status_code=503)
        if path.rstrip("/") in self.dashboard_paths:
            return await super().get_response("index.html", scope)
        return await super().get_response(path, scope)
