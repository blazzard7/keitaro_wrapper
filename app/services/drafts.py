"""
Локальные (черновые) операции над потоком. В Keitaro ничего не отправляется.

Каждая операция: меняем draft_* -> redistribute -> пишем историю -> commit.
Если любая проверка падает (DomainError), откатываем всю транзакцию.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import DomainError, NotFoundError
from app.models import Offer, Stream, StreamOffer
from app.services import domain
from app.services.sync import log, snapshot


def get_stream(db: Session, stream_id: int) -> Stream:
    s = db.get(Stream, stream_id)
    if s is None:
        raise NotFoundError(f"Поток {stream_id} не найден")
    return s


def supports_offers(s: Stream) -> bool:
    return s.schema == "landings" or bool(s.bindings)


def _binding(s: Stream, keitaro_offer_id: int) -> StreamOffer:
    for b in s.bindings:
        if b.offer.keitaro_offer_id == keitaro_offer_id:
            return b
    raise NotFoundError(f"Оффер {keitaro_offer_id} не привязан к потоку {s.id}")


def _guard(s: Stream) -> None:
    if s.remote_deleted:
        raise DomainError("Поток удалён в Keitaro - изменения невозможны")
    if not supports_offers(s):
        raise DomainError("Этот поток не содержит офферов (схема redirect/action)")


def _run(db: Session, fn):
    try:
        result = fn()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def add_offer(db: Session, stream_id: int, keitaro_offer_id: int) -> Stream:
    s = get_stream(db, stream_id)
    _guard(s)
    offer = db.scalar(select(Offer).where(Offer.keitaro_offer_id == keitaro_offer_id))
    if offer is None:
        raise NotFoundError(f"Оффер {keitaro_offer_id} не найден в справочнике. Обновите каталог (Sync).")

    def op():
        existing = next((b for b in s.bindings if b.offer_id == offer.id), None)
        if existing is not None:
            if existing.draft_state == domain.ACTIVE:
                raise DomainError("Этот оффер уже есть в потоке")
            return _restore(db, s, existing)  # был в архиве - просто возвращаем
        b = StreamOffer(stream_id=s.id, offer_id=offer.id, offer=offer,
                        draft_state=domain.ACTIVE, draft_share=0, is_pinned=False)
        s.bindings.append(b)
        db.flush()
        domain.redistribute(s.bindings)
        log(db, b, "add", None, snapshot(b))
        return s

    return _run(db, op)


def remove_offer(db: Session, stream_id: int, keitaro_offer_id: int) -> Stream:
    s = get_stream(db, stream_id)
    _guard(s)

    def op():
        b = _binding(s, keitaro_offer_id)
        if b.draft_state == domain.REMOVED:
            raise DomainError("Оффер уже удалён")
        if len(domain.active(s.bindings)) <= 1:
            raise DomainError("Нельзя удалить последний активный оффер потока")
        before = snapshot(b)
        b.draft_state, b.draft_share, b.is_pinned = domain.REMOVED, 0, False
        domain.redistribute(s.bindings)
        log(db, b, "remove", before, snapshot(b))
        return s

    return _run(db, op)


def _restore(db: Session, s: Stream, b: StreamOffer) -> Stream:
    before = snapshot(b)
    b.draft_state, b.draft_share, b.is_pinned = domain.ACTIVE, 0, False
    domain.redistribute(s.bindings)
    log(db, b, "restore", before, snapshot(b))
    return s


def restore_offer(db: Session, stream_id: int, keitaro_offer_id: int) -> Stream:
    s = get_stream(db, stream_id)
    _guard(s)

    def op():
        b = _binding(s, keitaro_offer_id)
        if b.draft_state == domain.ACTIVE:
            raise DomainError("Оффер уже активен")
        return _restore(db, s, b)

    return _run(db, op)


def set_pin(db: Session, stream_id: int, keitaro_offer_id: int, pinned: bool, share: int | None) -> Stream:
    s = get_stream(db, stream_id)
    _guard(s)

    def op():
        b = _binding(s, keitaro_offer_id)
        if b.draft_state != domain.ACTIVE:
            raise DomainError("Нельзя закрепить удалённый оффер")
        before = snapshot(b)
        b.is_pinned = pinned
        if pinned and share is not None:
            b.draft_share = share
        domain.redistribute(s.bindings)
        log(db, b, "pin" if pinned else "unpin", before, snapshot(b))
        return s

    return _run(db, op)


def rebalance(db: Session, stream_id: int) -> Stream:
    """Пересчитать доли (например, после того как оффер удалили прямо в Keitaro)."""
    s = get_stream(db, stream_id)
    _guard(s)
    return _run(db, lambda: (domain.redistribute(s.bindings), s)[1])


def cancel(db: Session, stream_id: int) -> Stream:
    s = get_stream(db, stream_id)

    def op():
        for b in list(s.bindings):
            before = snapshot(b)
            if domain.cancel_binding(b):
                log(db, b, "cancel_new", before, None)
                db.flush()
                s.bindings.remove(b)  # delete-orphan удалит строку; история остаётся (FK SET NULL)
            else:
                if snapshot(b) != before:
                    log(db, b, "cancel", before, snapshot(b))
        return s

    return _run(db, op)
