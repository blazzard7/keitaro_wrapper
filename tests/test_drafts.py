
import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.errors import DomainError, NotFoundError
from app.models import (
    Base,
    Campaign,
    Offer,
    Stream,
    StreamOffer,
    StreamOfferHistory,
)
from app.services.drafts import (
    add_offer,
    remove_offer,
    restore_offer,
    set_pin,
    cancel,
)



def make_drafts_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)

    campaign = Campaign(
        keitaro_campaign_id=123,
        name="Test campaign",
    )
    db.add(campaign)
    db.flush()

    stream = Stream(
        campaign=campaign,
        keitaro_stream_id=101,
        name="Test stream",
        type="regular",
        schema="landings",
    )
    db.add(stream)

    offer1 = Offer(keitaro_offer_id=1001, name="Offer 1")
    offer2 = Offer(keitaro_offer_id=1002, name="Offer 2")
    offer3 = Offer(keitaro_offer_id=1003, name="Offer 3")

    db.add_all([offer1, offer2, offer3])
    db.flush()

    binding1 = StreamOffer(
        stream=stream,
        offer=offer1,
        published_state="active",
        published_share=50,
        draft_state="active",
        draft_share=50,
        is_pinned=False,
        keitaro_binding_id=501,
    )

    binding2 = StreamOffer(
        stream=stream,
        offer=offer2,
        published_state="active",
        published_share=50,
        draft_state="active",
        draft_share=50,
        is_pinned=False,
        keitaro_binding_id=502,
    )

    db.add_all([binding1, binding2])
    db.commit()

    return engine, db, campaign, stream, offer1, offer2, offer3, binding1, binding2


def get_binding(db, stream_id, offer_id):
    return db.scalar(
        select(StreamOffer).where(
            StreamOffer.stream_id == stream_id,
            StreamOffer.offer_id == offer_id,
        )
    )


def get_history(db, stream_id):
    return db.scalars(
        select(StreamOfferHistory).where(
            StreamOfferHistory.stream_id == stream_id
        )
    ).all()


def test_add_offer_redistributes_shares():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    add_offer(db, stream.id, 1003)

    db.refresh(b1)
    db.refresh(b2)

    b3 = get_binding(db, stream.id, offer3.id)

    assert b3 is not None
    assert b3.draft_state == "active"

    shares = sorted([
        b1.draft_share,
        b2.draft_share,
        b3.draft_share,
    ])

    assert shares == [33, 33, 34]
    assert sum(shares) == 100

    history = get_history(db, stream.id)
    assert len(history) == 1
    assert history[0].action == "add"

    db.close()
    engine.dispose()


def test_remove_offer_archives_binding():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    remove_offer(db, stream.id, 1001)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.draft_state == "removed"
    assert b1.draft_share == 0

    assert b2.draft_state == "active"
    assert b2.draft_share == 100

    # Запись не удаляется физически.
    assert db.get(StreamOffer, b1.id) is not None

    history = get_history(db, stream.id)
    assert len(history) == 1
    assert history[0].action == "remove"

    db.close()
    engine.dispose()


def test_cannot_remove_last_active_offer():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    remove_offer(db, stream.id, 1001)

    with pytest.raises(DomainError):
        remove_offer(db, stream.id, 1002)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.draft_state == "removed"
    assert b2.draft_state == "active"

    db.close()
    engine.dispose()


def test_restore_offer_redistributes_shares():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    remove_offer(db, stream.id, 1001)
    restore_offer(db, stream.id, 1001)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.draft_state == "active"
    assert b2.draft_state == "active"

    assert b1.draft_share == 50
    assert b2.draft_share == 50

    history = get_history(db, stream.id)
    assert [h.action for h in history] == ["remove", "restore"]

    db.close()
    engine.dispose()


def test_pinning_preserves_fixed_share():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    set_pin(db, stream.id, 1001, True, 60)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.is_pinned is True
    assert b1.draft_share == 60

    assert b2.is_pinned is False
    assert b2.draft_share == 40

    db.close()
    engine.dispose()


def test_invalid_pinning_rolls_back_changes():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    set_pin(db, stream.id, 1001, True, 60)

    # Попытка закрепить второй оффер на 50% даст сумму 110%.
    with pytest.raises(DomainError):
        set_pin(db, stream.id, 1002, True, 50)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.is_pinned is True
    assert b1.draft_share == 60

    # Неудачная операция не должна изменить второй оффер.
    assert b2.is_pinned is False
    assert b2.draft_share == 40

    db.close()
    engine.dispose()


def test_unpin_redistributes_shares():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    set_pin(db, stream.id, 1001, True, 60)
    set_pin(db, stream.id, 1001, False, None)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.is_pinned is False
    assert b2.is_pinned is False

    assert b1.draft_share == 50
    assert b2.draft_share == 50

    db.close()
    engine.dispose()


def test_cancel_removes_new_local_binding():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    add_offer(db, stream.id, 1003)

    new_binding = get_binding(db, stream.id, offer3.id)
    assert new_binding is not None

    cancel(db, stream.id)

    new_binding = get_binding(db, stream.id, offer3.id)
    assert new_binding is None

    history = get_history(db, stream.id)
    assert any(h.action == "cancel_new" for h in history)

    db.close()
    engine.dispose()


def test_cancel_restores_published_state():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    remove_offer(db, stream.id, 1001)
    cancel(db, stream.id)

    db.refresh(b1)
    db.refresh(b2)

    assert b1.draft_state == "active"
    assert b1.draft_share == 50

    assert b2.draft_state == "active"
    assert b2.draft_share == 50

    db.close()
    engine.dispose()

def test_cancel_restores_pinned_state():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    set_pin(db, stream.id, 1001, True, 60)
    remove_offer(db, stream.id, 1001)
    cancel(db, stream.id)

    db.refresh(b1)

    assert b1.draft_state == "active"
    assert b1.draft_share == 50
    assert b1.is_pinned is False  # Проверяем фактическое поведение cancel.

    db.close()
    engine.dispose()

def test_cannot_add_existing_active_offer():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    with pytest.raises(DomainError):
        add_offer(db, stream.id, 1001)

    db.close()
    engine.dispose()


def test_cannot_remove_already_removed_offer():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    remove_offer(db, stream.id, 1001)

    with pytest.raises(DomainError):
        remove_offer(db, stream.id, 1001)

    db.close()
    engine.dispose()


def test_cannot_restore_active_offer():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    with pytest.raises(DomainError):
        restore_offer(db, stream.id, 1001)

    db.close()
    engine.dispose()


def test_cannot_modify_remote_deleted_stream():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    stream.remote_deleted = True
    db.commit()

    with pytest.raises(DomainError):
        add_offer(db, stream.id, 1003)

    db.close()
    engine.dispose()

def test_cannot_remove_offer_not_bound_to_stream():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    with pytest.raises(NotFoundError):
        remove_offer(db, stream.id, 9999)

    db.close()
    engine.dispose()

def test_cannot_modify_redirect_stream():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = (
        make_drafts_db()
    )

    stream.schema = "redirect"
    stream.bindings.clear()
    db.commit()

    with pytest.raises(DomainError, match="не содержит офферов"):
        add_offer(db, stream.id, 1003)

    db.close()
    engine.dispose()

def test_cannot_add_offer_missing_from_catalog():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    with pytest.raises(NotFoundError, match="Обновите каталог"):
        add_offer(db, stream.id, 9999)

    db.close()
    engine.dispose()
def test_add_offer_restores_archived_binding():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    remove_offer(db, stream.id, 1001)

    add_offer(db, stream.id, 1001)

    db.refresh(b1)

    assert b1.draft_state == "active"
    assert b1.draft_share == 50

    history = get_history(db, stream.id)
    assert [h.action for h in history] == ["remove", "restore"]

    db.close()
    engine.dispose()
def test_cannot_pin_removed_offer():
    engine, db, campaign, stream, offer1, offer2, offer3, b1, b2 = make_drafts_db()

    remove_offer(db, stream.id, 1001)

    with pytest.raises(DomainError, match="Нельзя закрепить удалённый оффер"):
        set_pin(db, stream.id, 1001, True, 50)

    db.refresh(b1)
    assert b1.draft_state == "removed"
    assert b1.is_pinned is False

    db.close()
    engine.dispose()
