from __future__ import annotations

from app.risk_ledger.services.database_adapters import (
    BsonSourceAdapter,
    SqlDumpSourceAdapter,
    SqliteSourceAdapter,
)
from app.risk_ledger.services.source_adapters import (
    CsvSourceAdapter,
    JsonLinesSourceAdapter,
    JsonSourceAdapter,
    SourceAdapterRegistry,
)


DEFAULT_SOURCE_ADAPTERS = SourceAdapterRegistry(
    (
        CsvSourceAdapter(),
        JsonSourceAdapter(),
        JsonLinesSourceAdapter(),
        SqliteSourceAdapter(),
        SqlDumpSourceAdapter(),
        BsonSourceAdapter(),
    )
)
