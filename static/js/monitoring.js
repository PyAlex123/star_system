/* Вкладка «Мониторинг»: ссылка школы, готовые наборы, запуск, экран для класса, итоги.
   Подключается после teacher.js, schedule.js и m-render.js (лица настроения).
   Данные — /api/monitoring/…; ученики отвечают на странице /m/<код школы>. */

const MON_TIMES = [5, 10, 15, 20, 45, null];          // null — до ручного закрытия
const MON_TYPES = {
  mood: "Настроение", scale: "Шкала", light: "Светофор", single: "Один вариант",
  multi: "Несколько вариантов", short: "Короткий ответ", long: "Развёрнутый ответ",
};
const LIVE_POLL = 2000;

let monLibrary = null;
let monLink = null;
let monRuns = { open: [], recent: [] };
let monView = "home";              // home | results
let monResults = null;
let monTicket = 0;
let monSchool = null;              // школа, для которой открыты итоги

let launchSet = null;
let launchClasses = new Set();
let launchMinutes = 5;

let liveRunId = null;
let liveData = null;
let liveAt = 0;                    // когда пришли данные: таймер считает от этого момента
let liveTimer = null;
let liveTick = null;
let liveShape = "";                // что нарисовано: перерисовываем каркас, только если он сменился
let liveListHtml = "";

/* ── Загрузка и главный экран ─────────────────────────────── */

async function loadMonitor() {
  if (!$("monHost")) return;
  if (!activeSchool) {
    $("monLink").innerHTML = "";
    $("monHost").innerHTML = `<div class="card empty">${icon("school", "ic ic-xl")}<b>Школ пока нет</b></div>`;
    return;
  }
  const ticket = ++monTicket;
  const school = activeSchool;
  if (monSchool !== school) { monView = "home"; monResults = null; monSchool = school; }
  try {
    const [library, link, runs] = await Promise.all([
      monLibrary || api("/api/monitoring/library"),
      api(`/api/monitoring/link?school_id=${school}`),
      api(`/api/monitoring/runs?school_id=${school}`),
    ]);
    if (ticket !== monTicket) return;                  // пока грузили, выбрали другую школу
    monLibrary = library;
    monLink = link;
    monRuns = runs;
    renderMonLink();
    if (monView === "home") renderMonHome();
  } catch (error) {
    if (ticket === monTicket) $("monHost").innerHTML = `<div class="card empty">${escapeHtml(error.message)}</div>`;
  }
}

function monUrlText(url) { return String(url || "").replace(/^https?:\/\//, ""); }
function monQrUrl(size = 10) { return `${BASE}/api/monitoring/link/qr.png?school_id=${activeSchool}&size=${size}`; }

function renderMonLink() {
  $("monLink").innerHTML = monLink ? `
    <span class="caps">Ссылка для учеников</span>
    <span class="mon-url mono">${escapeHtml(monUrlText(monLink.url))}</span>
    <button class="icon-btn boxed" title="Скопировать ссылку" aria-label="Скопировать ссылку" onclick="copyMonLink()">${icon("copy", "ic ic-sm")}</button>
    <button class="icon-btn boxed" title="QR-код ссылки" aria-label="QR-код ссылки" onclick="showMonQr()">${icon("qr", "ic ic-sm")}</button>` : "";
}

async function copyMonLink() {
  try {
    await navigator.clipboard.writeText(monLink.url);
    toast("Ссылка скопирована", "success");
  } catch (_) {
    showMonQr();
  }
}

function showMonQr() {
  showInfo("Ссылка для учеников", `
    <div class="mon-qr">
      <img src="${monQrUrl(10)}" alt="QR-код ссылки ${escapeHtml(monUrlText(monLink.url))}" width="240" height="240">
      <b class="mono">${escapeHtml(monUrlText(monLink.url))}</b>
      <p class="muted">Ссылка постоянная — её можно повесить в кабинете. Пока опроса нет, ученики видят
        «Подожди, учитель скоро начнёт», а после запуска вопросы открываются сами.</p>
    </div>`);
}

function monClasses(run) {
  return run.all_classes ? "все классы" : run.classes.map((c) => c.name).join(", ");
}

function monLeft(run) {
  if (!run.open) return "завершён";
  if (run.seconds_left == null) return "до закрытия вручную";
  const left = Math.max(0, run.seconds_left);
  return `осталось ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

function libraryQuestion(id) { return monLibrary.questions.find((q) => q.id === id); }

function monShortText(text) {
  return text.replace("«{тема}»", "урока").replace("{цель}", "цель урока");
}

function renderMonHome() {
  const { open, recent } = monRuns;
  $("monHost").innerHTML = `
    ${open.length ? `<h2 class="eyebrow">Идут сейчас</h2>
      <div class="mon-open">${open.map(monOpenCard).join("")}</div>` : ""}
    <h2 class="eyebrow">Стандартные наборы</h2>
    <div class="mon-sets">${monLibrary.sets.map(monSetCard).join("")}</div>
    <h2 class="eyebrow">Прошедшие опросы</h2>
    ${recent.length ? `<div class="card mon-recent">${recent.map(monRecentRow).join("")}</div>`
      : `<p class="muted mon-none">Здесь появятся проведённые опросы — с итогами по каждому вопросу.</p>`}`;
}

function monSetCard(set) {
  const n = set.questions.length;
  return `<article class="card mon-set">
    <div class="mon-set-head"><h3>${escapeHtml(set.title)}</h3>
      <span class="tag">${n} ${plural(n, "вопрос", "вопроса", "вопросов")}</span></div>
    <p class="muted">${escapeHtml(set.about)}</p>
    <ol class="mon-qs">${set.questions.map((id) => {
      const q = libraryQuestion(id);
      return `<li><span class="mon-std">${escapeHtml(q.std)}</span><span>${escapeHtml(monShortText(q.text))}</span></li>`;
    }).join("")}</ol>
    <div class="mon-set-foot">
      <span class="muted">${set.uses_goals ? "нужны цели урока" : `${set.minutes} мин`}</span>
      <button class="btn" onclick="openLaunch('${set.key}')">${icon("play", "ic ic-sm")}Запустить</button>
    </div>
  </article>`;
}

function monOpenCard(run) {
  return `<article class="card mon-run">
    <div class="mon-run-main">
      <b>${escapeHtml(run.title)}</b>
      <span class="muted">${escapeHtml(monClasses(run))}${run.topic ? ` · «${escapeHtml(run.topic)}»` : ""}</span>
    </div>
    <div class="mon-run-stats">
      <span><b class="num">${run.finished}</b> ${plural(run.finished, "ответил", "ответили", "ответили")}</span>
      <span class="muted">${monLeft(run)}</span>
    </div>
    <div class="mon-run-acts">
      <button class="btn" onclick="openLive(${run.id})">${icon("maximize", "ic ic-sm")}Экран для класса</button>
      <button class="btn btn-ghost" onclick="openMonResults(${run.id})">Итоги</button>
      <button class="btn btn-danger-quiet" onclick="closeRun(${run.id})">${icon("stop", "ic ic-sm")}Завершить</button>
    </div>
  </article>`;
}

function monRecentRow(run) {
  return `<div class="mon-row">
    <span class="mon-date">${escapeHtml(formatDay(run.lesson_date, { year: false }))}</span>
    <span class="mon-row-title"><b>${escapeHtml(run.title)}</b>
      <small class="muted">${escapeHtml(monClasses(run))}${isAdmin() ? ` · ${escapeHtml(run.owner_name)}` : ""}</small></span>
    <span class="mon-row-n"><b class="num">${run.finished}</b> <span class="muted">${plural(run.finished, "ответил", "ответили", "ответили")}</span></span>
    <button class="btn btn-ghost btn-sm" onclick="openMonResults(${run.id})">${icon("chart", "ic ic-sm")}Итоги</button>
  </div>`;
}

/* ── Запуск ───────────────────────────────────────────────── */

function busyClassIds() {
  return new Set(monRuns.open.flatMap((r) => r.classes.map((c) => c.id)));
}

/** Класс по умолчанию: тот, у кого сейчас занятие по расписанию, иначе выбранный в журнале. */
function defaultLaunchClasses() {
  const now = new Date();
  const minutes = now.getHours() * 60 + now.getMinutes();
  const toMin = (hm) => { const [h, m] = hm.split(":").map(Number); return h * 60 + m; };
  const weekday = isoWeekday(isoDate(now));
  const near = schoolSchedule()
    .filter((e) => e.weekday === weekday && toMin(e.start) - 15 <= minutes && minutes <= toMin(e.end) + 15)
    .map((e) => e.class_id);
  const busy = busyClassIds();
  return [...new Set(near.length ? near : [activeClass])].filter((id) => id && !busy.has(id));
}

function openLaunch(key) {
  launchSet = monLibrary.sets.find((s) => s.key === key);
  if (!launchSet) return;
  if (!sortedClasses(activeSchool).length) return toast("Сначала добавьте классы", "error");
  launchMinutes = launchSet.minutes;
  launchClasses = new Set(defaultLaunchClasses());
  $("launchTitle").textContent = `Запуск: ${launchSet.title}`;
  $("launchSub").textContent = launchSet.about;
  $("launchTopicField").hidden = !launchSet.uses_topic;
  $("launchGoalsField").hidden = !launchSet.uses_goals;
  $("launchTopic").value = "";
  $("launchGoals").value = "";
  $("launchCode").checked = false;
  renderLaunchClasses();
  renderLaunchTime();
  openModal("modalLaunch");
}

function renderLaunchClasses() {
  const busy = busyClassIds();
  const classes = sortedClasses(activeSchool);
  $("launchClasses").innerHTML = classes.map((c) => {
    const on = launchClasses.has(c.id);
    const taken = busy.has(c.id);
    return `<button type="button" class="${on ? "on" : ""}" aria-pressed="${on}" ${taken ? "disabled" : ""}
      title="${taken ? "У класса уже идёт опрос" : ""}" onclick="toggleLaunchClass(${c.id})">${escapeHtml(c.name)}</button>`;
  }).join("");
  const hints = [];
  if (!launchClasses.size) hints.push("Не выбрано ни одного класса — опрос пойдёт для всех классов школы.");
  if (classes.some((c) => busy.has(c.id))) hints.push("Серые — у них уже идёт опрос.");
  $("launchClassesHint").textContent = hints.join(" ");
  $("launchClassesHint").hidden = !hints.length;
}

function toggleLaunchClass(id) {
  if (launchClasses.has(id)) launchClasses.delete(id); else launchClasses.add(id);
  renderLaunchClasses();
}

function renderLaunchTime() {
  $("launchTime").innerHTML = MON_TIMES.map((m) => `<button type="button" role="radio" aria-checked="${m === launchMinutes}"
    onclick="pickLaunchTime(${m})">${m ? `${m} мин` : "До закрытия"}</button>`).join("");
}

function pickLaunchTime(minutes) {
  launchMinutes = minutes;
  renderLaunchTime();
}

async function launchRun() {
  const goals = $("launchGoals").value.split("\n").map((g) => g.trim()).filter(Boolean);
  if (launchSet.uses_goals && !goals.length) {
    $("launchGoals").focus();
    return toast("Впишите хотя бы одну цель урока", "error");
  }
  const button = $("launchBtn");
  button.disabled = true;
  try {
    const res = await api("/api/monitoring/runs", {
      method: "POST",
      json: {
        school_id: activeSchool, set_key: launchSet.key, class_ids: [...launchClasses],
        topic: launchSet.uses_topic ? $("launchTopic").value.trim() : "", goals: goals.slice(0, 5),
        minutes: launchMinutes, use_code: $("launchCode").checked,
      },
    });
    closeModal("modalLaunch");
    loadMonitor();
    openLive(res.run.id);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function closeRun(id) {
  confirmAction("Завершить опрос?", "Новые ответы больше не примутся. Всё, что ученики уже ответили, останется в итогах.",
    async () => {
      await api(`/api/monitoring/runs/${id}/close`, { method: "POST" });
      if (liveRunId === id) refreshLive(); else loadMonitor();
      toast("Опрос завершён", "success");
    }, "Завершить");
}

/* ── Экран для класса ─────────────────────────────────────── */

function openLive(id) {
  liveRunId = id;
  liveData = null;
  liveShape = "";
  liveListHtml = "";
  $("liveScreen").innerHTML = `<div class="live-loading">Открываем…</div>`;
  $("liveScreen").hidden = false;
  document.body.classList.add("live-open");
  clearInterval(liveTick);
  liveTick = setInterval(tickLive, 1000);
  refreshLive();
}

function closeLive() {
  liveRunId = null;
  clearTimeout(liveTimer);
  clearInterval(liveTick);
  $("liveScreen").hidden = true;
  $("liveScreen").innerHTML = "";
  document.body.classList.remove("live-open");
  const params = new URLSearchParams(location.search);
  if (params.has("live")) history.replaceState(null, "", location.pathname);
  if (currentPage() === "monitor") loadMonitor();
}

/** /teacher?live=12 — экран класса в отдельном окне (для проектора). */
function openLiveFromAddress() {
  const id = parseInt(new URLSearchParams(location.search).get("live"), 10);
  if (!id || !canEdit()) return;
  switchPage("monitor");
  openLive(id);
}

async function refreshLive() {
  clearTimeout(liveTimer);
  const id = liveRunId;
  if (!id) return;
  try {
    const data = await api(`/api/monitoring/runs/${id}`);
    if (id !== liveRunId) return;
    liveData = data;
    liveAt = Date.now();
    renderLive();
  } catch (error) {
    if (error.status === 404) { closeLive(); return toast("Опрос не найден", "error"); }
  }
  if (liveRunId === id) liveTimer = setTimeout(refreshLive, liveData?.open ? LIVE_POLL : 10000);
}

function liveSecondsLeft() {
  if (!liveData || liveData.seconds_left == null) return null;
  return Math.max(0, Math.round(liveData.seconds_left - (Date.now() - liveAt) / 1000));
}

function liveTimerText() {
  if (!liveData.open) return "Опрос завершён";
  const left = liveSecondsLeft();
  if (left == null) return "Без ограничения";
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

function tickLive() {
  if (!liveData || !$("liveTimer")) return;
  $("liveTimer").textContent = liveTimerText();
  const left = liveSecondsLeft();
  $("liveTimer").classList.toggle("soon", liveData.open && left != null && left <= 60);
  if (liveData.open && left === 0) refreshLive();           // окно вышло — узнаём у сервера
}

function renderLive() {
  const d = liveData;
  const shape = JSON.stringify([d.id, d.open, d.stopped, d.code, d.link?.url]);
  if (shape !== liveShape) {
    liveShape = shape;
    liveListHtml = "";
    $("liveScreen").innerHTML = `
      <div class="live-top">
        <div class="live-head">
          <span class="caps">${d.open ? "Идёт опрос" : "Опрос завершён"}</span>
          <h2 id="liveTitle">${escapeHtml(d.title)}</h2>
          <span class="muted">${escapeHtml(monClasses(d))}${d.topic ? ` · «${escapeHtml(d.topic)}»` : ""}</span>
        </div>
        <span class="live-timer ${d.open ? "" : "ended"}" id="liveTimer" role="timer" aria-live="off">${liveTimerText()}</span>
        <div class="live-acts">
          ${!d.stopped && d.seconds_left !== null ? `<button class="btn btn-ghost" onclick="extendRun()">${icon("plus", "ic ic-sm")}5 минут</button>` : ""}
          ${d.open ? `<button class="btn btn-danger" onclick="closeRun(${d.id})">${icon("stop", "ic ic-sm")}Завершить</button>` : ""}
          <button class="btn ${d.open ? "btn-ghost" : ""}" onclick="liveToResults()">${icon("chart", "ic ic-sm")}Итоги</button>
          <button class="icon-btn boxed" title="Открыть в отдельном окне — для проектора" aria-label="Открыть в отдельном окне"
            onclick="window.open('${BASE}/teacher?live=${d.id}', '_blank')">${icon("maximize", "ic ic-sm")}</button>
          <button class="icon-btn boxed" title="Свернуть (Esc)" aria-label="Свернуть" onclick="closeLive()">${icon("close", "ic ic-sm")}</button>
        </div>
      </div>
      <div class="live-body">
        <section class="live-join" aria-label="Как подключиться">
          <span class="caps">Откройте на телефоне</span>
          <b class="live-url">${escapeHtml(monUrlText(d.link?.url))}</b>
          <img class="live-qr" src="${monQrUrl(12)}" alt="QR-код ссылки ${escapeHtml(monUrlText(d.link?.url))}">
          ${d.code ? `<div class="live-code"><span class="caps">Код урока</span><b class="num">${escapeHtml(d.code)}</b></div>` : ""}
        </section>
        <section class="live-people" aria-label="Участники">
          <div class="live-count" id="liveCount"></div>
          <div class="live-list" id="liveList"></div>
        </section>
      </div>`;
  }
  const answering = d.joined - d.finished;
  $("liveCount").innerHTML = `<b class="num">${d.finished}</b>
    <span>${plural(d.finished, "ответил", "ответили", "ответили")}</span>
    ${answering ? `<span class="muted">· ещё отвечают ${answering}</span>` : ""}`;
  const list = d.participants.length ? d.participants.map((p) => livePerson(p, d.total_questions)).join("")
    : `<p class="live-empty">Пока никто не вошёл. Ученики появятся здесь, как только откроют ссылку.</p>`;
  if (list !== liveListHtml) {                       // не трогаем список без изменений — фокус не сбивается
    liveListHtml = list;
    $("liveList").innerHTML = list;
  }
}

function livePerson(p, total) {
  return `<div class="live-p ${p.finished ? "done" : ""} ${p.duplicate ? "dup" : ""}">
    <span class="live-p-name" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</span>
    <span class="live-p-meta">${escapeHtml(p.class_name)}
      ${p.guest ? `<span class="tag" title="Нет в списке класса">гость</span>` : ""}
      ${p.duplicate ? `<span class="tag tag-star" title="Это имя вошло несколько раз">повтор</span>` : ""}</span>
    <span class="live-p-state" title="${p.finished ? "Ответы отправлены" : `Ответил на ${p.answered} из ${total}`}">
      ${p.finished ? icon("check", "ic ic-sm") : `${p.answered}/${total}`}</span>
    <button class="icon-btn" title="Убрать из опроса" aria-label="Убрать ${escapeHtml(p.name)} из опроса"
      onclick="removeParticipant(${p.id})">${icon("close", "ic ic-sm")}</button>
  </div>`;
}

async function extendRun() {
  try {
    await api(`/api/monitoring/runs/${liveRunId}/extend`, { method: "POST", json: { minutes: 5 } });
    refreshLive();
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeParticipant(pid) {
  const person = liveData?.participants.find((p) => p.id === pid);
  if (!person) return;
  confirmAction("Убрать из опроса?", `${escapeHtml(person.name)}: ответы удалятся. Ученик сможет войти заново.`, async () => {
    await api(`/api/monitoring/runs/${liveRunId}/participants/${pid}`, { method: "DELETE" });
    refreshLive();
  }, "Убрать");
}

function liveToResults() {
  const id = liveRunId;
  closeLive();
  switchPage("monitor");
  openMonResults(id);
}

/* ── Итоги ────────────────────────────────────────────────── */

async function openMonResults(id) {
  monView = "results";
  $("monHost").innerHTML = `<div class="card empty">Считаем…</div>`;
  window.scrollTo(0, 0);
  try {
    monResults = await api(`/api/monitoring/runs/${id}/results`);
    renderMonResults();
  } catch (error) {
    toast(error.message, "error");
    backToMon();
  }
}

function backToMon() {
  monView = "home";
  monResults = null;
  loadMonitor();
}

function renderMonResults() {
  const r = monResults;
  $("monHost").innerHTML = `
    <div class="mon-res-head">
      <button class="btn btn-ghost btn-sm" onclick="backToMon()">${icon("arrow-left", "ic ic-sm")}Все опросы</button>
      <div class="mon-res-title">
        <h2>${escapeHtml(r.title)}</h2>
        <p class="muted">${escapeHtml(formatDay(r.lesson_date, { weekday: true }))} · ${escapeHtml(monClasses(r))}${
          r.topic ? ` · «${escapeHtml(r.topic)}»` : ""}${isAdmin() ? ` · ${escapeHtml(r.owner_name)}` : ""}</p>
      </div>
      <div class="mon-res-stats">
        <span><b class="num">${r.finished}</b> ${plural(r.finished, "ответил", "ответили", "ответили")}</span>
        ${r.joined > r.finished ? `<span class="muted">не закончили ${r.joined - r.finished}</span>` : ""}
      </div>
      ${r.open ? `<button class="btn btn-ghost" onclick="openMonResults(${r.id})">${icon("refresh", "ic ic-sm")}Обновить</button>
        <button class="btn" onclick="openLive(${r.id})">${icon("maximize", "ic ic-sm")}Экран для класса</button>` : ""}
    </div>
    ${r.goals?.length ? `<p class="mon-goals"><span class="caps">Цели урока</span>${r.goals.map((g) => escapeHtml(g)).join(" · ")}</p>` : ""}
    <div class="mon-res-grid">${r.results.map(monResultCard).join("")}</div>`;
}

function monResultCard(item) {
  const n = item.answered;
  const max = item.type === "mood" ? 5 : item.scale?.max;
  return `<section class="card mon-q">
    <div class="mon-q-head">
      ${item.std ? `<span class="mon-std">${escapeHtml(item.std)}</span>` : ""}
      <span class="caps">${MON_TYPES[item.type] || ""}</span>
      <span class="grow"></span>
      <span class="muted">${n} ${plural(n, "ответ", "ответа", "ответов")}</span>
    </div>
    <h3 class="mon-q-text">${escapeHtml(item.text)}</h3>
    ${item.average != null ? `<p class="mon-avg">Среднее <b class="num">${item.average.toLocaleString("ru-RU")}</b> из ${max}</p>` : ""}
    ${item.options && n ? monBars(item) : ""}
    ${item.texts ? monTexts(item) : ""}
    ${!n ? `<p class="muted">Пока никто не ответил.</p>` : ""}
  </section>`;
}

/** Доли ответов: одна серия — один цвет; у светофора — цвета состояний с подписью. */
function monBars(item) {
  const n = item.answered;
  return `<div class="mon-bars">${item.options.map((o) => {
    const pct = n ? Math.round((o.count / n) * 100) : 0;
    const marker = item.type === "mood" ? `<span class="mon-face mood-${o.key}">${faceSvg(o.key)}</span>`
      : item.type === "light" ? `<span class="mon-lamp ${o.key}" aria-hidden="true"></span>` : "";
    return `<div class="mon-bar-row ${item.type === "light" ? `light-${o.key}` : ""}" role="img"
        aria-label="${escapeHtml(o.label)}: ${o.count} (${pct}%)">
      <span class="mon-bar-label">${marker}<span>${escapeHtml(o.label)}</span></span>
      <span class="mon-bar-track"><i style="width:${pct}%"></i></span>
      <span class="mon-bar-num"><b class="num">${o.count}</b><small>${pct}%</small></span>
    </div>`;
  }).join("")}</div>`;
}

function monTexts(item) {
  const title = item.type === "single" || item.type === "multi" ? `<span class="caps">Свои варианты</span>` : "";
  return `${title}<ul class="mon-texts">${item.texts.map((t) => `<li><p>${escapeHtml(t.text)}</p>
    <small class="muted">${escapeHtml(t.name)} · ${escapeHtml(t.class_name)}</small></li>`).join("")}</ul>`;
}
