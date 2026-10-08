"""Additive, repeatable SQLite migration. Never resets cards or transactions."""
import sqlite3
from datetime import datetime
from pathlib import Path
from sqlalchemy import inspect, text

ADDITIONS = {
    "users": {"test_iin": "VARCHAR(8)", "password_hash": "VARCHAR(256)",
              "role": "VARCHAR(20) NOT NULL DEFAULT 'client'"},
    "trusted_invitations": {"recipient_user_id": "INTEGER REFERENCES users(id)", "accepted_by_user_id": "INTEGER REFERENCES users(id)"},
    "trusted_people": {
        "relationship_verified": "BOOLEAN NOT NULL DEFAULT 0",
        "invitation_status": "VARCHAR(20)",
        "trusted_person_test_iin": "VARCHAR(8)",
        "current_invitation_id": "INTEGER REFERENCES trusted_invitations(id)",
    },
}

def migrate(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing = [(table, name, kind) for table, columns in ADDITIONS.items() if table in tables
               for name, kind in columns.items()
               if name not in {column["name"] for column in inspector.get_columns(table)}]
    if not missing:
        return
    database = engine.url.database
    if database and database != ":memory:" and Path(database).exists():
        source = Path(database)
        folder = source.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / f"{source.stem}-before-family-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
        with sqlite3.connect(source) as original, sqlite3.connect(backup) as target:
            original.backup(target)
    with engine.connect() as connection:
        # Explicit BEGIN makes SQLite DDL and the legacy reset one atomic change.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for table, name, kind in missing:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_test_iin ON users(test_iin)")
            if any(table == "trusted_people" and name == "relationship_verified" for table, name, _ in missing):
                # A former demo checkmark is not evidence of kinship or consent.
                connection.exec_driver_sql("UPDATE trusted_people SET verified = 0")
                connection.execute(text("UPDATE trusted_people SET name = :new WHERE name = :old"),
                                   {"old": "Айдана Сапанова", "new": "Айдана Омарова"})
            if any(table == "trusted_invitations" and name == "accepted_by_user_id" for table, name, _ in missing):
                # Past demo responses must not impersonate the newly authenticated recipient.
                connection.exec_driver_sql("UPDATE trusted_invitations SET status='pending' WHERE status IN ('accepted','rejected')")
                connection.exec_driver_sql("UPDATE trusted_people SET invitation_status='pending' WHERE current_invitation_id IS NOT NULL")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
