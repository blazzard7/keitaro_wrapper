from types import SimpleNamespace as NS

import pytest

from app.errors import DomainError
from app.services import domain as d


def B(state="active", share=0, pinned=False, pub_state=None, pub_share=None):
    return NS(draft_state=state, draft_share=share, is_pinned=pinned,
              published_state=pub_state, published_share=pub_share, keitaro_binding_id=None)


def shares(bs):
    return [b.draft_share for b in bs]


def test_equal_split():
    bs = [B(), B(), B(), B()]
    d.redistribute(bs)
    assert shares(bs) == [25, 25, 25, 25]


def test_remainder_goes_to_first():
    bs = [B(), B(), B()]
    d.redistribute(bs)
    assert shares(bs) == [34, 33, 33] and sum(shares(bs)) == 100


def test_pinned_example_from_spec():
    # A закреплён на 25, B и C делят 75 -> 38 и 37 (37.5 с учётом целых)
    a, b, c = B(share=25, pinned=True), B(), B()
    d.redistribute([a, b, c])
    assert shares([a, b, c]) == [25, 38, 37] and sum(shares([a, b, c])) == 100


def test_removed_gets_zero_and_is_ignored():
    a, b = B(), B(state="removed", share=50)
    d.redistribute([a, b])
    assert shares([a, b]) == [100, 0]


def test_all_pinned_must_sum_to_100():
    with pytest.raises(DomainError):
        d.redistribute([B(share=30, pinned=True), B(share=30, pinned=True)])
    ok = [B(share=60, pinned=True), B(share=40, pinned=True)]
    d.redistribute(ok)
    assert shares(ok) == [60, 40]


def test_too_much_pinned():
    with pytest.raises(DomainError):
        d.redistribute([B(share=99, pinned=True), B(), B()])


def test_error_does_not_touch_shares():
    bs = [B(share=99, pinned=True), B(share=7), B(share=7)]
    with pytest.raises(DomainError):
        d.redistribute(bs)
    assert shares(bs) == [99, 7, 7]


def test_dirty_rules():
    assert d.is_dirty(B(pub_state=None))                                   # новый
    assert not d.is_dirty(B(state="removed", pub_state=None))              # добавили и убрали
    assert not d.is_dirty(B(share=50, pub_state="active", pub_share=50))
    assert d.is_dirty(B(share=40, pub_state="active", pub_share=50))       # изменилась доля
    assert d.is_dirty(B(state="removed", pub_state="active", pub_share=50))
    assert d.is_dirty(B(state="active", share=10, pub_state="removed", pub_share=0))  # Bring back
    assert not d.is_dirty(B(state="removed", pub_state="removed", pub_share=0))


def test_cancel():
    new = B()
    assert d.cancel_binding(new) is True                                   # не публиковался -> удалить
    edited = B(share=10, pub_state="active", pub_share=50)
    assert d.cancel_binding(edited) is False
    assert (edited.draft_state, edited.draft_share) == ("active", 50)
    deleted = B(state="removed", pub_state="active", pub_share=50)
    d.cancel_binding(deleted)
    assert (deleted.draft_state, deleted.draft_share) == ("active", 50)


def test_publish_validation():
    with pytest.raises(DomainError):
        d.validate_for_publish([B(share=60), B(share=30)])
    with pytest.raises(DomainError):
        d.validate_for_publish([B(state="removed")])
    d.validate_for_publish([B(share=60), B(share=40)])


def test_apply_published_and_clean():
    a = B(share=100)
    d.apply_published(a, 777)
    assert (a.published_state, a.published_share, a.keitaro_binding_id) == ("active", 100, 777)
    assert not d.is_dirty(a)
    a.draft_state, a.draft_share = "removed", 0
    d.apply_published(a)
    assert (a.published_state, a.published_share) == ("removed", 0) and not d.is_dirty(a)


def test_sync_external_removal_keeps_binding():
    b = B(share=50, pub_state="active", pub_share=50)
    assert d.merge_remote_absent(b) is True
    assert (b.published_state, b.draft_state, b.draft_share) == ("removed", "removed", 0)
    assert d.merge_remote_absent(b) is False                               # повторная синхронизация - без изменений


def test_sync_keeps_unpublished_draft():
    b = B(share=30, pub_state="active", pub_share=50)                      # есть локальный черновик
    d.merge_remote_present(b, share=60)
    assert (b.published_share, b.draft_share) == (60, 30)                  # черновик не затёрт


def test_sync_updates_clean_draft():
    b = B(share=50, pub_state="active", pub_share=50)
    d.merge_remote_present(b, share=70)
    assert (b.published_share, b.draft_share) == (70, 70)
