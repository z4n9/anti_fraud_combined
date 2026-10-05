"""Private Mock eGov adapter: one pair in, a minimal verified result out."""
from sqlalchemy import and_, or_, select
from app.domain.models import MockCitizen, MockRelationship

def verify_relationship(db, client_iin, trusted_iin):
    client = db.scalar(select(MockCitizen).where(MockCitizen.test_iin == client_iin))
    trusted = db.scalar(select(MockCitizen).where(MockCitizen.test_iin == trusted_iin))
    if client is None or trusted is None:
        return {"verified": False, "reason": "citizen_not_found"}
    if client.id == trusted.id:
        return {"verified": False, "reason": "relationship_not_found"}
    link = db.scalar(select(MockRelationship).where(or_(
        and_(MockRelationship.person_1_id == client.id, MockRelationship.person_2_id == trusted.id),
        and_(MockRelationship.person_1_id == trusted.id, MockRelationship.person_2_id == client.id))))
    if link is None:
        return {"verified": False, "reason": "relationship_not_found"}
    relationship = link.relationship_from_1_to_2 if link.person_1_id == client.id else link.relationship_from_2_to_1
    return {"verified": True, "relationship": relationship,
            "trusted_person": {"test_iin": trusted.test_iin, "full_name": trusted.full_name, "phone": trusted.phone}}
