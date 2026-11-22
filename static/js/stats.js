/* Вкладка «Статистика»: итоги школы за период, звёзды по неделям, классы,
   рейтинг учеников (школа · параллель · класс), «давно без звёзд», учителя.
   Видна всем ролям панели, каждому — по его школам. Подключается после teacher.js. */

let statsDays = 30;            // 7, 30, 90 или null — всё время
let statsData = null;
let statsScope = "school";     // рейтинг: school | parallel | class
let statsScopeValue = null;    // номер параллели или id класса
let statsRankBy = "earned";    // earned — за всё время, period — за выбранный период
let statsLoading = 0;

function setStatsPeriod(days) {
  statsDays = days;
  loadStats();
}

async function loadStats() {
  const host = $("statsHost");
  if (!host) return;
  document.querySelectorAll("#statsPeriod button").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.days === String(statsDays ?? ""))));
  if (!activeSchool) {
    host.innerHTML = `<div class="card empty">${icon("school", "ic ic-xl")}<b>Школ пока нет</b></div>`;
    return;
  }
  const ticket = ++statsLoading;
  if (!statsData || statsData.school_id !== activeSchool) host.innerHTML = `<div class="card empty">Считаем…</div>`;
  try {
    const query = `/api/stats?school_id=${activeSchool}${statsDays ? `&days=${statsDays}` : ""}`;
    const data = await api(query);
    if (ticket !== statsLoading) return;            // пока считали, выбрали другой период или школу
    statsData = data;
    renderStats();
  } catch (error) {
    host.innerHTML = `<div class="card empty">${escapeHtml(error.message)}</div>`;
  }
}

function periodWord() {
  return statsDays ? `за ${statsDays} ${plural(statsDays, "день", "дня", "дней")}` : "за всё время";
}

function renderStats() {
  const d = statsData;
  const t = d.totals;
  const school = schoolById(d.school_id);
  $("statsSub").textContent = `${school?.name || ""} · ${periodWord()}`;
  const lessonsText = t.planned ? `${t.lessons} из ${t.planned}` : String(t.lessons);
  const share = t.students ? Math.round((t.active / t.students) * 100) : 0;

  $("statsHost").innerHTML = `
    <div class="tiles stat-tiles">
      <div class="tile"><span>Учеников</span><b>${fmt(t.students)}</b><small>получали звёзды: ${fmt(t.active)} (${share}%)</small></div>
      <div class="tile"><span>Начислено звёзд</span><b>${fmt(t.awarded)} ★</b><small>${periodWord()}</small></div>
      <div class="tile"><span>Потрачено на призы</span><b>${fmt(t.spent)} ★</b><small>выдано призов: ${fmt(t.prizes)}</small></div>
      <div class="tile"><span>Уроков со звёздами</span><b>${lessonsText}</b><small>${t.planned ? "проведено из запланированных" : "по расписанию не заполнено"}</small></div>
      <div class="tile"><span>Заходили в кабинет</span><b>${fmt(t.seen)}</b><small>учеников ${periodWord()}</small></div>
    </div>

    <section class="card stat-card">
      <h2 class="eyebrow">Звёзды по неделям</h2>
      ${weekBars(d.weeks, { tall: true })}
    </section>

    <div class="stat-cols">
      <section class="card stat-card">
        <h2 class="eyebrow">Классы</h2>
        ${classTable(d.classes)}
      </section>
      <section class="card stat-card">
        <h2 class="eyebrow">Давно без звёзд</h2>
        ${idleList(d.students)}
      </section>
    </div>

    <section class="card stat-card">
      <div class="stat-head">
        <h2 class="eyebrow">Рейтинг учеников</h2>
        <div class="seg" role="radiogroup" aria-label="Уровень">
          ${[["school", "Школа"], ["parallel", "Параллель"], ["class", "Класс"]].map(([k, n]) =>
            `<button role="radio" aria-checked="${statsScope === k}" onclick="setStatsScope('${k}')">${n}</button>`).join("")}
        </div>
        ${scopeSelect(d)}
        <div class="seg" role="radiogroup" aria-label="Считать">
          <button role="radio" aria-checked="${statsRankBy === "earned"}" onclick="setStatsRankBy('earned')">Всё время</button>
          <button role="radio" aria-checked="${statsRankBy === "period"}" onclick="setStatsRankBy('period')">${statsDays ? `${statsDays} дн.` : "Период"}</button>
        </div>
      </div>
      ${rankingTable(d)}
    </section>

    ${d.teachers.length && !(state.me?.role === "teacher") ? `
    <section class="card stat-card">
      <h2 class="eyebrow">Учителя</h2>
      <div class="st-table">
        <div class="st-row st-headrow"><span>Учитель</span><span class="r">Уроков</span><span class="r">Звёзд</span></div>
        ${d.teachers.map((row) => `<div class="st-row"><span>${escapeHtml(row.name || "не указан")}</span>
          <span class="r num">${row.lessons}</span><span class="r num">${fmt(row.stars)}</span></div>`).join("")}
      </div>
    </section>` : ""}`;
}

function classTable(classes) {
  if (!classes.length) return `<p class="muted">Классов пока нет.</p>`;
  let lastParallel;
  const rows = classes.map((c) => {
    const head = c.parallel !== lastParallel
      ? `<div class="st-group">${c.parallel ? `${c.parallel} параллель` : "Без параллели"}</div>` : "";
    lastParallel = c.parallel;
    const lessons = c.planned ? `${c.lessons}<small> из ${c.planned}</small>` : String(c.lessons);
    return `${head}<div class="st-row">
      <button class="link" onclick="openStatsClass(${c.id})">${escapeHtml(c.name)}</button>
      <span class="r num">${c.students}</span><span class="r num">${fmt(c.stars)}</span>
      <span class="r num">${c.avg}</span><span class="r num">${lessons}</span></div>`;
  }).join("");
  return `<div class="st-table cls">
    <div class="st-row st-headrow"><span>Класс</span><span class="r">Учеников</span><span class="r">Звёзд</span>
      <span class="r" title="Звёзд в среднем на ученика">В среднем</span><span class="r">Уроков</span></div>${rows}</div>`;
}

function idleList(students) {
  const idle = students.filter((s) => s.idle).sort((a, b) => b.idle_lessons - a.idle_lessons);
  if (!idle.length) return `<p class="muted">Все ученики получали звёзды на последних уроках.</p>`;
  return `<p class="muted st-note">${IDLE_LESSONS} и больше уроков подряд без звёзд. Повод поговорить с учеником — ребёнку эта метка не показывается.</p>
    <div class="st-table">${idle.slice(0, 12).map((s) => `<div class="st-row">
      <button class="link" onclick="openActivity(${s.id})">${escapeHtml(s.name)}</button>
      <span class="muted">${escapeHtml(s.class_name)}</span>
      <span class="r">${s.idle_lessons} ${plural(s.idle_lessons, "урок", "урока", "уроков")}</span></div>`).join("")}
    </div>${idle.length > 12 ? `<p class="muted st-note">и ещё ${idle.length - 12}</p>` : ""}`;
}

function scopeSelect(d) {
  if (statsScope === "parallel") {
    const grades = [...new Set(d.classes.map((c) => c.parallel).filter(Boolean))].sort((a, b) => a - b);
    if (!grades.includes(statsScopeValue)) statsScopeValue = grades[0] ?? null;
    return `<select aria-label="Параллель" onchange="setStatsScopeValue(this.value)">${grades.map((g) =>
      `<option value="${g}" ${g === statsScopeValue ? "selected" : ""}>${g} параллель</option>`).join("")}</select>`;
  }
  if (statsScope === "class") {
    if (!d.classes.some((c) => c.id === statsScopeValue)) statsScopeValue = d.classes[0]?.id ?? null;
    return `<select aria-label="Класс" onchange="setStatsScopeValue(this.value)">${d.classes.map((c) =>
      `<option value="${c.id}" ${c.id === statsScopeValue ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select>`;
  }
  return "";
}

function rankingTable(d) {
  let list = d.students.filter((s) => !s.hidden);
  if (statsScope === "parallel") list = list.filter((s) => s.parallel === statsScopeValue);
  if (statsScope === "class") list = list.filter((s) => s.class_id === statsScopeValue);
  const key = statsRankBy === "period" ? "period" : "earned";
  list.sort((a, b) => b[key] - a[key] || a.name.localeCompare(b.name, "ru"));
  if (!list.length) return `<p class="muted">Здесь пока никого нет.</p>`;
  const max = Math.max(1, list[0][key]);
  return `<div class="st-table rank">${list.slice(0, 30).map((s, i) => `
    <div class="st-row">
      <span class="num st-place">${i + 1}</span>
      <img src="${escapeHtml(avatarUrl(s))}" alt="" loading="lazy">
      <button class="link" onclick="openActivity(${s.id})">${escapeHtml(s.name)}</button>
      <span class="muted">${escapeHtml(s.class_name)}</span>
      <span class="meter" aria-hidden="true"><i style="width:${Math.round((s[key] / max) * 100)}%"></i></span>
      <span class="r num">${fmt(s[key])} ★</span>
    </div>`).join("")}</div>
    <p class="muted st-note">${statsRankBy === "earned" ? "Заработано за всё время: потраченные на призы звёзды тоже считаются." : `Заработано ${periodWord()}.`}
    ${list.length > 30 ? `Показаны первые 30 из ${list.length}.` : ""}</p>`;
}

function setStatsScope(scope) { statsScope = scope; statsScopeValue = null; renderStats(); }
function setStatsScopeValue(value) { statsScopeValue = parseInt(value); renderStats(); }
function setStatsRankBy(by) { statsRankBy = by; renderStats(); }
function openStatsClass(classId) { statsScope = "class"; statsScopeValue = classId; renderStats(); }
