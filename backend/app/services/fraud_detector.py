"""Adapt bank ledger rows to a fresh, advisory-only transaction rule engine.

The candidate is never persisted. Mirrored ledger credits are behavioral inputs,
not additional directed graph edges. Public explanations are an explicit allowlist.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import statistics

from fastapi import HTTPException
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Card, Transaction, User
from app.domain.risk_schemas import DataUncertainty, RiskCheckOut, RiskComponent, RiskFactor
from app.services.bank_service import find_recipient, normalize_recipient
from app.services.risk_explanations import describe_signal, public_label
from app.services.transaction_features import TransactionFeatureReference
from app.services.transaction_rules import BoundedTransactionGraph, ScenarioRuleConfig, TransactionScenarioEngine

HISTORY_LIMIT = 50_000
PUBLIC_CODES = {"large_amount", "rapid_velocity", "daily_velocity", "structuring", "new_recipient",
                "night_activity", "rapid_cashout", "dormant_account", "behavior_deviation",
                "many_to_one", "short_cycle"}


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _level(score):
    if score >= 0.95:
        return "critical"
    if score >= 0.72:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _component(signals, source):
    factors = [RiskFactor(code=s["code"], label=public_label(s["code"]),
                          description=describe_signal(s), strength=s["strength"], source=source)
               for s in signals if s["code"] in PUBLIC_CODES]
    score = max((f.strength for f in factors), default=0.0)
    return RiskComponent(score=score, level=_level(score), factors=factors)


class FraudDetector:
    def evaluate_transfer(self, db: Session, current: User, recipient: str,
                          amount: Decimal, *, as_of: datetime | None = None) -> RiskCheckOut:
        now = _utc(as_of or datetime.now(timezone.utc))
        if db.scalar(select(User.id).where(User.id == current.id)) is None:
            raise HTTPException(401, "Аккаунт недоступен")
        target = find_recipient(db, normalize_recipient(recipient))
        if target.id == current.id:
            raise HTTPException(400, "Для перевода выберите другого получателя")
        cards = list(db.scalars(select(Card).order_by(Card.id)))
        active = {}
        valid_cards = {}
        for card in cards:
            if card.currency == "KZT":
                valid_cards[card.id] = card
                if card.status == "active":
                    active.setdefault(card.user_id, card)
        sender_card = active.get(current.id)
        target_card = active.get(target.id)
        if sender_card is None:
            raise HTTPException(404, "Активная тестовая карта не найдена")
        if target_card is None:
            raise HTTPException(400, "У получателя нет активной карты в тенге")
        if int(amount * 100) > sender_card.balance:
            raise HTTPException(400, "Недостаточно средств")

        # Bounded read; Python rechecks UTC chronology for SQLite's naive dates.
        rows = list(db.scalars(select(Transaction).where(
            Transaction.status == "completed", Transaction.created_at < now.replace(tzinfo=None)
        ).order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(HISTORY_LIMIT + 1)))
        truncated = len(rows) > HISTORY_LIMIT
        rows = sorted(rows[:HISTORY_LIMIT], key=lambda r: (_utc(r.created_at), r.id))
        rows = [r for r in rows if _utc(r.created_at) < now and r.amount != 0
                and r.card_id in valid_cards and valid_cards[r.card_id].user_id == r.user_id]
        users = {u.phone: u for u in db.scalars(select(User))}
        credits = {r.request_key: r for r in rows if r.type == "income" and r.amount > 0 and r.request_key}
        by_id = {r.id: r for r in rows}
        unresolved = False
        edges = []
        destinations = {}
        for row in rows:
            if row.type != "transfer" or row.amount >= 0:
                continue
            credit = credits.get(f"credit:{row.id}")
            destination = None
            if credit is not None:
                if (credit.amount == -row.amount and credit.user_id != row.user_id
                        and _utc(credit.created_at) >= _utc(row.created_at)):
                    destination = credit.card_id
            else:
                # Legacy rows have no paired credit: only resolve unambiguous cards.
                try:
                    person = users.get(normalize_recipient(row.recipient or ""))
                except HTTPException:
                    person = None
                candidates = [c.id for c in valid_cards.values() if person and c.user_id == person.id]
                if len(candidates) == 1 and person.id != row.user_id:
                    destination = candidates[0]
            if destination is None:
                unresolved = True
                continue
            destinations[row.id] = destination
            edges.append((row, destination))

        own = [r for r in rows if r.user_id == current.id and r.card_id == sender_card.id]
        outgoing = [r for r in own if r.amount < 0]
        values = [-r.amount / 100 for r in outgoing if _utc(r.created_at) >= now - timedelta(days=30)]
        median = statistics.median(values) if values else 1.0
        mad = statistics.median([abs(v - median) for v in values]) if values else 1.0
        reference = TransactionFeatureReference(schema_version="1.0", amount_median=median,
                                                amount_mad=max(mad, 1.0), categories={})
        config = ScenarioRuleConfig(night_utc_offset_hours=5, outbound_history_only=True)
        engine = TransactionScenarioEngine(reference, config)
        behavioral = []
        for row in own:
            sender_id = f"card:{row.card_id}"
            destination = f"card:{destinations[row.id]}" if row.id in destinations else f"unknown:{row.id}"
            direction = "outbound" if row.amount < 0 else "inbound"
            if direction == "inbound":
                origin = None
                if row.request_key and row.request_key.startswith("credit:"):
                    try:
                        origin = by_id.get(int(row.request_key[7:]))
                    except ValueError:
                        pass
                sender_id = f"card:{origin.card_id}" if origin else f"external:{row.id}"
                destination = f"card:{row.card_id}"
            behavioral.append(self._record(row.id, current.id, sender_id, destination,
                                           _utc(row.created_at), abs(row.amount) / 100, direction))
        if behavioral:
            engine.evaluate_chunk(pd.DataFrame(behavioral))
        # Never use behavioral replay's mirrored graph or external expense edges.
        engine.graph = BoundedTransactionGraph(config)
        for row, destination in edges:
            if _utc(row.created_at) >= now - timedelta(hours=config.graph_window_hours):
                engine.graph.evaluate_and_add(timestamp=pd.Timestamp(_utc(row.created_at)),
                    sender=f"card:{row.card_id}", recipient=f"card:{destination}", amount=-row.amount / 100)
        candidate = self._record("preview", current.id, f"card:{sender_card.id}",
                                 f"card:{target_card.id}", now, float(amount), "outbound")
        result = engine.evaluate_chunk(pd.DataFrame([candidate])).iloc[0]
        rules = json.loads(result["rule_explanation"])
        # With no outgoing baseline, relative-amount rules would invent a norm.
        if not values:
            rules = [s for s in rules if s["code"] not in {"large_amount", "new_recipient", "night_activity", "behavior_deviation"}]
        transaction_risk = _component(rules, "rule")
        if sender_card.balance > 0 and int(amount * 100) >= sender_card.balance * Decimal("0.8"):
            factor = RiskFactor(code="balance_share", label="Значительная доля доступного баланса",
                description="Сумма перевода составляет не менее 80% доступного баланса карты отправителя.",
                strength=0.8, source="rule")
            transaction_risk.factors.append(factor)
            transaction_risk.score = max(transaction_risk.score, factor.strength)
            transaction_risk.level = _level(transaction_risk.score)
        graph_signals = json.loads(result["graph_explanation"])
        # The source rule triggers when a *new* sender crosses the threshold.
        # A recipient with an already concentrated inflow stays concerning for
        # repeat senders too; candidate is counted once as a distinct sender.
        inflow_senders = {row.card_id for row, destination in edges
                          if destination == target_card.id
                          and _utc(row.created_at) >= now - timedelta(hours=1)}
        inflow_senders.add(sender_card.id)
        if (len(inflow_senders) >= config.many_to_one_senders_1h
                and not any(s["code"] == "many_to_one" for s in graph_signals)):
            graph_signals.append({"code": "many_to_one", "strength": 1.0,
                                  "evidence": {"unique_senders_1h": len(inflow_senders)}})
        recipient_risk = _component(graph_signals, "graph")
        reasons = []
        if len(outgoing) < 5:
            reasons.append("Недостаточно исходящих операций для устойчивого профиля отправителя.")
        if unresolved:
            reasons.append("Часть старых переводов не удалось связать со счетами; граф неполон.")
        if truncated:
            reasons.append("Доступная история ограничена последними 50000 операциями.")
        reasons.append("Проверка использует внутреннюю историю банка; внешние счета и ML-модели не учтены.")
        score = max(recipient_risk.score, transaction_risk.score)
        return RiskCheckOut(evaluated_at=now.isoformat(), recipient_risk=recipient_risk,
            transaction_risk=transaction_risk, overall_level=_level(score),
            data_uncertainty=DataUncertainty(level="high" if len(outgoing) < 5 or truncated else "medium",
                                             reasons=reasons, sender_history_count=len(outgoing)))

    @staticmethod
    def _record(tx, user, sender, recipient, when, amount, direction):
        return {"transaction_id": str(tx), "client_id": str(user), "sender_account_id": sender,
                "recipient_account_id": recipient, "transaction_timestamp": when.isoformat(),
                "transaction_amount": amount, "direction": direction, "currency": "KZT"}
