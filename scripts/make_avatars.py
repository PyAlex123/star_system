"""Генератор аватаров: 24 персонажа в SVG.

Рисуем сами, чтобы не зависеть от чужих лицензий и работать без интернета.

Что делает картинку живой (и чего не было в первой версии):
  * фон с градиентом и полупрозрачными кругами вместо плоской заливки;
  * тело с радиальным градиентом — свет падает сверху-слева, появляется объём;
  * глаза с двумя бликами: крупным и мелким;
  * румянец на щеках;
  * разные силуэты (уши, рога, антенны, шлем) — персонажи не сливаются в ряду рейтинга.

Запуск:  .venv/Scripts/python.exe scripts/make_avatars.py
"""
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "img" / "avatars"

SIZE = 200
DARK = "#1b2130"       # цвет зрачков и линий рта
WHITE = "#ffffff"


# ── Кирпичики ────────────────────────────────────────────────────────────────

def gradients(key: str, bg1: str, bg2: str, light: str, dark: str) -> str:
    return (
        f'<defs>'
        f'<linearGradient id="bg{key}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/>'
        f'</linearGradient>'
        f'<radialGradient id="bd{key}" cx="0.36" cy="0.28" r="0.88">'
        f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/>'
        f'</radialGradient>'
        f'</defs>'
    )


BACKDROP = (
    '<circle cx="30" cy="36" r="24" fill="#fff" opacity=".10"/>'
    '<circle cx="180" cy="62" r="36" fill="#fff" opacity=".07"/>'
    '<circle cx="154" cy="186" r="28" fill="#fff" opacity=".09"/>'
    '<circle cx="34" cy="168" r="14" fill="#fff" opacity=".08"/>'
)


def eyes(style: str = "normal", cy: int = 104, dx: int = 22, scale: float = 1.0,
         color: str = DARK) -> str:
    """Глаза. Два блика — главный приём, который оживляет мордочку."""
    left, right = 100 - dx, 100 + dx

    if style == "sleepy":
        return "".join(
            f'<path d="M{cx - 11} {cy} q11 -10 22 0" stroke="{color}" stroke-width="4.5" '
            f'fill="none" stroke-linecap="round"/>' for cx in (left, right))

    if style == "visor":
        return (
            f'<rect x="{left - 20}" y="{cy - 13}" width="{dx * 2 + 40}" height="26" rx="13" fill="#131a2b"/>'
            f'<circle cx="{left}" cy="{cy}" r="7" fill="#67e8f9"/>'
            f'<circle cx="{right}" cy="{cy}" r="7" fill="#67e8f9"/>'
            f'<circle cx="{left + 2.5}" cy="{cy - 2.5}" r="2.4" fill="{WHITE}"/>'
            f'<circle cx="{right + 2.5}" cy="{cy - 2.5}" r="2.4" fill="{WHITE}"/>'
        )

    if style == "almond":   # инопланетянин
        return (
            f'<path d="M{left - 15} {cy - 4} q10 -13 23 -3 q-7 18 -20 14 z" fill="#15192a"/>'
            f'<path d="M{right + 15} {cy - 4} q-10 -13 -23 -3 q7 18 20 14 z" fill="#15192a"/>'
            f'<ellipse cx="{left - 4}" cy="{cy - 1}" rx="3.4" ry="2.2" fill="{WHITE}" opacity=".9"'
            f' transform="rotate(-20 {left - 4} {cy - 1})"/>'
            f'<ellipse cx="{right + 4}" cy="{cy - 1}" rx="3.4" ry="2.2" fill="{WHITE}" opacity=".9"'
            f' transform="rotate(20 {right + 4} {cy - 1})"/>'
        )

    if style == "cyclops":
        return (
            f'<circle cx="100" cy="{cy}" r="26" fill="{WHITE}"/>'
            f'<circle cx="102" cy="{cy + 2}" r="15" fill="{color}"/>'
            f'<circle cx="107" cy="{cy - 4}" r="5.4" fill="{WHITE}"/>'
            f'<circle cx="96" cy="{cy + 8}" r="2.6" fill="{WHITE}" opacity=".75"/>'
        )

    if style == "half":     # киборг: живой глаз слева, механический справа
        return (
            f'<ellipse cx="{left}" cy="{cy}" rx="12" ry="13.5" fill="{WHITE}"/>'
            f'<circle cx="{left + 1}" cy="{cy + 1.5}" r="7.6" fill="{color}"/>'
            f'<circle cx="{left + 3.4}" cy="{cy - 2.2}" r="2.9" fill="{WHITE}"/>'
            f'<circle cx="{left - 2.4}" cy="{cy + 4.4}" r="1.5" fill="{WHITE}" opacity=".8"/>'
            f'<circle cx="{right}" cy="{cy}" r="14" fill="#131a2b"/>'
            f'<circle cx="{right}" cy="{cy}" r="9" fill="#ff5a4d"/>'
            f'<circle cx="{right}" cy="{cy}" r="4.5" fill="#ffd0a8"/>'
            f'<circle cx="{right + 3}" cy="{cy - 3}" r="2.2" fill="{WHITE}"/>'
            f'<path d="M{right - 20} {cy - 18} h40" stroke="#5b6679" stroke-width="3" '
            f'stroke-linecap="round" opacity=".8"/>'
        )

    if style == "robot":
        return (
            f'<rect x="{left - 15}" y="{cy - 11}" width="30" height="22" rx="8" fill="#131a2b"/>'
            f'<rect x="{right - 15}" y="{cy - 11}" width="30" height="22" rx="8" fill="#131a2b"/>'
            f'<circle cx="{left}" cy="{cy}" r="6" fill="#67e8f9"/>'
            f'<circle cx="{right}" cy="{cy}" r="6" fill="#67e8f9"/>'
            f'<circle cx="{left + 2}" cy="{cy - 2}" r="2" fill="{WHITE}"/>'
            f'<circle cx="{right + 2}" cy="{cy - 2}" r="2" fill="{WHITE}"/>'
        )

    # обычные: белок + зрачок + крупный и мелкий блики
    rx, ry = 12 * scale, 13.5 * scale
    pupil = 7.6 * scale
    out = []
    for cx in (left, right):
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{WHITE}"/>')
        out.append(f'<circle cx="{cx + 1}" cy="{cy + 1.5}" r="{pupil:.1f}" fill="{color}"/>')
        out.append(f'<circle cx="{cx + 3.4}" cy="{cy - 2.2}" r="{2.9 * scale:.1f}" fill="{WHITE}"/>')
        out.append(f'<circle cx="{cx - 2.4}" cy="{cy + 4.4}" r="{1.5 * scale:.1f}" fill="{WHITE}" opacity=".8"/>')
    return "".join(out)


def blush(color: str = "#ff7f96", cy: int = 124, dx: int = 38, opacity: str = ".38") -> str:
    return (f'<ellipse cx="{100 - dx}" cy="{cy}" rx="11" ry="6.5" fill="{color}" opacity="{opacity}"/>'
            f'<ellipse cx="{100 + dx}" cy="{cy}" rx="11" ry="6.5" fill="{color}" opacity="{opacity}"/>')


def mouth(style: str = "smile", cy: int = 130, color: str = DARK, accent: str = "#ff6b81") -> str:
    if style == "smile":
        return (f'<path d="M86 {cy} q14 13 28 0" stroke="{color}" stroke-width="4.6" '
                f'fill="none" stroke-linecap="round"/>')
    if style == "muzzle":   # носик + два усика-дужки
        return (f'<ellipse cx="100" cy="{cy - 8}" rx="8" ry="5.6" fill="{color}"/>'
                f'<path d="M100 {cy - 3} v5" stroke="{color}" stroke-width="3.4" stroke-linecap="round"/>'
                f'<path d="M100 {cy + 2} q-9 8 -17 2" stroke="{color}" stroke-width="3.6" '
                f'fill="none" stroke-linecap="round"/>'
                f'<path d="M100 {cy + 2} q9 8 17 2" stroke="{color}" stroke-width="3.6" '
                f'fill="none" stroke-linecap="round"/>')
    if style == "teeth":
        return (f'<path d="M78 {cy - 4} q22 20 44 0 q-22 10 -44 0 z" fill="{color}"/>'
                f'<path d="M86 {cy - 1} l5 9 l5 -9 z" fill="{WHITE}"/>'
                f'<path d="M104 {cy - 1} l5 9 l5 -9 z" fill="{WHITE}"/>')
    if style == "fangs":
        return (f'<path d="M80 {cy - 4} q20 16 40 0" stroke="{color}" stroke-width="4.4" '
                f'fill="none" stroke-linecap="round"/>'
                f'<path d="M88 {cy - 1} l4 9 l4 -9 z" fill="{WHITE}"/>'
                f'<path d="M104 {cy - 1} l4 9 l4 -9 z" fill="{WHITE}"/>')
    if style == "beak":
        return (f'<path d="M100 {cy - 14} l13 12 l-13 12 l-13 -12 z" fill="{accent}"/>'
                f'<path d="M100 {cy - 2} l13 -2 l-13 14 l-13 -14 z" fill="{accent}" opacity=".75"/>')
    if style == "wide":     # лягушачья улыбка во всю морду
        return (f'<path d="M64 {cy - 8} q36 26 72 0" stroke="{color}" stroke-width="5" '
                f'fill="none" stroke-linecap="round"/>')
    if style == "grin":     # акулья пасть с рядом зубов
        teeth = "".join(
            f'<path d="M{x} {cy - 3} l5 11 l5 -11 z" fill="{WHITE}"/>' for x in (66, 78, 90, 102, 114))
        return (f'<path d="M62 {cy - 6} q38 30 76 0 q-38 16 -76 0 z" fill="{color}"/>' + teeth)
    if style == "tongue":
        return (f'<path d="M82 {cy - 5} q18 22 36 0 z" fill="{color}"/>'
                f'<path d="M92 {cy + 6} q8 12 16 0 z" fill="{accent}"/>')
    if style == "line":
        return (f'<rect x="86" y="{cy - 2}" width="28" height="5" rx="2.5" fill="{color}" opacity=".8"/>')
    if style == "oh":
        return (f'<ellipse cx="100" cy="{cy}" rx="9" ry="11" fill="{color}"/>'
                f'<ellipse cx="100" cy="{cy + 4}" rx="5" ry="5" fill="{accent}" opacity=".8"/>')
    return ""


# ── Силуэты: уши, рога и прочее над головой ──────────────────────────────────

def top(style: str, key: str, body: str, inner: str, accent: str) -> str:
    if style == "pointed":
        return (f'<path d="M58 78 L46 24 L96 56 Z" fill="url(#bd{key})"/>'
                f'<path d="M142 78 L154 24 L104 56 Z" fill="url(#bd{key})"/>'
                f'<path d="M63 72 L55 38 L86 56 Z" fill="{inner}"/>'
                f'<path d="M137 72 L145 38 L114 56 Z" fill="{inner}"/>')
    if style == "round":
        return (f'<circle cx="58" cy="58" r="23" fill="{accent}"/>'
                f'<circle cx="142" cy="58" r="23" fill="{accent}"/>'
                f'<circle cx="58" cy="58" r="12" fill="{inner}"/>'
                f'<circle cx="142" cy="58" r="12" fill="{inner}"/>')
    if style == "fluffy":   # коала: огромные пушистые уши
        return (f'<circle cx="44" cy="92" r="32" fill="{accent}"/>'
                f'<circle cx="156" cy="92" r="32" fill="{accent}"/>'
                f'<circle cx="44" cy="92" r="19" fill="{inner}" opacity=".85"/>'
                f'<circle cx="156" cy="92" r="19" fill="{inner}" opacity=".85"/>')
    if style == "tufts":    # сова
        return (f'<path d="M60 70 L52 30 L92 58 Z" fill="{accent}"/>'
                f'<path d="M140 70 L148 30 L108 58 Z" fill="{accent}"/>')
    if style == "antenna":
        return (f'<rect x="96" y="22" width="8" height="26" rx="4" fill="{accent}"/>'
                f'<circle cx="100" cy="20" r="11" fill="{accent}"/>'
                f'<circle cx="100" cy="20" r="5" fill="#67e8f9"/>'
                f'<circle cx="102" cy="17" r="2" fill="{WHITE}"/>')
    if style == "horns":
        return (f'<path d="M64 66 q-14 -28 4 -40 q6 18 18 28 z" fill="{accent}"/>'
                f'<path d="M136 66 q14 -28 -4 -40 q-6 18 -18 28 z" fill="{accent}"/>')
    if style == "fin":
        return f'<path d="M100 22 L128 72 L72 72 Z" fill="{accent}"/>'
    if style == "spikes":
        return (f'<path d="M68 62 L78 32 L88 62 Z" fill="{accent}"/>'
                f'<path d="M88 56 L100 22 L112 56 Z" fill="{accent}"/>'
                f'<path d="M112 62 L122 32 L132 62 Z" fill="{accent}"/>')
    if style == "unihorn":
        return (f'<path d="M100 14 L112 62 L88 62 Z" fill="#fcd34d"/>'
                f'<path d="M100 14 L106 38 L94 38 Z" fill="#fbbf24"/>'
                f'<path d="M92 36 h16 M89 48 h22" stroke="#f59e0b" stroke-width="3" '
                f'stroke-linecap="round"/>'
                f'<path d="M64 70 q-16 -22 2 -36 q10 16 24 22 z" fill="{accent}" opacity=".9"/>'
                f'<path d="M136 70 q16 -22 -2 -36 q-10 16 -24 22 z" fill="{accent}" opacity=".9"/>')
    if style == "antennae":  # инопланетянин: два усика с шариками
        return (f'<path d="M78 56 q-10 -22 -22 -28" stroke="{accent}" stroke-width="5" '
                f'fill="none" stroke-linecap="round"/>'
                f'<path d="M122 56 q10 -22 22 -28" stroke="{accent}" stroke-width="5" '
                f'fill="none" stroke-linecap="round"/>'
                f'<circle cx="54" cy="26" r="9" fill="{accent}"/>'
                f'<circle cx="146" cy="26" r="9" fill="{accent}"/>'
                f'<circle cx="56" cy="23" r="3" fill="{WHITE}" opacity=".7"/>'
                f'<circle cx="148" cy="23" r="3" fill="{WHITE}" opacity=".7"/>')
    if style == "leaf":     # клубника
        return (f'<path d="M100 44 q-26 -14 -36 2 q22 6 30 14 z" fill="#22a561"/>'
                f'<path d="M100 44 q26 -14 36 2 q-22 6 -30 14 z" fill="#1d8f53"/>'
                f'<rect x="96" y="22" width="8" height="26" rx="4" fill="#15803d"/>')
    if style == "bolt-top":
        return ""
    return ""


# ── Голова ───────────────────────────────────────────────────────────────────

def head(style: str, key: str) -> str:
    fill = f'url(#bd{key})'
    if style == "box":
        return (f'<rect x="44" y="60" width="112" height="104" rx="30" fill="{fill}"/>'
                f'<rect x="54" y="70" width="40" height="26" rx="13" fill="{WHITE}" opacity=".16"/>')
    if style == "tall":     # инопланетянин: широкий лоб, узкий подбородок
        return (f'<path d="M100 48 q56 0 56 48 q0 48 -56 70 q-56 -22 -56 -70 q0 -48 56 -48 z" fill="{fill}"/>'
                f'<ellipse cx="76" cy="78" rx="18" ry="11" fill="{WHITE}" opacity=".15"'
                f' transform="rotate(-22 76 78)"/>')
    if style == "ghost":
        return (f'<path d="M100 44 q50 0 50 54 v56 q-11 10 -19 0 q-9 11 -18 0 q-9 11 -18 0 '
                f'q-9 11 -18 0 q-8 10 -19 0 v-56 q0 -54 42 -54 z" fill="{fill}"/>')
    if style == "blob":     # слайм
        return (f'<path d="M100 50 q54 0 54 58 q0 44 -54 44 q-54 0 -54 -44 q0 -58 54 -58 z" fill="{fill}"/>'
                f'<path d="M62 92 q-6 20 2 30 q10 -12 6 -30 z" fill="{fill}"/>'
                f'<path d="M142 86 q8 18 0 30 q-12 -12 -6 -30 z" fill="{fill}"/>')
    if style == "berry":
        return (f'<path d="M100 58 q54 0 54 46 q0 46 -54 72 q-54 -26 -54 -72 q0 -46 54 -46 z" fill="{fill}"/>')
    if style == "rocket":
        return (f'<path d="M100 32 q34 30 34 76 v34 h-68 v-34 q0 -46 34 -76 z" fill="{fill}"/>'
                f'<path d="M66 118 q-20 10 -20 34 l20 -8 z" fill="#e5484d"/>'
                f'<path d="M134 118 q20 10 20 34 l-20 -8 z" fill="#e5484d"/>'
                f'<path d="M84 142 h32 q-4 26 -16 34 q-12 -8 -16 -34 z" fill="#fbbf24"/>'
                f'<path d="M92 146 h16 q-2 18 -8 24 q-6 -6 -8 -24 z" fill="#fde047"/>')
    if style == "planet":
        return (f'<circle cx="100" cy="102" r="52" fill="{fill}"/>'
                f'<ellipse cx="100" cy="112" rx="84" ry="18" fill="none" stroke="#fbbf24" '
                f'stroke-width="9" opacity=".92" transform="rotate(-18 100 112)"/>'
                f'<circle cx="68" cy="78" r="9" fill="#fff" opacity=".18"/>'
                f'<circle cx="126" cy="70" r="6" fill="#fff" opacity=".14"/>')
    if style == "bolt":
        return (f'<path d="M112 24 L62 112 h30 l-14 66 l62 -96 h-32 z" fill="{fill}"/>')
    if style == "wide":     # лягушка, акула: приплюснутая голова
        return (f'<ellipse cx="100" cy="112" rx="56" ry="46" fill="{fill}"/>')
    # обычная круглая голова
    return (f'<ellipse cx="100" cy="108" rx="52" ry="50" fill="{fill}"/>')


def muzzle(style: str, color: str) -> str:
    if style == "oval":
        return f'<ellipse cx="100" cy="128" rx="31" ry="23" fill="{color}"/>'
    if style == "wide":
        return f'<ellipse cx="100" cy="132" rx="40" ry="24" fill="{color}"/>'
    if style == "belly":
        return f'<ellipse cx="100" cy="124" rx="36" ry="40" fill="{color}"/>'
    if style == "face":     # светлое «лицо» на всю голову (космонавт)
        return f'<ellipse cx="100" cy="112" rx="42" ry="40" fill="{color}"/>'
    if style == "owlface":  # два диска вокруг глаз — так сова читается с первого взгляда
        return (f'<circle cx="76" cy="106" r="31" fill="{color}"/>'
                f'<circle cx="124" cy="106" r="31" fill="{color}"/>')
    return ""


def extras(key: str, accent: str, light: str) -> str:
    if key == "astronaut":
        return (f'<circle cx="100" cy="106" r="54" fill="none" stroke="{accent}" stroke-width="9"/>'
                f'<path d="M62 84 q14 -20 36 -24" stroke="{WHITE}" stroke-width="8" '
                f'stroke-linecap="round" fill="none" opacity=".55"/>'
                f'<rect x="40" y="96" width="14" height="26" rx="7" fill="{accent}"/>'
                f'<rect x="146" y="96" width="14" height="26" rx="7" fill="{accent}"/>')
    if key == "cyborg":
        return (f'<path d="M100 58 q52 2 52 50 q0 46 -52 54 z" fill="#aab6c6"/>'
                f'<path d="M100 58 v104" stroke="#7b8799" stroke-width="3" opacity=".8"/>'
                f'<circle cx="142" cy="88" r="4.5" fill="#6b7788"/>'
                f'<circle cx="142" cy="134" r="4.5" fill="#6b7788"/>'
                f'<rect x="112" y="150" width="34" height="7" rx="3.5" fill="#6b7788" opacity=".75"/>')
    if key == "robot":
        return (f'<rect x="56" y="150" width="88" height="8" rx="4" fill="{accent}" opacity=".45"/>'
                f'<rect x="34" y="96" width="12" height="30" rx="6" fill="{accent}"/>'
                f'<rect x="154" y="96" width="12" height="30" rx="6" fill="{accent}"/>')
    if key == "penguin":
        return (f'<path d="M100 117 c10 0 16 7 16 13 c0 7 -8 11 -16 11 c-8 0 -16 -4 -16 -11 '
                f'c0 -6 6 -13 16 -13 z" fill="{accent}"/>'
                f'<path d="M85 130 q15 8 30 0" stroke="#c2410c" stroke-width="2.4" '
                f'fill="none" opacity=".55"/>')
    if key == "tiger":
        return (f'<path d="M72 68 q4 14 10 20" stroke="{accent}" stroke-width="7" '
                f'stroke-linecap="round" fill="none"/>'
                f'<path d="M128 68 q-4 14 -10 20" stroke="{accent}" stroke-width="7" '
                f'stroke-linecap="round" fill="none"/>'
                f'<path d="M100 60 v20" stroke="{accent}" stroke-width="7" stroke-linecap="round"/>'
                f'<path d="M52 110 h16 M52 126 h14" stroke="{accent}" stroke-width="6" '
                f'stroke-linecap="round"/>'
                f'<path d="M148 110 h-16 M148 126 h-14" stroke="{accent}" stroke-width="6" '
                f'stroke-linecap="round"/>')
    if key == "panda":
        return (f'<ellipse cx="78" cy="104" rx="19" ry="21" fill="#2b3240" '
                f'transform="rotate(-12 78 104)"/>'
                f'<ellipse cx="122" cy="104" rx="19" ry="21" fill="#2b3240" '
                f'transform="rotate(12 122 104)"/>')
    if key == "strawberry":
        # Семечки держим по краю, чтобы не лезли на лицо
        dots = ""
        for sx, sy in ((70, 92), (130, 92), (62, 118), (138, 118),
                       (74, 146), (126, 146), (100, 160)):
            dots += f'<ellipse cx="{sx}" cy="{sy}" rx="3.4" ry="4.6" fill="#ffe8a3" opacity=".9"/>'
        return dots
    if key == "dragon":
        return (f'<ellipse cx="90" cy="126" rx="3.6" ry="4.6" fill="#0f5132" opacity=".8"/>'
                f'<ellipse cx="110" cy="126" rx="3.6" ry="4.6" fill="#0f5132" opacity=".8"/>')
    if key == "ghost":
        return ""
    if key == "slime":
        return (f'<ellipse cx="78" cy="78" rx="16" ry="10" fill="{WHITE}" opacity=".3" '
                f'transform="rotate(-24 78 78)"/>')
    if key == "planet":
        return (f'<circle cx="34" cy="40" r="3.4" fill="{WHITE}" opacity=".8"/>'
                f'<circle cx="168" cy="36" r="2.6" fill="{WHITE}" opacity=".7"/>'
                f'<circle cx="150" cy="168" r="3" fill="{WHITE}" opacity=".7"/>')
    if key == "rocket":
        return (f'<circle cx="40" cy="44" r="3" fill="{WHITE}" opacity=".75"/>'
                f'<circle cx="162" cy="38" r="2.4" fill="{WHITE}" opacity=".65"/>')
    if key == "owl":
        return (f'<circle cx="76" cy="106" r="31" fill="none" stroke="{accent}" '
                f'stroke-width="3.5" opacity=".45"/>'
                f'<circle cx="124" cy="106" r="31" fill="none" stroke="{accent}" '
                f'stroke-width="3.5" opacity=".45"/>')
    return ""


# ── Описание персонажей ──────────────────────────────────────────────────────
# ключ: (фон1, фон2, тело светлое, тело тёмное, светлая морда, акцент,
#        верх, голова, морда, глаза, рот, румянец)

SPECS = {
    # ── Звери ────────────────────────────────────────────────────────────────
    "fox":       ("#ffc06a", "#f97316", "#fb923c", "#ea6a14", "#fff1e0", "#c2410c",
                  "pointed", "round", "oval", "normal", "muzzle", True),
    "panda":     ("#a7f3d0", "#34d399", "#ffffff", "#dfe6f1", "#ffffff", "#2b3240",
                  "round", "round", "oval", "normal", "muzzle", True),
    "cat":       ("#c4b5fd", "#7c3aed", "#c9b6ff", "#8b5cf6", "#f5f3ff", "#6d28d9",
                  "pointed", "round", "oval", "normal", "muzzle", True),
    "owl":       ("#fcd34d", "#d97706", "#c98a3e", "#9a5b1d", "#fde9c2", "#78350f",
                  "tufts", "round", "owlface", "normal", "beak", False),
    "bear":      ("#e7c9a4", "#b45309", "#c08b55", "#8a5a25", "#f5e2cb", "#8a5a25",
                  "round", "round", "oval", "normal", "muzzle", True),
    "penguin":   ("#93c5fd", "#1d4ed8", "#39415a", "#1b2130", "#ffffff", "#f59e0b",
                  "none", "round", "belly", "normal", "", True),
    "tiger":     ("#fdba74", "#ea580c", "#fb923c", "#dd6111", "#fff7ed", "#7c2d12",
                  "round", "round", "oval", "normal", "muzzle", True),
    "frog":      ("#d9f99d", "#65a30d", "#8fd43f", "#4d8a12", "#eaffc9", "#3f6212",
                  "none", "wide", "", "frog", "wide", True),
    "shark":     ("#7dd3fc", "#0369a1", "#9ec4ef", "#4a7cb8", "#e6f2ff", "#7ba6da",
                  "fin", "wide", "", "normal", "grin", False),
    "koala":     ("#d3dce8", "#64748b", "#aab6c6", "#75839a", "#e8edf5", "#8a97aa",
                  "fluffy", "round", "", "normal", "muzzle", True),
    "wolf":      ("#a5b4fc", "#4338ca", "#8694ad", "#4f5c76", "#e2e8f0", "#39415a",
                  "pointed", "round", "oval", "normal", "muzzle", False),

    # ── Космос и техника ─────────────────────────────────────────────────────
    "robot":     ("#67e8f9", "#0891b2", "#eef2fb", "#b9c4d6", "", "#0e7490",
                  "antenna", "box", "", "robot", "line", False),
    "astronaut": ("#818cf8", "#3730a3", "#f8fafc", "#cbd5e1", "#ffffff", "#4338ca",
                  "none", "round", "face", "normal", "smile", True),
    "alien":     ("#d8b4fe", "#7e22ce", "#86efac", "#22a55f", "", "#16a34a",
                  "antennae", "tall", "", "almond", "smile", False),
    "cyborg":    ("#94a3b8", "#334155", "#f1d2b4", "#c79a6e", "", "#64748b",
                  "none", "round", "", "half", "smile", False),
    "rocket":    ("#4f46e5", "#1e1b4b", "#eef2fb", "#b9c4d6", "", "#e5484d",
                  "none", "rocket", "", "normal", "smile", True),
    "planet":    ("#6d28d9", "#1e1b4b", "#a78bfa", "#6d28d9", "", "#fbbf24",
                  "none", "planet", "", "normal", "smile", True),

    # ── Монстрики и сказка ───────────────────────────────────────────────────
    "dragon":    ("#86efac", "#15803d", "#4ade80", "#15803d", "#dcfce7", "#15803d",
                  "horns", "round", "oval", "normal", "fangs", False),
    "unicorn":   ("#fbcfe8", "#db2777", "#ffffff", "#f3d9e8", "#ffffff", "#f472b6",
                  "unihorn", "round", "oval", "normal", "smile", True),
    "dino":      ("#5eead4", "#0f766e", "#2dd4bf", "#0d9488", "#ccfbf1", "#0f766e",
                  "spikes", "round", "oval", "normal", "teeth", True),
    "ghost":     ("#a78bfa", "#4c1d95", "#ffffff", "#dfe3f1", "", "#c4b5fd",
                  "none", "ghost", "", "normal", "oh", True),
    # Слайм намеренно фиолетовый: зелёный сливался с лягушкой в списке рейтинга
    "slime":     ("#e9d5ff", "#6b21a8", "#c084fc", "#7e22ce", "", "#f0abfc",
                  "none", "blob", "", "normal", "tongue", True),

    # ── Прочее ───────────────────────────────────────────────────────────────
    "strawberry": ("#fda4af", "#be123c", "#f43f5e", "#be123c", "", "#22a561",
                   "leaf", "berry", "", "normal", "smile", True),
    "bolt":       ("#38bdf8", "#1d4ed8", "#fde047", "#f59e0b", "", "#b45309",
                   "none", "bolt", "", "sleepy", "smile", True),
}

# Внутренняя часть круглых ушей. По умолчанию берётся светлый цвет морды,
# но у панды ухо должно быть сплошным, а у тигра — тёплым, иначе получаются «бублики».
EAR_INNER = {
    "panda": "#2b3240",
    "tiger": "#c2410c",
    "bear": "#e0b183",
    "koala": "#cfd8e4",
}

# Внешняя часть круглых ушей. По умолчанию — акцентный цвет, но у тигра
# акцент тёмно-коричневый (им нарисованы полосы), и уши получались как наушники.
EAR_OUTER = {
    "tiger": "#fb923c",
}

# Персонажи, у которых глаза живут выше или ниже обычного
EYE_TUNING = {
    "frog": dict(cy=76, dx=26, scale=1.25),
    "owl": dict(cy=106, dx=24, scale=1.35),
    "shark": dict(cy=92, dx=26, scale=.95),
    "alien": dict(cy=100, dx=22),
    "cyborg": dict(cy=104, dx=24),
    "robot": dict(cy=106, dx=26),
    "rocket": dict(cy=96, dx=17, scale=.82),
    "planet": dict(cy=98, dx=18, scale=.85),
    "ghost": dict(cy=96, dx=19),
    "slime": dict(cy=96, dx=21),
    "berry": dict(cy=104),
    "strawberry": dict(cy=106, dx=20, scale=.85),
    "bolt": dict(cy=96, dx=14),
    "penguin": dict(cy=100, dx=18, scale=.85),
    "koala": dict(cy=104, dx=20),
}

MOUTH_TUNING = {
    "frog": 96, "owl": 136, "shark": 130, "alien": 138, "cyborg": 138,
    "robot": 140, "rocket": 118, "planet": 120, "ghost": 124, "slime": 122,
    "strawberry": 132, "bolt": 118, "penguin": 0, "koala": 134,
}


def build(key: str) -> str:
    (bg1, bg2, light, dark, face_light, accent,
     top_style, head_style, muzzle_style, eye_style, mouth_style, has_blush) = SPECS[key]

    eye_args = EYE_TUNING.get(key, {})
    if eye_style == "frog":
        # Лягушачьи глаза — два шара, вылезающих над головой
        parts_eyes = (
            f'<circle cx="68" cy="68" r="26" fill="url(#bd{key})"/>'
            f'<circle cx="132" cy="68" r="26" fill="url(#bd{key})"/>'
            + eyes("normal", cy=68, dx=32, scale=1.15)
        )
    else:
        parts_eyes = eyes(eye_style, **eye_args)

    mouth_cy = MOUTH_TUNING.get(key, 132)
    parts_mouth = mouth(mouth_style, cy=mouth_cy, accent=accent) if mouth_style and mouth_cy else ""

    blush_cy = {"frog": 110, "rocket": 108, "planet": 110, "ghost": 112,
                "slime": 112, "strawberry": 120, "bolt": 108}.get(key, 126)
    blush_dx = {"rocket": 30, "planet": 32, "bolt": 26, "strawberry": 32}.get(key, 38)
    parts_blush = blush(cy=blush_cy, dx=blush_dx) if has_blush else ""

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" role="img" aria-label="{key}">
{gradients(key, bg1, bg2, light, dark)}
<rect width="{SIZE}" height="{SIZE}" fill="url(#bg{key})"/>
{BACKDROP}
{top(top_style, key, light, EAR_INNER.get(key, face_light or "#ffe3e8"), EAR_OUTER.get(key, accent))}
{head(head_style, key)}
{muzzle(muzzle_style, face_light) if muzzle_style else ""}
{extras(key, accent, light)}
{parts_blush}
{parts_eyes}
{parts_mouth}
</svg>
'''


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key in SPECS:
        (OUT_DIR / f"{key}.svg").write_text(build(key), encoding="utf-8")

    # Старые ключи, которых больше нет в наборе, удалять нельзя вслепую:
    # у детей они могли быть выбраны. Просто сообщаем.
    existing = {p.stem for p in OUT_DIR.glob("*.svg")}
    orphans = existing - set(SPECS)
    print(f"Готово: {len(SPECS)} аватаров в {OUT_DIR}")
    if orphans:
        print("Остались файлы вне набора:", ", ".join(sorted(orphans)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
