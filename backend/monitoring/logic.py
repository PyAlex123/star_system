"""Чистые функции «Мониторинга»: ни базы, ни запросов — только данные на входе и выходе.

Ответ ученика хранится словарём с короткими ключами:
    {"v": 4}                    — настроение (1–5) и шкала
    {"v": "green"}              — светофор
    {"o": "b"}                  — один вариант; {"o": "_other", "t": "…"} — «Другое»
    {"o": ["a", "c"], "t": "…"} — несколько вариантов (t — если отмечено «Другое»)
    {"t": "…"}                  — короткий и развёрнутый ответ
"""
import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .library import GOAL, LIGHT_VALUES, MOOD_LEVELS, TOPIC

OTHER = "_other"
MAX_NAME = 40
MAX_SHORT = 200
MAX_LONG = 2000
MAX_TOPIC = 120
MAX_GOALS = 5
MAX_QUESTIONS = 30


class AnswerError(ValueError):
    """Ответ не подходит к вопросу — текст показывается ученику."""


# ── ФИО ───────────────────────────────────────────────────────────────────────

_SPACES = re.compile(r"\s+")


def clean_person(value: Optional[str]) -> str:
    """Фамилия или имя как его ввели: без лишних пробелов, с заглавной буквы.
    Набранное капсом «ИВАНОВ» становится «Иванов»."""
    text = _SPACES.sub(" ", str(value or "")).strip()[:MAX_NAME]
    if len(text) > 1 and text == text.upper() and text != text.lower():
        text = text.title()
    return text[:1].upper() + text[1:] if text else ""


def norm(value: str) -> str:
    """Для сравнения: без регистра, лишних пробелов и разницы «ё/е»."""
    return _SPACES.sub(" ", str(value or "")).strip().lower().replace("ё", "е")


def name_key(last: str, first: str) -> str:
    return f"{norm(last)}|{norm(first)}"


def match_student(roster: Iterable[Tuple[int, str]], last: str, first: str) -> Optional[int]:
    """Ученик класса по фамилии и имени. Найден ровно один — его id, иначе None (гость).

    В базе имя записано «Фамилия Имя [Отчество]». Ребёнок может перепутать поля
    местами — такой вариант тоже ищем, но связываем только при единственном совпадении.
    """
    want = (norm(last), norm(first))
    found = set()
    for student_id, full_name in roster:
        parts = norm(full_name).split(" ")
        if len(parts) < 2:
            continue
        pair = (parts[0], parts[1])
        if pair == want or pair == (want[1], want[0]):
            found.add(student_id)
    return found.pop() if len(found) == 1 else None


# ── Вопросы запуска ───────────────────────────────────────────────────────────

def clean_goals(goals: Iterable[str]) -> List[str]:
    result = []
    for goal in goals or []:
        text = _SPACES.sub(" ", str(goal or "")).strip()[:MAX_TOPIC]
        if text and text not in result:
            result.append(text)
    return result[:MAX_GOALS]


def prepare_questions(questions: Sequence[Dict[str, Any]], topic: str, goals: Sequence[str]) -> List[Dict[str, Any]]:
    """Снимок вопросов для запуска: {тема} и {цель} заполнены.

    Вопрос с {цель} размножается — по вопросу на каждую цель (С15). Без целей
    такой вопрос задать нельзя — ошибка с понятным текстом. Без темы вопрос
    с {тема} берёт свой «простой» текст, если он есть («тема урока»).
    """
    topic = _SPACES.sub(" ", str(topic or "")).strip()[:MAX_TOPIC]
    result: List[Dict[str, Any]] = []
    for source in questions:
        q = copy.deepcopy(source)
        plain = q.pop("text_plain", None)
        text = q.get("text", "")
        if TOPIC in text:
            text = text.replace(TOPIC, topic) if topic else (plain or text.replace(TOPIC, "урока"))
        if GOAL in text:
            if not goals:
                raise ValueError("Впишите хотя бы одну цель урока — по ней ученики оценят себя")
            for i, goal in enumerate(goals, start=1):
                copy_q = copy.deepcopy(q)
                copy_q["id"] = f"{q['id']}-{i}"
                copy_q["text"] = text.replace(GOAL, goal)
                result.append(copy_q)
            continue
        q["text"] = text
        result.append(q)
    if not result:
        raise ValueError("В форме нет вопросов")
    if len(result) > MAX_QUESTIONS:
        raise ValueError(f"Слишком много вопросов: не больше {MAX_QUESTIONS}")
    return result


def public_question(q: Dict[str, Any]) -> Dict[str, Any]:
    """Вопрос для ученика — без правильного ответа."""
    return {k: v for k, v in q.items() if k not in ("correct",)}


# ── Проверка ответа ───────────────────────────────────────────────────────────

def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def validate_answer(q: Dict[str, Any], raw: Any) -> Optional[Dict[str, Any]]:
    """Приводит ответ к формату хранения. None — ответа нет (необязательный пропущен).
    Неподходящий ответ — AnswerError."""
    if not isinstance(raw, dict):
        raise AnswerError("Ответ не распознан")
    kind = q.get("type")
    if kind == "mood":
        value = raw.get("v")
        if value not in [level for level, _ in MOOD_LEVELS]:
            raise AnswerError("Выбери одно из лиц")
        return {"v": int(value)}
    if kind == "scale":
        scale = q.get("scale") or {}
        value = raw.get("v")
        if not isinstance(value, int) or isinstance(value, bool) or not scale.get("min", 1) <= value <= scale.get("max", 5):
            raise AnswerError("Выбери число на шкале")
        return {"v": value}
    if kind == "light":
        if raw.get("v") not in LIGHT_VALUES:
            raise AnswerError("Выбери один из цветов")
        return {"v": raw["v"]}
    option_ids = [o["id"] for o in q.get("options", [])]
    if kind == "single":
        choice = raw.get("o")
        if choice == OTHER and q.get("other"):
            text = _text(raw.get("t"), MAX_SHORT)
            if not text:
                raise AnswerError("Допиши свой вариант")
            return {"o": OTHER, "t": text}
        if choice not in option_ids:
            raise AnswerError("Выбери один вариант")
        return {"o": choice}
    if kind == "multi":
        chosen = raw.get("o")
        if not isinstance(chosen, list) or not chosen:
            if not q.get("required"):
                return None
            raise AnswerError("Отметь хотя бы один вариант")
        allowed = option_ids + ([OTHER] if q.get("other") else [])
        picked = [c for c in allowed if c in chosen]           # порядок вариантов, без повторов
        if len(picked) != len(set(chosen)) or len(picked) != len(chosen):
            raise AnswerError("Такого варианта нет")
        exclusive = {o["id"] for o in q.get("options", []) if o.get("exclusive")}
        if len(picked) > 1 and exclusive & set(picked):
            raise AnswerError("«Ничего» нельзя отметить вместе с другими вариантами")
        result: Dict[str, Any] = {"o": picked}
        if OTHER in picked:
            text = _text(raw.get("t"), MAX_SHORT)
            if not text:
                raise AnswerError("Допиши свой вариант")
            result["t"] = text
        return result
    if kind in ("short", "long"):
        text = _text(raw.get("t"), MAX_SHORT if kind == "short" else MAX_LONG)
        if not text:
            if q.get("required"):
                raise AnswerError("Напиши ответ")
            return None
        return {"t": text}
    raise AnswerError("Вопрос не распознан")


def grade(q: Dict[str, Any], value: Optional[Dict[str, Any]]) -> Tuple[Optional[bool], float]:
    """Верно ли и сколько баллов. (None, 0) — у вопроса нет правильного ответа."""
    correct = q.get("correct")
    if not correct or q.get("type") not in ("single", "multi", "short"):
        return None, 0.0
    points = float(q.get("points") or 1)
    if not value:
        return False, 0.0
    kind = q["type"]
    if kind == "single":
        ok = value.get("o") == correct[0]
    elif kind == "multi":
        ok = set(value.get("o") or []) == set(correct)
    else:
        ok = norm(value.get("t", "")) in {norm(c) for c in correct}
    return ok, points if ok else 0.0


def missing_required(questions: Sequence[Dict[str, Any]], answered: Iterable[str]) -> List[str]:
    done = set(answered)
    return [q["id"] for q in questions if q.get("required") and q["id"] not in done]


# ── Сводка ────────────────────────────────────────────────────────────────────

def _labels(q: Dict[str, Any]) -> List[Tuple[Any, str]]:
    """Возможные значения ответа и их подписи — в порядке показа."""
    kind = q.get("type")
    if kind == "mood":
        return list(MOOD_LEVELS)
    if kind == "scale":
        scale = q.get("scale") or {}
        return [(v, str(v)) for v in range(scale.get("min", 1), scale.get("max", 5) + 1)]
    if kind == "light":
        return list(zip(LIGHT_VALUES, q.get("lights") or ["", "", ""]))
    if kind in ("single", "multi"):
        labels = [(o["id"], o["text"]) for o in q.get("options", [])]
        return labels + ([(OTHER, "Другое")] if q.get("other") else [])
    return []


def summarize(questions: Sequence[Dict[str, Any]],
              answers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Итоги по каждому вопросу.

    answers — [{question_id, value (dict), name, class_name}] для участников запуска.
    Для вариантов — сколько выбрали каждый (доля от ответивших на вопрос);
    для лиц и шкал — ещё и среднее; тексты — списком.
    """
    by_question: Dict[str, List[Dict[str, Any]]] = {}
    for answer in answers:
        by_question.setdefault(answer["question_id"], []).append(answer)

    result = []
    for q in questions:
        got = by_question.get(q["id"], [])
        item: Dict[str, Any] = {"id": q["id"], "std": q.get("std"), "type": q["type"], "text": q["text"],
                                "answered": len(got)}
        labels = _labels(q)
        if labels:
            counts = {key: 0 for key, _ in labels}
            for a in got:
                value = a["value"]
                keys = value.get("o") if q["type"] == "multi" else value.get("o", value.get("v"))
                for key in (keys if isinstance(keys, list) else [keys]):
                    if key in counts:
                        counts[key] += 1
            item["options"] = [{"key": key, "label": label, "count": counts[key]} for key, label in labels]
            if q["type"] in ("mood", "scale") and got:
                item["average"] = round(sum(a["value"]["v"] for a in got) / len(got), 1)
            if q["type"] == "scale":
                item["scale"] = q.get("scale")
        texts = [{"text": a["value"]["t"], "name": a.get("name", ""), "class_name": a.get("class_name", "")}
                 for a in got if a["value"].get("t")]
        if texts:
            item["texts"] = texts
        if q.get("correct"):
            item["correct_count"] = sum(1 for a in got if a.get("correct"))
        result.append(item)
    return result
