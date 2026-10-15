"""Brand display must not rewrite persisted financial records or custom labels."""
import pytest
from app.domain.models import Card


@pytest.mark.parametrize('stored,display', [('AMAN Gold', 'Family Gold'), ('Family Gold', 'Family Gold'), ('Моя карта AMAN', 'Моя карта AMAN')])
def test_card_brand_display_preserves_storage(client, stored, display):
    with client.test_factory() as db:
        card = db.get(Card, 1)
        card.card_name = stored
        db.commit()
        before = (card.balance, card.last_four, card.currency, card.status)
    result = client.get('/api/cards')
    assert result.status_code == 200
    assert result.json()[0]['card_name'] == display
    with client.test_factory() as db:
        card = db.get(Card, 1)
        assert card.card_name == stored
        assert (card.balance, card.last_four, card.currency, card.status) == before
