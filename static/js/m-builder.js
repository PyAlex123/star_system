/* Конструктор «Моих форм»: карточки вопросов слева, превью «как увидит ученик» справа.
   Сохраняется сам (через 1,2 с после правки); уйти с несохранёнными правками без
   предупреждения нельзя. Превью рисует тот же m-render.js, что и страница ученика.
   Подключается после monitoring.js. */

const MB_TYPES = [
  ["mood", "Настроение — 5 лиц"], ["scale", "Шкала 1–5 или 1–10"], ["light", "Светофор"],
  ["single", "Один вариант"], ["multi", "Несколько вариантов"], ["short", "Короткий ответ"], ["long", "Развёрнутый ответ"],
];
const MB_GRADED = ["single", "multi", "short"];
const MB_SAVE_DELAY = 1200;

let mb = null;                 // {form, selected, dirty, saving, error, savedAt}
let mbTimer = null;
let mbDrag = null;

/* ── Открыть и закрыть ────────────────────────────────────── */

async function openBuilder(id, form = null) {
  try {
    form = form || await api(`/api/monitoring/forms/${id}`);
  } catch (error) {
    return toast(error.message, "error");
  }
  mb = { form, selected: 0, dirty: false, saving: false, error: "", savedAt: Date.now() };
  monView = "builder";
  if (currentPage() !== "monitor") switchPage("monitor");
  renderBuilder();
  window.scrollTo(0, 0);
}

async function closeBuilder() {
  if (mb?.dirty || mb?.saving) {
    await saveForm();
    if (mb?.dirty) {
      return confirmAction("Уйти без сохранения?", `Последние правки не сохранились: ${escapeHtml(mb.error || "нет связи")}.`,
        () => { mb = null; backToMon(); }, "Уйти");
    }
  }
  mb = null;
  backToMon();
}

window.addEventListener("beforeunload", (event) => {
  if (mb && (mb.dirty || mb.saving)) { event.preventDefault(); event.returnValue = ""; }
});

/* ── Сохранение ───────────────────────────────────────────── */

function changed({ rerender = false } = {}) {
  mb.dirty = true;
  clearTimeout(mbTimer);
  mbTimer = setTimeout(saveForm, MB_SAVE_DELAY);
  if (rerender) renderBuilder(); else { renderPreview(); renderStatus(); }
}

async function saveForm() {
  clearTimeout(mbTimer);
  if (!mb || !mb.dirty) return;
  const form = mb.form;
  mb.saving = true;
  mb.dirty = false;
  renderStatus();
  try {
    await api(`/api/monitoring/forms/${form.id}`, {
      method: "PUT", json: { title: form.title, kind: form.kind, settings: form.settings, questions: form.questions },
    });
    if (mb?.form === form) { mb.error = ""; mb.savedAt = Date.now(); }
  } catch (error) {
    if (mb?.form === form) { mb.dirty = true; mb.error = error.message; }
  } finally {
    if (mb?.form === form) { mb.saving = false; renderStatus(); }
  }
}

function renderStatus() {
  const el = $("mbStatus");
  if (!el || !mb) return;
  const at = new Date(mb.savedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  el.className = `mb-status ${mb.error ? "bad" : ""}`;
  el.innerHTML = mb.error ? `${icon("warning", "ic ic-sm")}Не сохранено: ${escapeHtml(mb.error)}`
    : mb.saving || mb.dirty ? "Сохраняем…" : `${icon("check", "ic ic-sm")}Сохранено в ${at}`;
}

/* ── Разметка ─────────────────────────────────────────────── */

function renderBuilder() {
  const f = mb.form;
  const qs = f.questions;
  const graded = qs.some((q) => q.correct?.length);
  $("monHost").innerHTML = `
    <div class="mb-head">
      <button class="btn btn-ghost btn-sm" onclick="closeBuilder()">${icon("arrow-left", "ic ic-sm")}Мои формы</button>
      <input type="text" class="mb-title" data-f="title" maxlength="120" value="${escapeHtml(f.title)}" aria-label="Название формы">
      <span class="mb-status" id="mbStatus" role="status"></span>
      <button class="btn" onclick="launchFromBuilder()" ${qs.length ? "" : "disabled"}>${icon("play", "ic ic-sm")}Запустить</button>
    </div>
    <div class="mb-grid">
      <div class="mb-main">
        <section class="card mb-settings" aria-label="Настройки формы">
          <div class="field"><span class="field-label">Тип</span>
            <div class="seg" role="radiogroup" aria-label="Тип формы">
              <button role="radio" aria-checked="${f.kind === "survey"}" onclick="setFormKind('survey')">Опрос</button>
              <button role="radio" aria-checked="${f.kind === "test"}" onclick="setFormKind('test')">Тест</button>
            </div>
            <p class="field-hint">${f.kind === "test" ? "У вопросов можно отметить правильный ответ и баллы."
              : "Без правильных ответов — мнение и самочувствие."}</p></div>
          <div class="field"><span class="field-label">После отправки ученик видит</span>
            <div class="seg" role="radiogroup" aria-label="Что видит ученик">
              ${[["none", "Ничего"], ["score", "Балл"], ["full", "Балл и ответы"]].map(([k, n]) =>
                `<button role="radio" aria-checked="${f.settings.after === k}" ${k !== "none" && !graded ? "disabled title='Нет вопросов с правильным ответом'" : ""}
                  onclick="setFormSetting('after', '${k}')">${n}</button>`).join("")}
            </div></div>
          <label class="check"><input type="checkbox" ${f.settings.shuffle ? "checked" : ""} onchange="setFormSetting('shuffle', this.checked)">
            <span>Перемешивать вопросы и варианты — у каждого ученика свой порядок</span></label>
          <label class="check"><input type="checkbox" ${f.settings.show_names ? "checked" : ""} onchange="setFormSetting('show_names', this.checked)">
            <span>Показывать имена в ответах о настроении (обычно — только общая сводка)</span></label>
        </section>
        <div class="mb-list" id="mbList">${qs.length ? qs.map(mbQuestion).join("")
          : `<div class="card empty">${icon("list", "ic ic-xl")}<b>Вопросов пока нет</b>Добавьте свой вопрос или стандартный из библиотеки.</div>`}</div>
        <div class="mb-add">
          <div class="menu-wrap" id="mbAddMenu">
            <button class="btn btn-ghost" onclick="toggleMenu('mbAddMenu')" ${qs.length >= 30 ? "disabled" : ""}>${icon("plus", "ic ic-sm")}Вопрос</button>
            <div class="menu" role="menu">${MB_TYPES.map(([type, name]) =>
              `<button role="menuitem" onclick="addQuestion('${type}')">${escapeHtml(name)}</button>`).join("")}</div>
          </div>
          <button class="btn btn-ghost" onclick="openLibraryPicker()" ${qs.length >= 30 ? "disabled" : ""}>${icon("plus", "ic ic-sm")}Из библиотеки</button>
          <span class="muted">${qs.length} из 30 вопросов · в тексте можно писать {тема} и {цель} — заполнятся при запуске</span>
        </div>
      </div>
      <aside class="mb-preview" aria-label="Как увидит ученик">
        <span class="caps">Так увидит ученик</span>
        <div class="mb-phone" id="mbPreview"></div>
      </aside>
    </div>`;
  renderStatus();
  renderPreview();
}

function mbQuestion(q, i) {
  const qs = mb.form.questions;
  const sel = i === mb.selected;
  const head = `<header class="mb-q-head">
      <span class="mb-handle" title="Перетащите, чтобы поменять порядок" data-handle="${i}">${icon("grip", "ic ic-sm")}</span>
      <b class="mb-num">${i + 1}</b>
      ${q.std ? `<span class="mon-std">${escapeHtml(q.std)}</span><span class="muted mb-std-note">стандартный вопрос</span>`
        : `<select data-f="type" data-i="${i}" aria-label="Тип вопроса">${MB_TYPES.map(([type, name]) =>
          `<option value="${type}" ${type === q.type ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select>`}
      <span class="grow"></span>
      <button class="icon-btn" title="Выше" aria-label="Переместить выше" onclick="moveQuestion(${i}, -1)" ${i ? "" : "disabled"}>${icon("arrow-up", "ic ic-sm")}</button>
      <button class="icon-btn" title="Ниже" aria-label="Переместить ниже" onclick="moveQuestion(${i}, 1)" ${i < qs.length - 1 ? "" : "disabled"}>${icon("arrow-down", "ic ic-sm")}</button>
      ${q.std ? "" : `<button class="icon-btn" title="Копировать вопрос" aria-label="Копировать вопрос" onclick="copyQuestion(${i})">${icon("copy", "ic ic-sm")}</button>`}
      <button class="icon-btn danger" title="Удалить вопрос" aria-label="Удалить вопрос" onclick="removeQuestion(${i})">${icon("trash", "ic ic-sm")}</button>
    </header>`;
  if (q.std) {
    return `<article class="card mb-q std ${sel ? "on" : ""}" data-q="${i}">${head}<p class="mb-std-text">${escapeHtml(q.text)}</p></article>`;
  }
  return `<article class="card mb-q ${sel ? "on" : ""}" data-q="${i}">${head}
    <textarea data-f="text" data-i="${i}" rows="2" maxlength="300" placeholder="Текст вопроса" aria-label="Текст вопроса">${escapeHtml(q.text)}</textarea>
    ${mbTypeEditor(q, i)}
    <footer class="mb-q-foot">
      <label class="check"><input type="checkbox" data-f="required" data-i="${i}" ${q.required ? "checked" : ""}><span>Обязательный</span></label>
      ${MB_GRADED.includes(q.type) && mb.form.kind === "test" ? `
        <label class="check"><input type="checkbox" data-f="graded" data-i="${i}" ${q.correct ? "checked" : ""}><span>Правильный ответ</span></label>
        ${q.correct ? `<label class="mb-points">Баллы <input type="number" min="1" max="100" data-f="points" data-i="${i}" value="${q.points || 1}"></label>` : ""}` : ""}
    </footer>
  </article>`;
}

function mbTypeEditor(q, i) {
  const test = q.correct && mb.form.kind === "test";
  if (q.type === "single" || q.type === "multi") {
    return `<div class="mb-opts">${(q.options || []).map((o, k) => {
      const right = test && q.correct.includes(o.id);
      return `<div class="mb-opt">
        ${test ? `<button class="mb-right ${right ? "on" : ""} ${q.type}" title="${right ? "Правильный" : "Отметить правильным"}"
          aria-label="Правильный ответ: ${escapeHtml(o.text || `вариант ${k + 1}`)}" aria-pressed="${right}" onclick="toggleCorrect(${i}, '${o.id}')">
          ${right ? icon("check", "ic ic-xs") : ""}</button>` : `<span class="mb-dot ${q.type}" aria-hidden="true"></span>`}
        <input type="text" data-f="option" data-i="${i}" data-k="${k}" maxlength="120" value="${escapeHtml(o.text)}"
          placeholder="Вариант ${k + 1}" aria-label="Вариант ${k + 1}">
        <button class="icon-btn" title="Удалить вариант" aria-label="Удалить вариант ${k + 1}" onclick="removeOption(${i}, ${k})"
          ${q.options.length > 2 ? "" : "disabled"}>${icon("close", "ic ic-sm")}</button>
      </div>`;
    }).join("")}
      <div class="mb-opt-acts">
        <button class="btn btn-link btn-sm" onclick="addOption(${i})" ${(q.options || []).length >= 10 ? "disabled" : ""}>${icon("plus", "ic ic-sm")}Вариант</button>
        <label class="check"><input type="checkbox" data-f="other" data-i="${i}" ${q.other ? "checked" : ""}><span>Вариант «Другое: ___»</span></label>
      </div></div>`;
  }
  if (q.type === "scale") {
    const s = q.scale || { min: 1, max: 5 };
    return `<div class="mb-row">
      <div class="seg" role="radiogroup" aria-label="Шкала">
        <button role="radio" aria-checked="${s.max !== 10}" onclick="setScale(${i}, 5)">1–5</button>
        <button role="radio" aria-checked="${s.max === 10}" onclick="setScale(${i}, 10)">1–10</button>
      </div>
      <input type="text" data-f="left" data-i="${i}" maxlength="40" value="${escapeHtml(s.left || "")}" placeholder="Подпись у 1" aria-label="Подпись у начала шкалы">
      <input type="text" data-f="right" data-i="${i}" maxlength="40" value="${escapeHtml(s.right || "")}" placeholder="Подпись у ${s.max}" aria-label="Подпись у конца шкалы">
    </div>`;
  }
  if (q.type === "light") {
    const names = ["зелёный", "жёлтый", "красный"];
    return `<div class="mb-opts">${["green", "yellow", "red"].map((c, k) => `<div class="mb-opt">
      <span class="mon-lamp ${c}" aria-hidden="true"></span>
      <input type="text" data-f="light" data-i="${i}" data-k="${k}" maxlength="80" value="${escapeHtml((q.lights || [])[k] || "")}"
        aria-label="Подпись: ${names[k]}"></div>`).join("")}</div>`;
  }
  if (q.type === "short" && test) {
    return `<div class="field"><label for="mbSpell${i}">Правильные написания — по одному на строку</label>
      <textarea id="mbSpell${i}" data-f="spellings" data-i="${i}" rows="2" placeholder="I = U / R">${escapeHtml((q.correct || []).join("\n"))}</textarea>
      <p class="field-hint">Регистр, лишние пробелы и «ё/е» не важны.</p></div>`;
  }
  return "";
}

function renderPreview() {
  const host = $("mbPreview");
  if (!host || !mb) return;
  const qs = mb.form.questions;
  if (!qs.length) { host.innerHTML = `<p class="muted mb-preview-empty">Здесь появится вопрос.</p>`; return; }
  mb.selected = Math.min(mb.selected, qs.length - 1);
  const q = qs[mb.selected];
  const shown = { ...q, text: (q.text || "Текст вопроса").replace("{тема}", "…").replace("{цель}", "цель урока") };
  host.innerHTML = `
    <div class="m-progress"><span>${mb.selected + 1} из ${qs.length}</span>
      <span class="m-bar"><i style="width:${((mb.selected + 1) / qs.length) * 100}%"></i></span></div>
    <div class="m-card m-q">${questionHtml(shown, null)}</div>`;
}

/* ── Правки ───────────────────────────────────────────────── */

function newId(prefix, taken) {
  let n = 1;
  while (taken.has(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

function blankQuestion(type) {
  const ids = new Set(mb.form.questions.map((q) => q.id));
  const q = { id: newId("q", ids), std: null, type, text: "", required: true };
  if (type === "single" || type === "multi") { q.options = [{ id: "a", text: "" }, { id: "b", text: "" }]; q.other = false; }
  if (type === "scale") q.scale = { min: 1, max: 5, left: "", right: "" };
  if (type === "light") q.lights = ["всё понятно", "есть вопросы", "не понял"];
  return q;
}

function retype(q, type) {
  const fresh = blankQuestion(type);
  return { ...fresh, id: q.id, text: q.text, required: q.required,
    ...(q.options && fresh.options ? { options: q.options, other: q.other } : {}) };
}

function addQuestion(type) {
  closeMenus();
  mb.form.questions.push(blankQuestion(type));
  mb.selected = mb.form.questions.length - 1;
  changed({ rerender: true });
  document.querySelector(`.mb-q[data-q="${mb.selected}"] textarea`)?.focus();
}

function copyQuestion(i) {
  const ids = new Set(mb.form.questions.map((q) => q.id));
  const copy = JSON.parse(JSON.stringify(mb.form.questions[i]));
  copy.id = newId("q", ids);
  mb.form.questions.splice(i + 1, 0, copy);
  mb.selected = i + 1;
  changed({ rerender: true });
}

function removeQuestion(i) {
  mb.form.questions.splice(i, 1);
  mb.selected = Math.max(0, Math.min(mb.selected, mb.form.questions.length - 1));
  changed({ rerender: true });
}

function moveQuestion(i, step) {
  const qs = mb.form.questions;
  const j = i + step;
  if (j < 0 || j >= qs.length) return;
  [qs[i], qs[j]] = [qs[j], qs[i]];
  mb.selected = j;
  changed({ rerender: true });
  document.querySelector(`.mb-q[data-q="${j}"] [aria-label="${step < 0 ? "Переместить выше" : "Переместить ниже"}"]`)?.focus();
}

function addOption(i) {
  const q = mb.form.questions[i];
  const ids = new Set(q.options.map((o) => o.id));
  const id = "abcdefghij".split("").find((c) => !ids.has(c));
  q.options.push({ id, text: "" });
  changed({ rerender: true });
  document.querySelector(`[data-f="option"][data-i="${i}"][data-k="${q.options.length - 1}"]`)?.focus();
}

function removeOption(i, k) {
  const q = mb.form.questions[i];
  const [gone] = q.options.splice(k, 1);
  if (q.correct) {
    q.correct = q.correct.filter((c) => c !== gone.id);
    if (!q.correct.length) q.correct = [];
  }
  changed({ rerender: true });
}

function toggleCorrect(i, optionId) {
  const q = mb.form.questions[i];
  const list = q.correct || [];
  q.correct = q.type === "single" ? [optionId]
    : list.includes(optionId) ? list.filter((c) => c !== optionId) : [...list, optionId];
  changed({ rerender: true });
}

function setScale(i, max) {
  mb.form.questions[i].scale = { ...(mb.form.questions[i].scale || {}), min: 1, max };
  changed({ rerender: true });
}

function setFormKind(kind) {
  const f = mb.form;
  f.kind = kind;
  if (kind === "test" && f.settings.after === "none") f.settings.after = "score";
  if (kind === "survey") {
    f.questions.forEach((q) => { delete q.correct; delete q.points; });
    f.settings.after = "none";
  }
  changed({ rerender: true });
}

function setFormSetting(key, value) {
  mb.form.settings[key] = value;
  changed({ rerender: key === "after" });
}

/** Ввод в полях карточек — без перерисовки списка, чтобы не сбить курсор. */
function builderInput(el) {
  if (!mb) return;
  const f = el.dataset.f;
  if (f === "title") { mb.form.title = el.value; return changed(); }
  const i = parseInt(el.dataset.i, 10);
  const q = mb.form.questions[i];
  if (!q) return;
  const k = parseInt(el.dataset.k, 10);
  if (f === "text") q.text = el.value;
  else if (f === "option") q.options[k].text = el.value;
  else if (f === "light") q.lights[k] = el.value;
  else if (f === "left" || f === "right") q.scale[f] = el.value;
  else if (f === "spellings") q.correct = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
  else if (f === "points") q.points = Math.max(1, Math.min(100, parseInt(el.value, 10) || 1));
  else if (f === "required") q.required = el.checked;
  else if (f === "other") q.other = el.checked;
  else if (f === "graded") {
    if (el.checked) { q.correct = []; q.points = 1; } else { delete q.correct; delete q.points; }
    return changed({ rerender: true });
  } else if (f === "type") {
    mb.form.questions[i] = retype(q, el.value);
    return changed({ rerender: true });
  }
  mb.selected = i;
  changed();
}

document.addEventListener("input", (e) => {
  const el = e.target;
  if (monView !== "builder" || !el.closest?.("#monHost [data-f]") || el.type === "checkbox" || el.tagName === "SELECT") return;
  builderInput(el);
});
document.addEventListener("change", (e) => {
  if (monView !== "builder" || !e.target.closest("#monHost [data-f]")) return;
  if (e.target.type === "checkbox" || e.target.tagName === "SELECT") builderInput(e.target);
});

// Выбор карточки — превью показывает её вопрос
document.addEventListener("focusin", (e) => {
  const card = e.target.closest?.("#monHost .mb-q");
  if (!card || !mb) return;
  const i = parseInt(card.dataset.q, 10);
  if (i !== mb.selected) {
    document.querySelector(`.mb-q[data-q="${mb.selected}"]`)?.classList.remove("on");
    card.classList.add("on");
    mb.selected = i;
    renderPreview();
  }
});

/* ── Перетаскивание за ручку ──────────────────────────────── */

document.addEventListener("pointerdown", (e) => {
  const handle = e.target.closest?.("#monHost .mb-handle");
  document.querySelectorAll("#monHost .mb-q[draggable]").forEach((c) => c.removeAttribute("draggable"));
  if (handle) handle.closest(".mb-q").setAttribute("draggable", "true");
});
document.addEventListener("dragstart", (e) => {
  const card = e.target.closest?.("#monHost .mb-q");
  if (!card) return;
  mbDrag = parseInt(card.dataset.q, 10);
  card.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", String(mbDrag));
});
document.addEventListener("dragover", (e) => {
  const card = e.target.closest?.("#monHost .mb-q");
  if (mbDrag === null || !card) return;
  e.preventDefault();
  document.querySelectorAll("#monHost .mb-q.drop").forEach((c) => c.classList.remove("drop"));
  card.classList.add("drop");
});
document.addEventListener("drop", (e) => {
  const card = e.target.closest?.("#monHost .mb-q");
  if (mbDrag === null || !card) return;
  e.preventDefault();
  const to = parseInt(card.dataset.q, 10);
  const qs = mb.form.questions;
  const [moved] = qs.splice(mbDrag, 1);
  qs.splice(to, 0, moved);
  mb.selected = to;
  mbDrag = null;
  changed({ rerender: true });
});
document.addEventListener("dragend", () => {
  mbDrag = null;
  document.querySelectorAll("#monHost .mb-q.dragging, #monHost .mb-q.drop").forEach((c) => c.classList.remove("dragging", "drop"));
});

/* ── Библиотека и запуск ──────────────────────────────────── */

function openLibraryPicker() {
  renderLibraryPicker();
  openModal("modalLibrary");
}

function renderLibraryPicker() {
  const have = new Set(mb.form.questions.map((q) => q.id));
  $("libraryList").innerHTML = monLibrary.questions.map((q) => `<div class="mb-lib-row">
    <span class="mon-std">${escapeHtml(q.std)}</span>
    <span class="mb-lib-text"><b>${escapeHtml(q.text)}</b><small class="muted">${escapeHtml(MON_TYPES[q.type])} · ${escapeHtml(q.when)} урока</small></span>
    <button class="btn btn-sm ${have.has(q.id) ? "btn-ghost" : ""}" onclick="addLibraryQuestion('${q.id}')" ${have.has(q.id) ? "disabled" : ""}>
      ${have.has(q.id) ? "Уже в форме" : `${icon("plus", "ic ic-sm")}Добавить`}</button>
  </div>`).join("");
}

function addLibraryQuestion(id) {
  if (mb.form.questions.some((q) => q.id === id) || mb.form.questions.length >= 30) return;
  const source = monLibrary.questions.find((q) => q.id === id);
  const { when, ...question } = JSON.parse(JSON.stringify(source));
  mb.form.questions.push(question);
  mb.selected = mb.form.questions.length - 1;
  changed({ rerender: true });
  renderLibraryPicker();
}

async function launchFromBuilder() {
  await saveForm();
  if (mb.dirty) return toast(`Форма не сохранилась: ${mb.error}`, "error");
  const qs = mb.form.questions;
  const has = (marker) => qs.some((q) => (q.text || "").includes(marker));
  openLaunch(null, {
    key: null, formId: mb.form.id, title: mb.form.title, minutes: 10, uses_topic: has("{тема}"), uses_goals: has("{цель}"),
    about: `${mb.form.kind === "test" ? "Тест" : "Опрос"}: ${qs.length} ${plural(qs.length, "вопрос", "вопроса", "вопросов")}`,
  });
}
