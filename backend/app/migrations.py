"""Additive, repeatable SQLite migration. Never resets cards or transactions."""
import sqlite3
import re
from datetime import datetime
from pathlib import Path
from sqlalchemy import inspect, text

ADDITIONS = {
    "users": {"test_iin": "VARCHAR(8)", "password_hash": "VARCHAR(256)",
              "role": "VARCHAR(20) NOT NULL DEFAULT 'client'"},
    "trusted_invitations": {"recipient_user_id": "INTEGER REFERENCES users(id)", "accepted_by_user_id": "INTEGER REFERENCES users(id)",
                            "active": "BOOLEAN NOT NULL DEFAULT 0", "relationship_verified": "BOOLEAN NOT NULL DEFAULT 0"},
    "transfer_requests": {"anti_scam_json": "TEXT NOT NULL DEFAULT '{\"pressure\":null,\"secrecy\":null,\"stranger\":null}'",
                          "bank_review_reason": "VARCHAR(30)"},
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
    with engine.connect() as probe:
        transfer_sql = probe.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='transfer_requests'").scalar() or ""
    # Inspect the status literal, not the similarly named audit column.
    # A database can already contain bank_review_reason and still retain the
    # legacy CHECK constraint that forbids the new workflow status.
    rebuild = bool(transfer_sql and "'bank_review'" not in transfer_sql)
    if not missing and not rebuild:
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
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
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
            if any(table == "trusted_invitations" and name == "active" for table, name, _ in missing):
                connection.exec_driver_sql("UPDATE trusted_invitations SET active=1 WHERE id IN (SELECT current_invitation_id FROM trusted_people WHERE current_invitation_id IS NOT NULL)")
                connection.exec_driver_sql("UPDATE trusted_invitations SET relationship_verified=1 WHERE id IN (SELECT current_invitation_id FROM trusted_people WHERE relationship_verified=1)")
            if rebuild:
                ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='transfer_requests'").scalar()
                indexes = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='transfer_requests' AND sql IS NOT NULL").scalars().all()
                ddl = re.sub(r'(CREATE TABLE\s+)(?:"transfer_requests"|transfer_requests)', r'\1transfer_requests_rebuild', ddl, count=1, flags=re.I)
                ddl = ddl.replace("'pending_approval'", "'pending_approval','bank_review'")
                columns = [row[1] for row in connection.exec_driver_sql('PRAGMA table_info(transfer_requests)').all()]
                quoted = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
                connection.exec_driver_sql(ddl)
                connection.exec_driver_sql(f'INSERT INTO transfer_requests_rebuild ({quoted}) SELECT {quoted} FROM transfer_requests')
                connection.exec_driver_sql('DROP TABLE transfer_requests')
                connection.exec_driver_sql('ALTER TABLE transfer_requests_rebuild RENAME TO transfer_requests')
                for index in indexes:
                    connection.exec_driver_sql(index)
            if "protection_change_requests" in tables:
                connection.exec_driver_sql('DROP INDEX IF EXISTS uq_protection_pending_action')
                connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_protection_pending_disable ON protection_change_requests(user_id) WHERE status='pending' AND action='disable'")
                connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_protection_pending_remove ON protection_change_requests(user_id,invitation_id) WHERE status='pending' AND action='remove'")
            if "transfer_participants" in tables and "transfer_requests" in tables:
                connection.exec_driver_sql("""INSERT OR IGNORE INTO transfer_participants(request_id,user_id,invitation_id,response)
                    SELECT r.id,r.trusted_user_id,r.invitation_id,'pending' FROM transfer_requests r
                    JOIN trusted_invitations i ON i.id=r.invitation_id
                    WHERE r.status='pending_approval' AND i.owner_user_id=r.sender_user_id
                      AND i.relationship_verified=1 AND i.status='accepted'
                      AND i.accepted_by_user_id=r.trusted_user_id AND i.recipient_user_id=r.trusted_user_id
                    """)
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError("Migration would leave invalid foreign keys")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
