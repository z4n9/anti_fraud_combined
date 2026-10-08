import sqlite3
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.core.database import configure_sqlite
from app import seed


def test_upgrade_legacy_database_preserves_bank_data(tmp_path, monkeypatch):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, created_at DATETIME NOT NULL);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), card_name TEXT NOT NULL,
          last_four TEXT NOT NULL, balance INTEGER NOT NULL, currency TEXT NOT NULL, expiry TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), card_id INTEGER NOT NULL REFERENCES cards(id),
          type TEXT NOT NULL, title TEXT NOT NULL, amount INTEGER NOT NULL, recipient TEXT, message TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
          created_at DATETIME NOT NULL, request_key TEXT UNIQUE, balance_after INTEGER);
        CREATE TABLE trusted_people (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), name TEXT NOT NULL,
          phone TEXT NOT NULL, relationship TEXT NOT NULL, verified BOOLEAN NOT NULL, protection_active BOOLEAN NOT NULL,
          notifications_enabled BOOLEAN NOT NULL, confirmation_enabled BOOLEAN NOT NULL);
        ''')
        db.execute('INSERT INTO users VALUES (1,?,?,?)',('Алихан','+77000000025','2026-01-01 00:00:00'))
        db.execute('INSERT INTO cards VALUES (1,1,?,?,?,?,?,?)',('AMAN Gold','4582',123456789,'KZT','09/29','active'))
        db.execute('INSERT INTO transactions VALUES (42,1,1,?,?,?,?,?,?,?,?,?)',('transfer','Сохранённая операция',-50001,'+77010000043','Не потерять','completed','2026-01-02 00:00:00','legacy-key',123456789))
        db.execute('INSERT INTO trusted_people VALUES (1,1,?,?,?,?,?,?,?)',('Айдана Сапанова','+77010000043','Дочь',1,1,1,0))
        before_cards=db.execute('SELECT * FROM cards').fetchall()
        before_transactions=db.execute('SELECT * FROM transactions').fetchall()
        before_user=db.execute('SELECT id,name,phone,created_at FROM users').fetchall()
    engine=create_engine(f'sqlite:///{path.as_posix()}',connect_args={'check_same_thread':False})
    event.listen(engine,'connect',configure_sqlite)
    monkeypatch.setattr(seed,'engine',engine)
    monkeypatch.setattr(seed,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False))
    try:
        seed.seed_data()
        seed.seed_data()
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT * FROM cards WHERE id=1').fetchall()==before_cards
            assert db.execute('SELECT * FROM transactions').fetchall()==before_transactions
            assert db.execute('SELECT id,name,phone,created_at FROM users WHERE id=1').fetchall()==before_user
            assert db.execute('SELECT test_iin FROM users WHERE id=1').fetchone()==('TEST0001',)
            assert db.execute('SELECT role FROM users WHERE id=1').fetchone()==('client',)
            assert db.execute("SELECT role FROM users WHERE test_iin='TEST0099'").fetchone()==('analyst',)
            assert db.execute("SELECT count(*) FROM cards JOIN users ON users.id=cards.user_id WHERE users.test_iin='TEST0099'").fetchone()==(0,)
            assert db.execute('SELECT name,verified,relationship_verified,invitation_status,protection_active,confirmation_enabled FROM trusted_people WHERE user_id=1').fetchone()==('Айдана Омарова',0,0,None,1,0)
            assert db.execute('SELECT count(*) FROM mock_citizens').fetchone()==(7,)
            assert db.execute('SELECT count(*) FROM mock_relationships').fetchone()==(5,)
            assert db.execute('SELECT count(*) FROM trusted_invitations').fetchone()==(0,)
            assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        backups=list((tmp_path/'backups').glob('*.db'))
        assert len(backups)==1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute('SELECT * FROM cards').fetchall()==before_cards
            assert backup.execute('SELECT * FROM transactions').fetchall()==before_transactions
    finally:
        engine.dispose()

def test_old_demo_consent_is_reset_for_real_recipient(tmp_path):
    from app.migrations import migrate
    path=tmp_path/'demo-v2.db'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE users (id INTEGER PRIMARY KEY, test_iin TEXT);
        INSERT INTO users VALUES (1,'TEST0001');
        CREATE TABLE trusted_invitations (id INTEGER PRIMARY KEY, status TEXT);
        INSERT INTO trusted_invitations VALUES (7,'accepted');
        CREATE TABLE trusted_people (id INTEGER PRIMARY KEY, verified BOOLEAN, relationship_verified BOOLEAN,
            invitation_status TEXT, trusted_person_test_iin TEXT, current_invitation_id INTEGER);
        INSERT INTO trusted_people VALUES (1,1,1,'accepted','TEST0002',7);
        ''')
    engine=create_engine(f'sqlite:///{path.as_posix()}')
    try:
        migrate(engine)
        migrate(engine)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT status,accepted_by_user_id FROM trusted_invitations').fetchone()==('pending',None)
            assert db.execute('SELECT invitation_status FROM trusted_people').fetchone()==('pending',)
    finally:
        engine.dispose()
