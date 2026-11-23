"""Доменные ошибки. main.py переводит их в HTTP-коды."""


class DomainError(Exception):
    """Нарушено бизнес-правило (доли, состояния и т.п.) -> 400."""


class NotFoundError(Exception):
    """Сущность не найдена -> 404."""


class KeitaroError(Exception):
    """Keitaro вернул ошибку или недоступен -> 502."""


class WriteDisabledError(Exception):
    """Запись в Keitaro выключена настройкой -> 403."""
