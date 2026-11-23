/* Вопрос «Мониторинга» так, как его видит ученик. Один отрисовщик на страницу
   ученика (/m) и на превью в конструкторе — вид совпадает до пикселя.

   questionHtml(q, value) возвращает разметку. Нажатия ловит страница по атрибутам:
     [data-pick]  — выбор: data-v (лица, шкала, светофор) или data-o (варианты)
     [data-text]  — поле ответа (короткий, развёрнутый, «Другое»)
   Лица и светофор — векторные рисунки на токенах темы, всегда с подписью. Звёзд
   в шкалах нет: в системе звезда — это валюта. Подключается после common.js. */

const MOOD_LEVELS = [[5, "Отлично"], [4, "Хорошо"], [3, "Нормально"], [2, "Не очень"], [1, "Плохо"]];
const LIGHT_VALUES = ["green", "yellow", "red"];
const OTHER_OPTION = "_other";
const INSTANT_TYPES = ["mood", "scale", "light"];     // касание сразу ведёт дальше

const MOOD_MOUTHS = {
  5: '<path d="M14 27.5 Q24 40 34 27.5 Z" class="fill"/>',
  4: '<path d="M15.5 29 Q24 36.5 32.5 29"/>',
  3: '<path d="M16.5 31.5 H31.5"/>',
  2: '<path d="M16 34 Q24 28.5 32 34"/>',
  1: '<path d="M14.5 35.5 Q24 25 33.5 35.5"/>',
};

/** Лицо настроения: круг цвета состояния, глаза и рот. */
function faceSvg(level) {
  const brows = level === 1 ? '<path d="M13 15.5 L20 18"/><path d="M35 15.5 L28 18"/>' : "";
  return `<svg class="face-art" viewBox="0 0 48 48" aria-hidden="true">
    <circle cx="24" cy="24" r="21" class="face-bg"/>
    ${brows}<circle cx="17.5" cy="21" r="2.6" class="eye"/><circle cx="30.5" cy="21" r="2.6" class="eye"/>
    ${MOOD_MOUTHS[level]}
  </svg>`;
}

function moodLabel(value) {
  return (MOOD_LEVELS.find(([v]) => v === value) || [, ""])[1];
}

function picked(value, key) {
  if (!value) return false;
  if (Array.isArray(value.o)) return value.o.includes(key);
  return value.o === key || value.v === key;
}

function otherField(q, value) {
  if (!q.other) return "";
  const on = picked(value, OTHER_OPTION);
  return `<input type="text" class="mq-other" data-text="other" maxlength="200" placeholder="Свой вариант"
    value="${escapeHtml(on ? value.t || "" : "")}" ${on ? "" : "hidden"} aria-label="Свой вариант">`;
}

function optionsHtml(q, value) {
  const multi = q.type === "multi";
  const options = [...(q.options || []), ...(q.other ? [{ id: OTHER_OPTION, text: "Другое…" }] : [])];
  return `<div class="mq-opts ${multi ? "multi" : ""}" role="${multi ? "group" : "radiogroup"}">
    ${options.map((o) => {
      const on = picked(value, o.id);
      return `<button type="button" class="mq-opt ${on ? "on" : ""}" data-pick data-o="${escapeHtml(o.id)}"
        role="${multi ? "checkbox" : "radio"}" aria-checked="${on}">
        <span class="mq-mark" aria-hidden="true">${on ? icon("check", "ic ic-sm") : ""}</span>
        <span>${escapeHtml(o.text)}</span></button>`;
    }).join("")}
  </div>${otherField(q, value)}`;
}

function answerHtml(q, value) {
  if (q.type === "mood") {
    return `<div class="mq-faces" role="radiogroup">${MOOD_LEVELS.map(([v, label]) => `
      <button type="button" class="mq-face lvl-${v} ${picked(value, v) ? "on" : ""}" data-pick data-v="${v}"
        role="radio" aria-checked="${picked(value, v)}">${faceSvg(v)}<span>${label}</span></button>`).join("")}</div>`;
  }
  if (q.type === "scale") {
    const s = q.scale || { min: 1, max: 5 };
    const values = Array.from({ length: s.max - s.min + 1 }, (_, i) => s.min + i);
    return `<div class="mq-scale ${values.length > 6 ? "wide" : ""}" role="radiogroup">${values.map((v) => `
      <button type="button" class="mq-num ${picked(value, v) ? "on" : ""}" data-pick data-v="${v}" role="radio"
        aria-checked="${picked(value, v)}" aria-label="${v}${v === s.min && s.left ? ` — ${escapeHtml(s.left)}` : ""}${
          v === s.max && s.right ? ` — ${escapeHtml(s.right)}` : ""}">${v}</button>`).join("")}</div>
      ${s.left || s.right ? `<div class="mq-ends"><span>${escapeHtml(s.left || "")}</span><span>${escapeHtml(s.right || "")}</span></div>` : ""}`;
  }
  if (q.type === "light") {
    return `<div class="mq-lights" role="radiogroup">${LIGHT_VALUES.map((v, i) => `
      <button type="button" class="mq-light ${v} ${picked(value, v) ? "on" : ""}" data-pick data-v="${v}" role="radio"
        aria-checked="${picked(value, v)}"><span class="mq-lamp" aria-hidden="true"></span>
        <span>${escapeHtml((q.lights || [])[i] || "")}</span></button>`).join("")}</div>`;
  }
  if (q.type === "single" || q.type === "multi") return optionsHtml(q, value);
  if (q.type === "short") {
    return `<input type="text" class="mq-text" data-text="answer" maxlength="200" autocomplete="off"
      placeholder="Твой ответ" value="${escapeHtml(value?.t || "")}" aria-label="Твой ответ">`;
  }
  return `<textarea class="mq-text" data-text="answer" maxlength="2000" rows="5" placeholder="Твой ответ"
    aria-label="Твой ответ">${escapeHtml(value?.t || "")}</textarea>`;
}

/** Карточка вопроса. hint — подсказка под текстом («можно несколько»). */
function questionHtml(q, value) {
  const hint = q.type === "multi" ? "Можно выбрать несколько" : !q.required ? "Можно пропустить" : "";
  return `<section class="mq" data-type="${q.type}">
    <h1 class="mq-title">${escapeHtml(q.text)}</h1>
    ${hint ? `<p class="mq-hint">${hint}</p>` : ""}
    ${answerHtml(q, value)}
  </section>`;
}

/** Ответ после нажатия на вариант: новое значение по старому. */
function pickValue(q, current, el) {
  if (el.dataset.v !== undefined) {
    const raw = el.dataset.v;
    return { v: q.type === "light" ? raw : parseInt(raw, 10) };
  }
  const key = el.dataset.o;
  if (q.type === "single") return key === OTHER_OPTION ? { o: key, t: current?.o === key ? current.t || "" : "" } : { o: key };
  const exclusive = new Set((q.options || []).filter((o) => o.exclusive).map((o) => o.id));
  let list = Array.isArray(current?.o) ? current.o.slice() : [];
  if (list.includes(key)) list = list.filter((k) => k !== key);
  else list = exclusive.has(key) ? [key] : [...list.filter((k) => !exclusive.has(k)), key];
  const next = { o: list };
  if (list.includes(OTHER_OPTION)) next.t = current?.t || "";
  return next;
}

/** Ответ готов к отправке? Для «Другое» нужен текст, для текста — непустая строка. */
function answerComplete(q, value) {
  if (!value) return false;
  if (q.type === "short" || q.type === "long") return Boolean((value.t || "").trim());
  if (q.type === "multi") return value.o?.length > 0 && (!value.o.includes(OTHER_OPTION) || Boolean((value.t || "").trim()));
  if (q.type === "single") return value.o !== OTHER_OPTION || Boolean((value.t || "").trim());
  return value.v !== undefined;
}
