"""Общее состояние системы («общая доска»).

Все агенты читают и пишут в один и тот же словарь. Это и есть то, что
объединяет их в одну систему, а не в набор отдельных скриптов.
"""
import operator
from typing import Annotated, TypedDict


class PixelState(TypedDict):
    brief: str              # исходный бриф пользователя
    task: dict              # структурированное задание от Art Director
    route: dict             # решения Jev-like модели (тип, сложность, уверенность)
    generator_model: str    # какая модель генерирует (результат роутинга)
    ux_notes: str           # заметки UX Researcher
    html: str               # текущая версия экрана
    feedback: str           # замечания валидатора для доработки
    verdict: dict           # последний вердикт валидатора
    attempt: int            # номер текущей попытки генерации
    outcome: str            # running | pass | fail | needs_human | rejected
    log: Annotated[list, operator.add]   # журнал шагов (дописывается)
