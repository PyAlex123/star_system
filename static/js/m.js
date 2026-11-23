/* Страница ученика /m/<код школы>: ожидание → вход → вопросы по одному → «Спасибо».

   Без входа и пароля. Устройство помнит ученика (фамилия, имя, класс) и его участие
   в текущем опросе (token) — в localStorage под ключом ссылки школы. Каждый ответ
   уходит сразу; без связи копится в очереди и досылается, когда сеть вернётся.
   Время окна проверяет сервер: ответ после закрытия он не примет. */

const toast = makeToast({ duration: 3600 });
const LINK = (() => {
  const parts = location.pathname.slice(BASE.length).split("/").filter(Boolean);
  return parts[0] === "m" && parts[1] ? decodeURIComponent(parts[1]) : "";
})();
const KEY = `zv_m_${LINK}`;
const POLL_WAIT = 5000;           // ждём запуска — раз в 5 секунд
const POLL_BUSY = 15000;          // отвечает — реже: узнать, не закрыт ли опрос
const FORGET_AFTER = 10 * 60;     // старый опрос (закрыт больше 10 минут назад) — забываем

let data = { school: "", classes: [], me: null };
let screen = "loading";
let qIndex = 0;
let answers = {};
let pollTimer = null;
let flushing = false;
let finishing = false;

/* ── Память устройства ────────────────────────────────────── */

function memory() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (_) { return {}; }
}
function remember(patch) {
  try { localStorage.setItem(KEY, JSON.stringify({ ...memory(), ...patch })); } catch (_) {}
}
function forget(...keys) {
  const mem = memory();
  keys.forEach((k) => delete mem[k]);
  try { localStorage.setItem(KEY, JSON.stringify(mem)); } catch (_) {}
}

function setOnline(online) { $("mOffline").hidden = online; }

/* ── Связь с сервером ─────────────────────────────────────── */

async function poll() {
  clearTimeout(pollTimer);
  if (!LINK) return show("nolink");
  try {
    const token = memory().token;
    const fresh = await requestJson(`/api/m/${encodeURIComponent(LINK)}/state${token ? `?p=${encodeURIComponent(token)}` : ""}`);
    setOnline(true);
    apply(fresh);
    await flush();
    if (finishing && screen === "question" && !pending().length) await finish();   // «Отправить» без связи
  } catch (error) {
    if (error.status === 404) return show("notfound");
    if (error.status) toast(error.message, "error");
    else setOnline(false);
  }
  pollTimer = setTimeout(poll, screen === "question" ? POLL_BUSY : POLL_WAIT);
}

/** Неотправленные ответы: [{token, question_id, value}]. Повторная отправка безопасна. */
function queue() { return memory().queue || []; }

function pending() {
  const token = memory().token;
  return queue().filter((i) => i.token === token);
}

async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    let item;
    while ((item = pending()[0])) {
      try {
        await requestJson("/api/m/answer", { method: "POST", json: item });
        setOnline(true);
      } catch (error) {
        if (!error.status) { setOnline(false); break; }                 // нет сети — попробуем позже
        if (error.status === 409 || error.status === 404) { remember({ queue: [] }); poll(); break; }
        toast(error.message, "error");                                  // ответ не подошёл — показать и отбросить
      }
      // Убираем именно отправленное: пока шёл запрос, ученик мог ответить ещё раз
      const sent = JSON.stringify(item);
      remember({ queue: queue().filter((i) => JSON.stringify(i) !== sent) });
    }
  } finally {
    flushing = false;
  }
}

function send(q, value) {
  const token = memory().token;
  const items = queue().filter((i) => i.token === token && i.question_id !== q.id);
  remember({ queue: [...items, { token, question_id: q.id, value: value || {} }] });
  flush();
}

/* ── Что показать ─────────────────────────────────────────── */

function apply(fresh) {
  const before = JSON.stringify(data.classes);
  data = fresh;
  $("mSchool").textContent = fresh.school || "";
  let me = fresh.me;
  if (me && me.status !== "active" && me.ended_ago != null && me.ended_ago > FORGET_AFTER) {
    forget("token", "queue");                       // прошлый урок — начинаем с чистого листа
    me = data.me = null;
  }
  if (memory().token && !me) {
    forget("token", "queue");
    if (screen === "question") toast("Учитель убрал тебя из списка — войди заново", "error");
  }
  if (me) {
    if (me.status === "active") {
      if (screen !== "question") startQuestions(me);
      return;
    }
    if (me.status === "done") { if (screen !== "done") showDone(me); return; }
    if (screen !== "closed") showClosed(me.ended_ago);
    return;
  }
  if (screen === "done" || screen === "closed") return;          // ждём кнопки
  if (!fresh.classes.length) return show("waiting");
  if (screen === "join") { if (JSON.stringify(fresh.classes) !== before) fillClasses(); return; }
  const mem = memory();
  if (screen === "confirm" && openClass(mem.class_id)) return;
  if (mem.last && openClass(mem.class_id)) showConfirm(); else showJoin();
}

function openClass(id) { return data.classes.find((c) => c.id === id); }

function show(name) {
  screen = name;
  const screens = {
    waiting: `<div class="m-card m-center m-wait">
        <span class="m-ring pulse" aria-hidden="true">${icon("clock", "ic ic-xl")}</span>
        <h1 class="m-title">Подожди, учитель скоро начнёт</h1>
        <p class="m-lead">Опрос откроется сам — обновлять страницу не нужно.</p></div>`,
    nolink: `<div class="m-card m-center">
        <span class="m-ring" aria-hidden="true">${icon("qr", "ic ic-xl")}</span>
        <h1 class="m-title">Открой ссылку своей школы</h1>
        <p class="m-lead">Её даёт учитель: QR-код в кабинете или адрес на доске.</p></div>`,
    notfound: `<div class="m-card m-center">
        <span class="m-ring warn" aria-hidden="true">${icon("warning", "ic ic-xl")}</span>
        <h1 class="m-title">Ссылка не найдена</h1>
        <p class="m-lead">Проверь адрес у учителя — возможно, в нём опечатка.</p></div>`,
  };
  $("mHost").innerHTML = screens[name] || "";
}

/* ── Вход ─────────────────────────────────────────────────── */

function codeField(classId) {
  const needs = openClass(classId)?.code;
  return `<div class="field" id="jCodeField" ${needs ? "" : "hidden"}>
      <label for="jCode">Код урока</label>
      <input type="text" id="jCode" class="input-mono m-code" inputmode="numeric" maxlength="4" autocomplete="off"
        placeholder="••••" aria-describedby="jCodeHint">
      <p class="field-hint" id="jCodeHint">4 цифры — учитель покажет их на экране</p>
    </div>`;
}

function showJoin(message = "") {
  screen = "join";
  const mem = memory();
  $("mHost").innerHTML = `
    <form class="m-card m-form" onsubmit="submitJoin(event)" novalidate>
      <div><h1 class="m-title">Представься</h1>
        <p class="m-lead">Учитель увидит, кто ответил. Других учеников и их ответы здесь не видно.</p></div>
      <div class="field"><label for="jLast">Фамилия</label>
        <input type="text" id="jLast" maxlength="40" autocomplete="family-name" value="${escapeHtml(mem.last || "")}"></div>
      <div class="field"><label for="jFirst">Имя</label>
        <input type="text" id="jFirst" maxlength="40" autocomplete="given-name" value="${escapeHtml(mem.first || "")}"></div>
      <div class="field"><label for="jClass">Класс</label><select id="jClass" onchange="onClassChange()"></select></div>
      ${codeField(mem.class_id)}
      <p class="field-error" id="jError" ${message ? "" : "hidden"}>${icon("warning", "ic ic-sm")}<span>${escapeHtml(message)}</span></p>
      <button class="btn btn-lg btn-block" type="submit">Начать${icon("arrow-right")}</button>
    </form>`;
  fillClasses();
}

function fillClasses() {
  const select = $("jClass");
  if (!select) return;
  const current = parseInt(select.value, 10) || memory().class_id;
  const single = data.classes.length === 1;
  select.innerHTML = (single ? "" : `<option value="">Выбери класс</option>`) +
    data.classes.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  select.value = String(openClass(current) ? current : single ? data.classes[0].id : "");
  onClassChange();
}

function onClassChange() {
  const id = parseInt($("jClass")?.value, 10);
  if ($("jCodeField")) $("jCodeField").hidden = !openClass(id)?.code;
}

function joinError(message) {
  const box = $("jError");
  if (!box) return toast(message, "error");
  box.hidden = false;
  box.querySelector("span").textContent = message;
}

async function join(last, first, classId, code) {
  try {
    const res = await requestJson(`/api/m/${encodeURIComponent(LINK)}/join`, {
      method: "POST", json: { last_name: last, first_name: first, class_id: classId, code: code || null },
    });
    remember({ token: res.token, last, first, class_id: classId, queue: [] });
    startQuestions(res.me);
  } catch (error) {
    if (!error.status) { setOnline(false); return joinError("Нет связи — попробуй ещё раз"); }
    joinError(error.message);
    if (error.status === 409) poll();
  }
}

function submitJoin(event) {
  event.preventDefault();
  const last = $("jLast").value.trim();
  const first = $("jFirst").value.trim();
  const classId = parseInt($("jClass").value, 10);
  if (!last) { $("jLast").focus(); return joinError("Напиши фамилию"); }
  if (!first) { $("jFirst").focus(); return joinError("Напиши имя"); }
  if (!classId) { $("jClass").focus(); return joinError("Выбери класс"); }
  const code = $("jCode")?.value.trim();
  if (openClass(classId)?.code && !/^\d{4}$/.test(code || "")) { $("jCode").focus(); return joinError("Введи 4 цифры кода урока"); }
  join(last, first, classId, code);
}

function showConfirm() {
  screen = "confirm";
  const mem = memory();
  const cls = openClass(mem.class_id);
  $("mHost").innerHTML = `
    <form class="m-card m-form m-confirm" onsubmit="confirmMe(event)" novalidate>
      <span class="m-ring" aria-hidden="true">${icon("user", "ic ic-xl")}</span>
      <h1 class="m-title">Ты — ${escapeHtml(mem.last)} ${escapeHtml(mem.first)}, ${escapeHtml(cls?.name || "")}?</h1>
      ${codeField(mem.class_id)}
      <p class="field-error" id="jError" hidden>${icon("warning", "ic ic-sm")}<span></span></p>
      <button class="btn btn-lg btn-block" type="submit">Да, это я</button>
      <button class="btn btn-ghost btn-lg btn-block" type="button" onclick="notMe()">Это не я</button>
    </form>`;
}

function confirmMe(event) {
  event.preventDefault();
  const mem = memory();
  const code = $("jCode")?.value.trim();
  if (openClass(mem.class_id)?.code && !/^\d{4}$/.test(code || "")) { $("jCode").focus(); return joinError("Введи 4 цифры кода урока"); }
  join(mem.last, mem.first, mem.class_id, code);
}

function notMe() {
  forget("last", "first", "class_id");
  showJoin();
  $("jLast")?.focus();
}

/* ── Вопросы ──────────────────────────────────────────────── */

function questions() { return data.me?.questions || []; }

function startQuestions(me) {
  data.me = me;
  answers = { ...(me.answers || {}) };
  for (const item of pending()) answers[item.question_id] = item.value;
  const qs = questions();
  const first = qs.findIndex((q) => !answerComplete(q, answers[q.id]));
  qIndex = first === -1 ? qs.length - 1 : first;
  screen = "question";
  renderQuestion();
}

function renderQuestion() {
  const qs = questions();
  const q = qs[qIndex];
  const value = answers[q.id];
  const last = qIndex === qs.length - 1;
  const done = answerComplete(q, value);
  const instantWait = INSTANT_TYPES.includes(q.type) && !done && !last;
  const label = last ? "Отправить ответы" : !done && !q.required ? "Пропустить" : "Далее";
  $("mHost").innerHTML = `
    <div class="m-progress">
      <span>${qIndex + 1} из ${qs.length}</span>
      <span class="m-bar" role="progressbar" aria-label="Вопрос ${qIndex + 1} из ${qs.length}"
        aria-valuemin="1" aria-valuemax="${qs.length}" aria-valuenow="${qIndex + 1}"><i style="width:${((qIndex + 1) / qs.length) * 100}%"></i></span>
    </div>
    <div class="m-card m-q">${questionHtml(q, value)}</div>
    <div class="m-nav">
      ${qIndex > 0 ? `<button class="btn btn-ghost btn-lg" type="button" onclick="go(-1)">${icon("arrow-left")}Назад</button>` : "<span></span>"}
      ${instantWait ? "" : `<button class="btn btn-lg" type="button" id="mNext" onclick="${last ? "finish()" : "go(1)"}"
        ${q.required && !done ? "disabled" : ""}>${label}${last ? "" : icon("arrow-right")}</button>`}
    </div>`;
}

function syncNext() {
  const q = questions()[qIndex];
  const btn = $("mNext");
  if (!btn || !q) return;
  const done = answerComplete(q, answers[q.id]);
  btn.disabled = q.required && !done;
  if (qIndex < questions().length - 1) btn.firstChild.textContent = !done && !q.required ? "Пропустить" : "Далее";
}

/** Текст из поля — в ответ текущего вопроса (без перерисовки, чтобы не сбить ввод). */
function readText(el) {
  const q = questions()[qIndex];
  const text = el.value;
  answers[q.id] = el.dataset.text === "other" ? { ...(answers[q.id] || {}), t: text } : { t: text };
  syncNext();
}

/** Перед переходом: отправить текстовый ответ, если он есть (или стереть пустой необязательный). */
function commitText() {
  const q = questions()[qIndex];
  if (!q || INSTANT_TYPES.includes(q.type)) return;
  const value = answers[q.id];
  if (answerComplete(q, value)) send(q, value);
  else if (!q.required && (q.type === "short" || q.type === "long")) send(q, { t: "" });
}

function go(step) {
  commitText();
  qIndex = Math.max(0, Math.min(questions().length - 1, qIndex + step));
  renderQuestion();
  window.scrollTo({ top: 0, behavior: "instant" });
}

$("mHost").addEventListener("click", (event) => {
  const el = event.target.closest("[data-pick]");
  if (!el || screen !== "question") return;
  const q = questions()[qIndex];
  const value = pickValue(q, answers[q.id], el);
  answers[q.id] = value;
  renderQuestion();
  if (answerComplete(q, value)) send(q, value);
  const other = document.querySelector('[data-text="other"]:not([hidden])');
  if (other && (value.o === OTHER_OPTION || value.o?.includes?.(OTHER_OPTION))) other.focus();
  if (INSTANT_TYPES.includes(q.type) && qIndex < questions().length - 1) {
    setTimeout(() => { if (screen === "question" && questions()[qIndex] === q) go(1); }, 260);
  }
});

$("mHost").addEventListener("input", (event) => {
  if (event.target.matches("[data-text]")) readText(event.target);
});

$("mHost").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.target.matches('input[data-text]')) return;
  event.preventDefault();
  if (!$("mNext")?.disabled) $("mNext").click();
});

async function finish() {
  commitText();
  const qs = questions();
  const missing = qs.findIndex((q) => q.required && !answerComplete(q, answers[q.id]));
  if (missing !== -1) {
    qIndex = missing;
    renderQuestion();
    return toast("Ответь на этот вопрос", "error");
  }
  finishing = true;
  await flush();
  if (pending().length) {
    return toast("Нет связи — ответы отправятся, как только появится интернет", "error", "wifi-off");
  }
  try {
    const res = await requestJson("/api/m/finish", { method: "POST", json: { token: memory().token } });
    finishing = false;
    showDone(res.me);
  } catch (error) {
    if (!error.status) { setOnline(false); return; }
    finishing = false;
    if (error.status === 409 || error.status === 404) return poll();
    toast(error.message, "error");
  }
}

/* ── Итог ─────────────────────────────────────────────────── */

function showDone(me) {
  screen = "done";
  data.me = me;
  const result = me.result
    ? `<p class="m-score"><span>Твой результат</span><b class="num">${fmt(me.result.score)} из ${fmt(me.result.max)}</b></p>` : "";
  $("mHost").innerHTML = `
    <div class="m-card m-center">
      <span class="m-ring ok" aria-hidden="true">${icon("check", "ic ic-xl")}</span>
      <h1 class="m-title">Спасибо! Ответы отправлены</h1>
      ${result}
      <button class="btn btn-ghost btn-lg btn-block" type="button" onclick="nextStudent()">${icon("users")}Следующий ученик</button>
    </div>`;
}

function showClosed(endedAgo) {
  screen = "closed";
  const at = new Date(Date.now() - (endedAgo || 0) * 1000)
    .toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  $("mHost").innerHTML = `
    <div class="m-card m-center">
      <span class="m-ring" aria-hidden="true">${icon("lock", "ic ic-xl")}</span>
      <h1 class="m-title">Опрос закрыт в ${at}</h1>
      <p class="m-lead">Ответы больше не принимаются. То, что ты успел ответить, учитель увидит.</p>
      <button class="btn btn-ghost btn-lg btn-block" type="button" onclick="leaveRun()">Понятно</button>
    </div>`;
}

/** Общий компьютер: следующий ученик вводит себя заново. */
function nextStudent() {
  forget("token", "queue", "last", "first", "class_id");
  screen = "loading";
  data.me = null;
  apply({ ...data, me: null });
}

function leaveRun() {
  forget("token", "queue");
  screen = "loading";
  apply({ ...data, me: null });
}

window.addEventListener("online", () => { setOnline(true); poll(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
poll();
