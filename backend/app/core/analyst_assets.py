"""Only built public assets are served; an absent dashboard does not stop the bank."""
from pathlib import Path
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


class AnalystAssets(StaticFiles):
    async def check_config(self):
        if Path(self.directory).is_dir():
            await super().check_config()

    async def get_response(self, path, scope):
        if not Path(self.directory).is_dir():
            return HTMLResponse("<h1>Кабинет аналитика временно недоступен</h1>", status_code=503)
        return await super().get_response(path, scope)
