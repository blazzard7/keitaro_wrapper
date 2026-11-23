"""
Чистая бизнес-логика без БД и HTTP: работает с любыми объектами, у которых есть поля привязки
(ORM StreamOffer или простые заглушки в тестах). Поэтому её легко тестировать.
"""
from app.errors import DomainError

ACTIVE = "active"
REMOVED = "removed"


def active(bindings):
    return [b for b in bindings if b.draft_state == ACTIVE]


# ---------- Доли ----------
def redistribute(bindings) -> None:
    """
    Делает сумму draft_share активных привязок ровно 100.
    Закреплённые (is_pinned) сохраняют долю; остаток делится поровну между незакреплёнными.
    Остаток от целочисленного деления отдаётся первым по порядку: 75% на двоих -> 38 и 37.
    Все проверки - ДО записи, поэтому при ошибке доли не остаются в полусогласованном виде.
    """
    act = active(bindings)
    pinned = [b for b in act if b.is_pinned]
    free = [b for b in act if not b.is_pinned]

    for b in pinned:
        if not 0 <= b.draft_share <= 100:
            raise DomainError("Закреплённая доля должна быть от 0 до 100")
    rest = 100 - sum(b.draft_share for b in pinned)

    if act:
        if not free and rest != 0:
            raise DomainError(
                f"Все офферы закреплены, но сумма долей {100 - rest}%, а должна быть 100%"
            )
        if free and rest < len(free):
            raise DomainError(
                f"Закреплено слишком много: на {len(free)} незакреплённых осталось {rest}%"
            )

    for b in bindings:
        if b.draft_state == REMOVED:
            b.draft_share = 0
    if free:
        base, extra = divmod(rest, len(free))
        for i, b in enumerate(free):
            b.draft_share = base + (1 if i < extra else 0)


def validate_for_publish(bindings) -> None:
    act = active(bindings)
    if not act:
        raise DomainError("В потоке должен остаться хотя бы один активный оффер")
    total = sum(b.draft_share for b in act)
    if total != 100:
        raise DomainError(f"Сумма долей {total}%, а должна быть ровно 100%")


# ---------- Dirty ----------
def is_dirty(b) -> bool:
    if b.published_state is None:  # в Keitaro ещё не было
        return b.draft_state == ACTIVE  # добавлен, но не опубликован
    if b.draft_state != b.published_state:
        return True
    return b.draft_state == ACTIVE and b.draft_share != b.published_share


def stream_is_dirty(bindings) -> bool:
    return any(is_dirty(b) for b in bindings)


# ---------- Cancel ----------
def cancel_binding(b) -> bool:
    """
    Возвращает draft к published. True - привязку нужно физически удалить:
    она ни разу не была в Keitaro, значит в архиве ей делать нечего.
    """
    if b.published_state is None:
        return True
    b.draft_state = b.published_state
    b.draft_share = b.published_share or 0
    return False


# ---------- Publish ----------
def apply_published(b, remote_binding_id=None) -> None:
    """Вызывать только ПОСЛЕ успешного ответа Keitaro."""
    if b.draft_state == ACTIVE:
        b.published_state = ACTIVE
        b.published_share = b.draft_share
        if remote_binding_id is not None:
            b.keitaro_binding_id = remote_binding_id
    else:
        b.published_state = REMOVED
        b.published_share = 0
        b.keitaro_binding_id = None


# ---------- Синхронизация с Keitaro ----------
def merge_remote_present(b, share: int, binding_id=None) -> None:
    """Оффер есть в Keitaro. Чужие (наши) черновики не затираем."""
    was_dirty = is_dirty(b)
    b.published_state = ACTIVE
    b.published_share = share
    if binding_id is not None:
        b.keitaro_binding_id = binding_id
    if not was_dirty:
        b.draft_state = ACTIVE
        b.draft_share = share


def merge_remote_absent(b) -> bool:
    """
    Оффера нет в Keitaro, а мы считали его опубликованным (его удалили мимо обёртки).
    Привязку НЕ удаляем: помечаем removed - её можно вернуть через Bring back.
    Возвращает True, если что-то изменилось.
    """
    if b.published_state != ACTIVE:
        return False
    was_dirty = is_dirty(b)
    b.published_state = REMOVED
    b.published_share = 0
    b.keitaro_binding_id = None
    if not was_dirty:
        b.draft_state = REMOVED
        b.draft_share = 0
        b.is_pinned = False
    return True
