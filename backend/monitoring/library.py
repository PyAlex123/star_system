"""Стандартные вопросы С1–С15 и готовые наборы (ТЗ: MDs/07-monitoring.md, §5–6).

Текст стандартного вопроса не редактируется: по его номеру результаты сравниваются
между уроками. Поэтому библиотека — константы в коде, а не записи в базе.

Формат вопроса (он же — снимок вопроса в запуске):
    id        — постоянный ключ внутри формы: "c2", "c15-1" (С15 — по вопросу на цель)
    std       — номер стандартного вопроса «С2» или None у своих вопросов
    type      — mood | scale | light | single | multi | short | long
    text      — текст; {тема} и {цель} заполняются при запуске
    required  — обязательный
    options   — [{id, text, exclusive?}] у single и multi
    other     — вариант «Другое: ___» у single и multi
    scale     — {min, max, left, right} у шкалы
    lights    — три подписи светофора: зелёный, жёлтый, красный
    correct   — правильный ответ (тесты): id вариантов или допустимые написания
    points    — баллы за верный ответ
"""
import copy
from typing import Any, Dict, List, Optional

QUESTION_TYPES = ("mood", "scale", "light", "single", "multi", "short", "long")

# Лица настроения: значение 5 — «Отлично», 1 — «Плохо»
MOOD_LEVELS = [(5, "Отлично"), (4, "Хорошо"), (3, "Нормально"), (2, "Не очень"), (1, "Плохо")]
LIGHT_VALUES = ("green", "yellow", "red")

TOPIC = "{тема}"
GOAL = "{цель}"


def _opts(*texts: str, exclusive: str = "") -> List[Dict[str, Any]]:
    """Варианты с постоянными id a, b, c… — по ним хранятся ответы."""
    return [{"id": chr(ord("a") + i), "text": t, **({"exclusive": True} if t == exclusive else {})}
            for i, t in enumerate(texts)]


LIBRARY: List[Dict[str, Any]] = [
    {"id": "c1", "std": "С1", "when": "начало", "type": "mood", "required": True,
     "text": "С каким настроением ты пришёл на урок?"},
    {"id": "c2", "std": "С2", "when": "конец", "type": "mood", "required": True,
     "text": "Какое у тебя настроение сейчас?"},
    {"id": "c3", "std": "С3", "when": "начало", "type": "single", "required": True,
     "text": "Сколько у тебя сил?", "options": _opts("Полон сил", "Нормально", "Устал")},
    {"id": "c4", "std": "С4", "when": "конец", "type": "multi", "required": True, "other": True,
     "text": "Что мешало тебе на уроке?",
     "options": _opts("шумно", "не выспался", "сложно", "скучно", "ничего", exclusive="ничего")},
    {"id": "c5", "std": "С5", "when": "конец", "type": "light", "required": True,
     "text": "Насколько понятна тема «{тема}»?", "text_plain": "Насколько понятна тема урока?",
     "lights": ["могу объяснить другу", "понял, но есть вопросы", "не понял"]},
    {"id": "c6", "std": "С6", "when": "конец", "type": "single", "required": True,
     "text": "Смог бы ты решить такое задание сам?", "options": _opts("Да", "С подсказкой", "Нет")},
    {"id": "c7", "std": "С7", "when": "конец", "type": "short", "required": True,
     "text": "Что было самым трудным?"},
    {"id": "c8", "std": "С8", "when": "конец", "type": "long", "required": False,
     "text": "Какой вопрос ты хочешь задать учителю?"},
    {"id": "c9", "std": "С9", "when": "конец", "type": "single", "required": True,
     "text": "Какой был темп урока?", "options": _opts("Медленно", "В самый раз", "Быстро")},
    {"id": "c10", "std": "С10", "when": "конец", "type": "scale", "required": True,
     "text": "Насколько было интересно?",
     "scale": {"min": 1, "max": 5, "left": "скучно", "right": "очень интересно"}},
    {"id": "c11", "std": "С11", "when": "конец", "type": "multi", "required": True,
     "text": "Что понравилось больше всего?",
     "options": _opts("объяснение", "задания", "практика", "работа в группе", "видео", "игра")},
    {"id": "c12", "std": "С12", "when": "конец", "type": "single", "required": True,
     "text": "Хватило времени на задания?", "options": _opts("Да", "Не совсем", "Нет")},
    {"id": "c13", "std": "С13", "when": "конец", "type": "scale", "required": True,
     "text": "Оцени свою работу на уроке",
     "scale": {"min": 1, "max": 5, "left": "слабо", "right": "отлично"}},
    {"id": "c14", "std": "С14", "when": "конец", "type": "short", "required": True,
     "text": "Продолжи: «Сегодня я узнал(а)…»"},
    {"id": "c15", "std": "С15", "when": "конец", "type": "single", "required": True,
     "text": "Я могу: {цель}", "options": _opts("Да", "Почти", "Нет")},
]

BY_ID = {q["id"]: q for q in LIBRARY}

# Готовые наборы. minutes — время по умолчанию в окне запуска.
SETS: List[Dict[str, Any]] = [
    {"key": "pulse", "title": "Пульс за 30 секунд", "about": "Настроение, понимание темы и темп урока",
     "questions": ["c2", "c5", "c9"], "minutes": 5},
    {"key": "start", "title": "Начало урока", "about": "С каким настроением и сколько сил",
     "questions": ["c1", "c3"], "minutes": 5},
    {"key": "exit", "title": "Выходной билет", "about": "Достигнуты ли цели урока и что осталось непонятным",
     "questions": ["c15", "c6", "c7", "c8"], "minutes": 10},
    {"key": "big", "title": "Большая рефлексия", "about": "Настроение, интерес, что понравилось и что узнал",
     "questions": ["c2", "c10", "c11", "c12", "c13", "c14"], "minutes": 10},
]

SETS_BY_KEY = {s["key"]: s for s in SETS}


def library_question(qid: str) -> Dict[str, Any]:
    """Копия стандартного вопроса: правки снимка не должны трогать библиотеку."""
    question = copy.deepcopy(BY_ID[qid])
    question.pop("when", None)
    return question


def set_questions(key: str) -> Optional[List[Dict[str, Any]]]:
    found = SETS_BY_KEY.get(key)
    return [library_question(qid) for qid in found["questions"]] if found else None


def uses(questions: List[Dict[str, Any]], marker: str) -> bool:
    return any(marker in q.get("text", "") for q in questions)


def public_library() -> Dict[str, Any]:
    """Библиотека и наборы для панели учителя."""
    return {
        "questions": [library_question(q["id"]) | {"when": q["when"]} for q in LIBRARY],
        "sets": [dict(s, uses_topic=uses(set_questions(s["key"]), TOPIC),
                      uses_goals=uses(set_questions(s["key"]), GOAL)) for s in SETS],
    }
