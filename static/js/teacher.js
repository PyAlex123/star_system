/* Панель учителя: школы, классы, журнал звёзд, карточки с QR, витрина призов,
   уроки с отменой начислений. Вид — по макету docs/design/«Звёзды - Учитель».
   Роли: суперадмин видит всё и управляет школами, учителями и витриной;
   учитель — только свои школы. Сервер проверяет права сам, панель лишь прячет
   то, что пользователю всё равно недоступно.
   Тема оформления — у каждой школы своя: переключили школу — сменилась тема. */

const MAX_LESSON_BANK = 10;
const MAX_STUDENT_STARS = 5;
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
const STAR_POINTS = "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2";
const CATEGORY_NAMES = { small: "Небольшой приз", medium: "Средний приз", large: "Большой приз" };
const PANEL_THEMES = ["sky", "clear", "sticker"];   // где есть боковая панель ученика

let state = { schools: [], classes: [], students: [], prizes: [], lessons: [] };
let activeSchool = null;   // id школы
let activeClass = null;    // id класса
let draft = {};            // { student_id: звёзды за текущий урок }
let jointClasses = null;   // общий урок объединённых классов: [id, …] (2 и больше) или null
let jointPick = new Set(); // окно «Провести вместе с…»
let journalView = "home";  // журнал: "home" — плитки уроков и классов, "class" — журнал класса

let rowPop = null;         // { kind: "code" | "more", id } — открытое окошко в строке
let panelId = null;        // ученик в боковой панели
let showPasswords = false; // пароли скрыты: экран учителя часто выводят на доску
let flippedPasswords = new Set();

let deductTarget = null;
let giveTarget = null;
let givePrize = null;
let accessTarget = null;
let schoolTarget = null;
let prizeTarget = null;
let prizeCategory = "small";
let userTarget = null;
let pickedClasses = new Set();
let lessonClass = null;    // фильтр на вкладке «Уроки»
let openLessons = new Set();
let justDone = null;       // номер урока, только что проведённого в этой вкладке

let prizePhotoFile = null;
let prizePhotoCleared = false;
let prizePhotoPreviewUrl = null;

const toast = makeToast({ duration: 3400 });

/* ── Помощники ────────────────────────────────────────────── */

/** Запрос от имени учителя: сессия в cookie. Истекла — на страницу входа. */
/** Куда вести, когда сессия истекла: панель открыта из Telegram-бота — снова вход по подписи
 *  Telegram (/login?tg=1), без пароля; иначе — обычная форма. */
function loginUrl() {
  let telegram = false;
  try { telegram = sessionStorage.getItem("zv_tg") === "1"; } catch (_) {}
  return `${BASE}/login${telegram ? "?tg=1" : ""}`;
}

async function api(path, options = {}) {
  try {
    return await requestJson(path, options, "Не удалось выполнить действие");
  } catch (error) {
    if (error.status === 401) {
      location.href = loginUrl();
      throw new Error("Сессия истекла");
    }
    // Отказ в правах бывает, когда в другой вкладке вошли под другим логином:
    // вход у браузера один на все вкладки. Тогда страница перестраивается под него.
    if (error.status === 403 && await sessionSwitched()) throw new Error("Вход сменился");
    throw error;
  }
}

/** В другой вкладке вошли под другим логином? Тогда перезагружаем панель под ним
 *  и после перезагрузки объясняем, что случилось. */
async function sessionSwitched() {
  if (!state.me) return false;
  try {
    const fresh = await requestJson("/api/state");
    if (fresh.me && fresh.me.id !== state.me.id) {
      try { sessionStorage.setItem("zv_switched", JSON.stringify({ now: fresh.me.login, was: state.me.login })); } catch (_) {}
      location.reload();
      return true;
    }
  } catch (error) {
    if (error.status === 401) { location.href = loginUrl(); return true; }
  }
  return false;
}

function reportSwitchedLogin() {
  let info = null;
  try { info = JSON.parse(sessionStorage.getItem("zv_switched") || "null"); sessionStorage.removeItem("zv_switched"); } catch (_) {}
  if (!info?.now) return;
  showInfo("Вы вошли под другим логином",
    `<p>Эта вкладка была открыта как <b>${escapeHtml(info.was || "—")}</b>, но в этом окне браузера
     последний вход выполнен как <b>${escapeHtml(info.now)}</b>. Вход один на все вкладки окна
     (и на все вкладки инкогнито), поэтому панель открыта под <b>${escapeHtml(info.now)}</b>.</p>
     <p>Нужен <b>${escapeHtml(info.was || "другой логин")}</b> — войдите заново. Чтобы держать открытыми
     администратора и учителя одновременно, одного из них откройте в обычном окне, другого — в инкогнито.</p>
     <div class="row" style="margin-top:14px"><button class="btn btn-ghost" onclick="logout()">
       ${icon("logout", "ic ic-sm")}Войти другим логином</button></div>`);
}

let lastFocus = null;
function openModal(id) {
  lastFocus = document.activeElement;
  closeMenus();
  $(id).classList.add("open");
  // Фокус — в первое поле; если полей нет — на само окно (Tab пойдёт по его кнопкам)
  setTimeout(() => {
    const target = $(id).querySelector("input:not([type=hidden]):not([type=file]):not([disabled]), textarea");
    if (target) target.focus();
    else { const modal = $(id).querySelector(".modal"); modal.tabIndex = -1; modal.focus(); }
  }, 40);
}
function closeModal(id) {
  $(id).classList.remove("open");
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}
function topModal() {
  const open = [...document.querySelectorAll(".backdrop.open")];
  return open[open.length - 1] || null;
}

function confirmAction(title, text, onConfirm, button = "Да, продолжить") {
  $("confirmTitle").textContent = title;
  $("confirmText").innerHTML = text;     // text собирается только из экранированных частей
  const old = $("confirmBtn");
  const fresh = old.cloneNode(true);     // снимаем прошлые обработчики
  fresh.textContent = button;
  old.replaceWith(fresh);
  fresh.onclick = async () => {
    closeModal("modalConfirm");
    try {
      await onConfirm();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  openModal("modalConfirm");
}

function avatarUrl(student) {
  const avatar = student?.avatar || {};
  if (avatar.type === "preset") return `${BASE}/static/img/avatars/${avatar.value}.svg`;
  if (avatar.value) return assetUrl(avatar.value);
  return `${BASE}/static/img/avatars/fox.svg`;
}

function stars(n) { return plural(n, "звезда", "звезды", "звёзд"); }
function kids(n) { return plural(n, "ученик", "ученика", "учеников"); }
function starSvg() { return `<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="${STAR_POINTS}"/></svg>`; }
function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}
function hasPanel() { return PANEL_THEMES.includes(currentTheme()); }

/* ── Школы и классы ───────────────────────────────────────── */

function schoolById(id) { return state.schools.find((s) => s.id === id) || null; }
function classById(id) { return state.classes.find((c) => c.id === id) || null; }
function studentById(id) { return state.students.find((s) => s.id === id) || null; }
function sortedClasses(schoolId) {
  return state.classes.filter((c) => c.school_id === schoolId)
    .sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
}
function classStudents(classId = activeClass) {
  return state.students.filter((s) => s.class_id === classId)
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));
}

/* Общий урок объединённых классов: журнал показывает учеников всех классов урока
   (по классам), банк звёзд один на урок — как и на сервере (lessons.py). */
function journalClassIds() { return jointClasses || (activeClass ? [activeClass] : []); }
function journalStudents() { return journalClassIds().flatMap((id) => classStudents(id)); }
function jointName() { return combinedName((jointClasses || []).map((id) => classById(id)?.name || "")); }
function jointTitle() { return (jointClasses || []).map((id) => classById(id)?.name || "").join(" + "); }

async function loadState() {
  state = await api("/api/state");

  if (!schoolById(activeSchool)) {
    const remembered = parseInt(getCookie("zv_school"));
    activeSchool = schoolById(remembered) ? remembered : state.schools[0]?.id ?? null;
  }
  const classes = sortedClasses(activeSchool);
  if (!classes.some((c) => c.id === activeClass)) {
    activeClass = classes[0]?.id ?? null;
    draft = {};
    journalView = "home";
  }
  if (!classes.some((c) => c.id === lessonClass)) lessonClass = activeClass;
  if (jointClasses && !jointClasses.every((id) => classes.some((c) => c.id === id))) {
    jointClasses = null;
    draft = {};
    journalView = "home";
  }
  if (panelId && !studentById(panelId)) panelId = null;
  syncSchoolTheme();
  renderAll();
}

/** Тема панели — тема выбранной школы. */
function syncSchoolTheme() {
  const school = schoolById(activeSchool);
  if (school) setPrefCookie("zv_school", school.id);
  if (school?.theme && school.theme !== currentTheme()) applyTheme(school.theme);
}

function isAdmin() { return state.me?.role === "superadmin"; }

/* Должности в школе. У сотрудника в каждой школе свои (можно несколько): права — по должности
   в выбранной школе. Руководство (директор, завучи) — оформление, призы школы, карточки,
   расписание; учитель — журнал, звёзды, опросы. */
const POSTS = { director: "Директор", head_primary: "Завуч начальной школы",
                head_secondary: "Завуч средней школы", teacher: "Учитель" };
const POSTS_SHORT = { director: "директор", head_primary: "завуч нач.", head_secondary: "завуч сред.", teacher: "учитель" };
const ADMIN_POSTS = ["director", "head_primary", "head_secondary"];

/** Должности сотрудника в школе (по умолчанию — в выбранной). */
function postsOf(user, school = activeSchool) { return (user?.posts || {})[String(school)] || []; }
/** Учитель в выбранной школе — журнал и звёзды. Администратору можно всё. */
function teachesHere(school = activeSchool) { return isAdmin() || postsOf(state.me, school).includes("teacher"); }
/** Руководство выбранной школы: директор или завуч. */
function managesHere(school = activeSchool) {
  return !isAdmin() && postsOf(state.me, school).some((p) => ADMIN_POSTS.includes(p));
}
/** «Администрация школы» в выбранной школе — карточки, призы школы (журнал — только если ещё и учитель). */
function isSchoolAdmin() { return managesHere(); }
/** Школа участвует в системе наград. Без неё — расписание, ученики списком, опросы без звёзд
 *  и отметки; звёзд, призов, карточек с QR, уроков и статистики нет. */
function rewardsHere(school = activeSchool) { return schoolById(school)?.rewards !== false; }
const REWARD_PAGES = ["prizes", "cards", "lessons", "stats"];
/** Куда уводить с раздела, которого в этой школе у человека нет. */
function homePage() { return canEdit() ? "journal" : rewardsHere() ? "stats" : "schedule"; }
/** Менять журнал (звёзды, учеников, классы) — учитель этой школы. */
function canEdit() { return teachesHere(); }
/** «учитель · завуч нач.» — должности в школе; у администратора — «администратор». */
function postsLabel(user, school = activeSchool) {
  if (user?.role === "superadmin") return "администратор";
  const posts = postsOf(user, school);
  return posts.length ? posts.map((p) => POSTS_SHORT[p] || p).join(" · ") : "нет должности в этой школе";
}

/** Имя сотрудника: администратору приходит полный список, остальным — коллеги по школам. */
function staffList() { return state.users || state.staff || []; }
function staffName(id) { return staffList().find((u) => u.id === id)?.name || ""; }
function staffById(id) { return staffList().find((u) => u.id === id) || (state.me?.id === id ? state.me : null); }

/** Аватар сотрудника: своё фото или кружок с инициалами. */
function staffAvatar(user, cls = "") {
  const name = user?.name || "";
  const photo = user?.avatar?.type === "photo" && user.avatar.value;
  return photo
    ? `<img class="av-staff ${cls}" src="${escapeHtml(assetUrl(photo))}" alt="" loading="lazy">`
    : `<span class="av-staff av-ini ${cls}" aria-hidden="true">${escapeHtml(initials(name))}</span>`;
}

/** Вход учеников через Telegram-бота включён (настройка администратора). */
function botLogin() { return state.telegram_login !== false; }

/** Что видно — по должности в выбранной школе: «.editor-only» — учителю (журнал),
 *  «.sadmin-only» — руководству школы, «.teacher-only» — учителю без руководства,
 *  «.admin-only» — администратору. Зовётся и при смене школы. */
function applyRole() {
  document.body.classList.toggle("is-admin", isAdmin());
  document.body.classList.toggle("can-teach", teachesHere());
  document.body.classList.toggle("is-teacher", !isAdmin() && teachesHere() && !managesHere());
  document.body.classList.toggle("is-sadmin", managesHere());
  document.body.classList.toggle("can-checkin", canManageCheckins());
  document.body.classList.toggle("no-rewards", !rewardsHere());
  renderMe();
  if (!isAdmin() && ["users", "schools"].includes(currentPage())) showPage(homePage());
  // showPage, а не switchPage: адрес не трогаем — иначе ссылка #prizes или #cards
  // при загрузке перезаписалась бы на #stats раньше, чем её прочитает applyRoute()
  if (!canManageCheckins() && currentPage() === "checkin") showPage(homePage());
  if (!canEdit() && currentPage() === "journal") showPage(homePage());
  if (!isSchoolAdmin() && currentPage() === "cards") showPage(homePage());
  if (!rewardsHere() && REWARD_PAGES.includes(currentPage())) showPage(homePage());
}

function renderAll() {
  applyRole();
  renderSchoolMenu();
  renderJournal();
  renderTable();
  renderPanel();
  renderOrdersPanel();
  renderPrizes();
  renderLessons();
  renderTodayStrip();
  renderSchedule();
  if (currentPage() === "stats") loadStats();
  if (currentPage() === "checkin") loadCheckin();
  else refreshCheckinBadge();
  if (currentPage() === "monitor") loadMonitor();
  if (currentPage() === "lessons") loadLessonRuns();
  if (isAdmin()) { renderUsers(); renderSchoolsPage(); }
  if (currentPage() === "me") renderCabinet();
  if (isSchoolAdmin()) renderCardsPage();
}

function renderSchoolMenu() {
  const school = schoolById(activeSchool);
  const count = sortedClasses(activeSchool).length;
  $("schoolLabel").textContent = school?.name || "Нет школы";
  $("schoolCount").textContent = school ? `${count} ${plural(count, "класс", "класса", "классов")}` : "";
  const items = state.schools.map((s) => `
    <button role="menuitemradio" aria-checked="${s.id === activeSchool}" class="${s.id === activeSchool ? "checked" : ""}"
            onclick="selectSchool(${s.id})"><span>${escapeHtml(s.name)}</span>${s.id === activeSchool ? icon("check", "ic ic-sm") : ""}</button>`);
  if (state.schools.length) items.push(`<div class="menu-sep"></div>`);
  if (school) items.push(`<button role="menuitem" onclick="openTheme()">${icon("palette", "ic ic-sm")}Оформление школы…</button>`);
  // Сотрудники, место на карте, переименование и удаление — в разделе «Школы»
  if (isAdmin()) {
    items.push(`<button role="menuitem" onclick="closeMenus(); ${school ? `openSchoolCard(${school.id})` : "switchPage('schools')"}">${icon("school", "ic ic-sm")}Карточка школы…</button>`);
    items.push(`<button role="menuitem" onclick="closeMenus(); switchPage('schools')">${icon("grid", "ic ic-sm")}Все школы</button>`);
  }
  $("schoolMenuList").innerHTML = items.join("");
  $("pasteSchoolName").textContent = school?.name || "—";
}

function selectSchool(id) {
  closeMenus();
  if (id === activeSchool) return;
  leaveDraftGuard(() => {
    useSchool(id);
    setRoute();
  });
}

/** Сделать школу выбранной: журнал — к плиткам, тема панели — её. В разделе «Школы»
 *  открытая карточка тоже переходит на эту школу. */
function useSchool(id) {
  activeSchool = id;
  jointClasses = null;
  journalView = "home";
  activeClass = sortedClasses(id)[0]?.id ?? null;
  lessonClass = activeClass;
  draft = {};
  panelId = null;
  rowPop = null;
  if (typeof schoolView !== "undefined" && schoolView) schoolView = id;
  syncSchoolTheme();
  renderAll();
}

/* ── Журнал: «Сегодня» (плитки) и журнал класса ───────────── */

function renderJournal() {
  if (journalView === "class" && !classById(activeClass)) journalView = "home";
  const inClass = journalView === "class";
  $("journalHome").hidden = inClass;
  $("journalClass").hidden = !inClass;
  $("bank").hidden = !inClass || !rewardsHere();
  document.querySelector(".j-head").classList.toggle("in-class", inClass);
  renderJournalHead();
  renderJournalHome();
}

/** Заголовок журнала: дата и школа на экране «Сегодня»; «← Все классы», класс и меню ⋯ — в классе. */
function renderJournalHead() {
  const host = $("journalTitle");
  if (!host) return;
  if (journalView !== "class") {
    const school = schoolById(activeSchool);
    const day = lessonDay();
    const isToday = day === localToday();
    const [y, m, d] = day.split("-").map(Number);
    const weekday = new Date(y, m - 1, d).toLocaleDateString("ru-RU", { weekday: "long" });
    const date = formatDay(day, { year: false });
    const count = sortedClasses(activeSchool).length;
    host.innerHTML = `<div class="j-title-text">
      <h1 class="page-title">${isToday ? `Сегодня — ${escapeHtml(weekday)}, ${escapeHtml(date)}`
        : `${escapeHtml(weekday.charAt(0).toUpperCase() + weekday.slice(1))}, ${escapeHtml(date)}`}</h1>
      <p class="page-sub">${school ? `${escapeHtml(school.name)} · ${count} ${plural(count, "класс", "класса", "классов")}` : "Школ пока нет"}${
        isToday ? "" : ` · <button class="btn btn-link btn-sm" onclick="setLessonDate(localToday())">вернуться к сегодня</button>`}</p>
    </div>`;
    return;
  }
  const cls = classById(activeClass);
  const count = journalStudents().length;
  const name = jointClasses ? jointTitle() : cls?.name || "";
  host.innerHTML = `
    <button class="btn btn-ghost btn-md j-back" onclick="goJournalHome()" title="К плиткам уроков и классов (Esc)">
      ${icon("arrow-left", "ic ic-sm")}<span>Все классы</span></button>
    <div class="j-title-text">
      <h1 class="page-title j-class" title="${escapeHtml(name)}">${jointClasses ? icon("users") : ""}<span>${escapeHtml(name)}</span></h1>
      <p class="page-sub">${jointClasses ? "Общий урок · " : ""}${count} ${kids(count)}</p>
    </div>
    ${jointClasses ? "" : `<button class="icon-btn" id="classMenuBtn" onclick="openClassMenu(event)"
        aria-label="Действия с классом ${escapeHtml(name)}" title="Действия с классом"
        aria-haspopup="menu">${icon("more", "ic ic-sm")}</button>`}`;
}

function classStarsOn(classId, day) {
  return (state.lessons || [])
    .filter((l) => l.class_id === classId && l.date === day)
    .reduce((sum, l) => sum + (l.total_stars || 0), 0);
}

function parallelTitle(parallel) {
  if (!parallel) return "Другие";
  return `${parallel}-${parallel === 3 ? "и" : "е"} классы`;
}

const TILE_STATE = { done: "проведён", now: "идёт сейчас", next: "следующий", past: "не отмечен", later: "", gone: "" };

/** Экран «Сегодня»: крупные плитки уроков на выбранную дату и все классы по параллелям. */
function renderJournalHome() {
  const tiles = $("todayTiles");
  if (!tiles || journalView === "class") return;
  const school = schoolById(activeSchool);
  const classes = sortedClasses(activeSchool);
  const day = lessonDay();
  const today = localToday();
  $("todayTitle").textContent = day === today ? "Уроки на сегодня" : `Уроки на ${dotted(day)}`;
  renderBell($("journalBell"), day === today ? school : null);   // звонок — только «на сегодня»
  if (!school) {
    tiles.innerHTML = `<div class="card empty">${icon("school", "ic ic-xl")}${isAdmin()
      ? "<b>Школ пока нет</b>Добавьте первую в меню «Школа»."
      : "<b>Вам пока не открыта ни одна школа</b>Обратитесь к администратору."}</div>`;
    $("classTiles").innerHTML = "";
    return;
  }

  const clock = new Date();
  const nowMinutes = clock.getHours() * 60 + clock.getMinutes();
  const lessons = todayLessons(schoolSchedule().filter((e) => e.weekday === isoWeekday(day) && entryShownOn(e, day)));
  let nextFound = false;
  tiles.innerHTML = lessons.length ? `<div class="tile-grid lessons">${lessons.map((group) => {
    const ids = group.map((e) => e.class_id);
    const first = group[0];
    const together = ids.length > 1;
    let kind = lessonTileState(first.start, first.end, day, today, nowMinutes, ids.every((id) => lessonHeld(id, day)));
    if (kind === "later" && day === today && !nextFound) { kind = "next"; nextFound = true; }
    if (kind === "past" && day < String(first.created_at || "").slice(0, 10)) kind = "later";   // занятия тогда ещё не было
    // «Проведён / не отмечен» — по журналу звёзд; в школе без наград его не ведут
    if (kind === "past" && !rewardsHere()) kind = "gone";
    const room = group.find((e) => e.room)?.room || "";
    const count = ids.reduce((sum, id) => sum + classStudents(id).length, 0);
    const names = group.map((e) => classById(e.class_id)?.name || "").join(", ");
    const label = TILE_STATE[kind];
    return `<button type="button" class="lesson-tile ${kind}"
        onclick="${together ? `openJoint([${ids.join(",")}])` : `selectClass(${ids[0]})`}"
        aria-label="${escapeHtml(first.start)}, ${escapeHtml(names)}${label ? `, ${label}` : ""}. Открыть журнал">
      <span class="lt-time">${escapeHtml(first.start)}<span>–${escapeHtml(first.end)}</span></span>
      <b class="lt-class">${together ? icon("users") : ""}<span>${entriesName(group).split(" + ").map((n) => `<span class="nw">${escapeHtml(n)}</span>`).join(" + ")}</span></b>
      <span class="lt-meta">${[together ? "общий урок" : "", room ? `каб. ${escapeHtml(room)}` : "", `${count} ${kids(count)}`].filter(Boolean).join(" · ")}</span>
      ${label ? `<span class="lt-state">${kind === "done" ? icon("check", "ic ic-xs") : ""}${label}</span>` : ""}
    </button>`;
  }).join("")}</div>`
    : `<div class="j-empty">${icon("calendar")}<span>На эту дату занятий по расписанию нет.</span>
        <button class="btn btn-ghost btn-sm" onclick="switchPage('schedule')">${icon("clock", "ic ic-sm")}Открыть расписание</button></div>`;

  const order = new Map(classes.map((c, i) => [c.id, i]));
  const add = `<button type="button" class="class-tile add" onclick="openClassGrid()">${icon("plus")}<span>Добавить классы</span></button>`;
  $("classTiles").innerHTML = classes.length
    ? `<div class="par-grid">` + groupByParallel(classes).map((group) => `
      <div class="par-row">
        <span class="par-name">${parallelTitle(group.parallel)}</span>
        <div class="tile-grid classes">${group.classes.map((c) => {
          const count = classStudents(c.id).length;
          const got = classStarsOn(c.id, day);
          return `<button type="button" class="class-tile c${order.get(c.id) % 3}" onclick="selectClass(${c.id})"
              aria-label="${escapeHtml(c.name)}, ${count} ${kids(count)}. Открыть журнал">
            <b>${escapeHtml(c.name)}</b>
            <span class="ct-count">${count ? `${count} ${kids(count)}` : "нет учеников"}</span>
            ${got ? `<span class="ct-got" title="Начислено за ${dotted(day)}">${icon("star", "ic ic-xs")}+${got}</span>` : ""}
          </button>`;
        }).join("")}</div>
      </div>`).join("") + `<div class="par-row"><span class="par-name"></span><div class="tile-grid classes">${add}</div></div></div>`
    : `<div class="card empty">${icon("users", "ic ic-xl")}<b>В школе ${quoted(escapeHtml(school.name))} пока нет классов</b>
        <button class="btn btn-md" onclick="openClassGrid()">${icon("plus", "ic ic-sm")}Добавить классы</button></div>`;
}

/* ── Карточки учеников (администрация школы) ──────────────── */

/** Классы плитками по параллелям; плитка открывает карточки для печати в новой вкладке. */
function renderCardsPage() {
  const host = $("cardsTiles");
  if (!host) return;
  const classes = sortedClasses(activeSchool);
  if (!classes.length) {
    host.innerHTML = `<div class="card empty">${icon("users", "ic ic-xl")}<b>В школе пока нет классов</b></div>`;
    return;
  }
  const order = new Map(classes.map((c, i) => [c.id, i]));
  host.innerHTML = `<div class="par-grid">` + groupByParallel(classes).map((group) => `
    <div class="par-row">
      <span class="par-name">${parallelTitle(group.parallel)}</span>
      <div class="tile-grid classes">${group.classes.map((c) => {
        const count = classStudents(c.id).length;
        return `<a class="class-tile c${order.get(c.id) % 3}" href="${BASE}/cards/${c.id}" target="_blank" rel="noopener"
            aria-label="Карточки ${escapeHtml(c.name)}, ${count} ${kids(count)}">
          <b>${escapeHtml(c.name)}</b>
          <span class="ct-count">${count ? `${count} ${kids(count)}` : "нет учеников"}</span>
          <span class="ct-print">${icon("printer", "ic ic-xs")}</span>
        </a>`;
      }).join("")}</div>
    </div>`).join("") + "</div>";
}

function setLessonDate(iso) {
  $("lessonDate").value = iso;
  onLessonDateChange();
}

/** Звёзды поставлены, но урок не завершён: прежде чем уйти из класса — спросить.
 *  next() выполняется сразу, если терять нечего. */
function leaveDraftGuard(next) {
  const sum = journalView === "class" ? usedBank() : 0;
  if (!sum) return next();
  confirmAction("Звёзды не начислены",
    `Вы поставили ${sum} ${stars(sum)}, но урок не завершён. Если уйти, они пропадут.`,
    async () => { draft = {}; next(); }, "Уйти без начисления");
}

/** Назад к плиткам уроков и классов. */
function goJournalHome() {
  if (journalView !== "class") return;
  leaveDraftGuard(() => {
    jointClasses = null;
    journalView = "home";
    draft = {};
    panelId = null;
    rowPop = null;
    setRoute();
    renderAll();
    window.scrollTo(0, 0);
  });
}

/** Меню класса живёт поверх страницы — как остальные меню «⋯». */
function openClassMenu(event) {
  event.stopPropagation();
  let menu = $("classMenu");
  if (menu?.classList.contains("shown")) { closeMenus(); return; }
  closeMenus();
  if (!menu) {
    menu = document.createElement("div");
    menu.id = "classMenu";
    menu.className = "menu class-menu";
    menu.setAttribute("role", "menu");
    document.body.appendChild(menu);
  }
  menu.innerHTML = `
    <button role="menuitem" onclick="openJointPicker()">${icon("users", "ic ic-sm")}Провести вместе с…</button>
    <button role="menuitem" onclick="renameClass()">${icon("pencil", "ic ic-sm")}Переименовать класс</button>
    <button role="menuitem" class="danger" onclick="clearClass()">${icon("users", "ic ic-sm")}Удалить всех учеников…</button>
    <button role="menuitem" class="danger" onclick="removeClass()">${icon("trash", "ic ic-sm")}Удалить класс…</button>`;
  const rect = event.currentTarget.getBoundingClientRect();
  menu.style.left = `${Math.max(8, rect.left - 8)}px`;
  menu.style.top = `${rect.bottom + 4}px`;
  menu.style.display = "block";
  menu.classList.add("shown");
}

/** Открыть журнал класса (из плитки или полоски уроков дня). */
function selectClass(id) {
  if (journalView === "class" && !jointClasses && activeClass === id) return;
  leaveDraftGuard(() => {
    jointClasses = null;
    activeClass = id;
    lessonClass = id;
    journalView = "class";
    draft = {};
    panelId = null;
    rowPop = null;
    setRoute();
    renderAll();
    window.scrollTo(0, 0);
  });
}

/** «5-А», «5а», «5 А» — один и тот же класс. */
function classKey(name) { return String(name).toLowerCase().replace(/[\s-]/g, ""); }

const GRID_LETTERS = ["А", "Б", "В", "Г", "Д"];

function openClassGrid() {
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  const existing = new Set(sortedClasses(activeSchool).map((c) => classKey(c.name)));
  pickedClasses = new Set();
  $("classGridSchool").textContent = schoolById(activeSchool)?.name || "—";
  $("classOther").value = "";

  let html = `<span></span>` + GRID_LETTERS.map((l) => `<span class="grid-head">${l}</span>`).join("");
  for (let grade = 1; grade <= 11; grade++) {
    html += `<span class="grid-head">${grade}</span>`;
    for (const letter of GRID_LETTERS) {
      const name = `${grade}-${letter}`;
      const has = existing.has(classKey(name));
      html += `<button type="button" class="grid-cell ${has ? "exists" : ""}" ${has ? "disabled" : ""}
                       data-name="${name}" onclick="toggleGridClass(this)"
                       title="${has ? "Уже есть" : name}">${name}</button>`;
    }
  }
  $("classGrid").innerHTML = html;
  updateClassGridButton();
  openModal("modalClasses");
}

function toggleGridClass(button) {
  const name = button.dataset.name;
  if (pickedClasses.has(name)) pickedClasses.delete(name);
  else pickedClasses.add(name);
  button.classList.toggle("picked", pickedClasses.has(name));
  updateClassGridButton();
}

function classGridNames() {
  const other = $("classOther").value.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  return [...pickedClasses, ...other];
}

function updateClassGridButton() {
  const count = classGridNames().length;
  const button = $("classGridSubmit");
  button.disabled = count === 0;
  button.textContent = count ? `Создать ${count} ${plural(count, "класс", "класса", "классов")}` : "Создать";
}

async function submitClassGrid() {
  const names = classGridNames();
  if (!names.length) return;
  try {
    const result = await api("/api/classes/bulk", { method: "POST", json: { school_id: activeSchool, names } });
    closeModal("modalClasses");
    if (result.created.length) {
      activeClass = result.created[0].id;
      draft = {};
    }
    await loadState();
    const made = result.created.length;
    toast(made
      ? `${plural(made, "Создан", "Создано", "Создано")} ${made} ${plural(made, "класс", "класса", "классов")}`
      : "Такие классы уже есть", made ? "success" : "");
  } catch (error) {
    toast(error.message, "error");
  }
}

function openSchool(id = null) {
  closeMenus();
  schoolTarget = id;
  const school = id ? schoolById(id) : null;
  $("schoolModalTitle").textContent = school ? "Переименовать школу" : "Новая школа";
  $("schoolName").value = school?.name || "";
  openModal("modalSchool");
}

async function saveSchool() {
  const name = $("schoolName").value.trim();
  if (!name) return toast("Введите название школы", "error");
  try {
    if (schoolTarget) {
      await api(`/api/schools/${schoolTarget}`, { method: "PATCH", json: { name } });
    } else {
      const created = await api("/api/schools", { method: "POST", json: { name } });
      activeSchool = created.school.id;
      activeClass = null;
    }
    closeModal("modalSchool");
    await loadState();
    toast(schoolTarget ? "Название школы изменено" : "Школа добавлена", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeSchool() {
  closeMenus();
  const school = schoolById(activeSchool);
  if (!school) return;
  if (state.schools.length === 1) return toast("Нельзя удалить единственную школу", "error");
  const classes = sortedClasses(activeSchool);
  const count = state.students.filter((s) => classes.some((c) => c.id === s.class_id)).length;
  confirmAction(`Удалить школу ${quoted(escapeHtml(school.name))}?`,
    `Пропадут классы (${classes.length}) и ученики (${count}) вместе со звёздами и историей. Это нельзя отменить.`,
    async () => {
      await api(`/api/schools/${activeSchool}`, { method: "DELETE" });
      if (typeof schoolView !== "undefined") schoolView = null;
      activeSchool = null;
      activeClass = null;
      await loadState();
      toast("Школа удалена", "success");
    }, "Удалить навсегда");
}

function renameClass() {
  closeMenus();
  const cls = classById(activeClass);
  if (!cls) return toast("Сначала выберите класс", "error");
  $("classNameInput").value = cls.name;
  openModal("modalClass");
}

async function saveClassName() {
  const name = $("classNameInput").value.trim();
  if (!name) return toast("Введите название класса", "error");
  try {
    await api(`/api/classes/${activeClass}`, { method: "PATCH", json: { name } });
    closeModal("modalClass");
    await loadState();
    toast("Класс переименован", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Общий урок: «Провести вместе с…» ────────────────────── */

function openJointPicker() {
  closeMenus();
  if (sortedClasses(activeSchool).length < 2) return toast("В школе пока один класс", "error");
  jointPick = new Set(jointClasses || [activeClass].filter(Boolean));
  renderJointPicker();
  openModal("modalJoint");
}

function renderJointPicker() {
  const classes = sortedClasses(activeSchool);
  $("jointList").innerHTML = classes.map((c) => `<button type="button" role="checkbox" aria-checked="${jointPick.has(c.id)}"
      data-class="${c.id}" title="${escapeHtml(c.name)}" onclick="toggleJointPick(${c.id})">${escapeHtml(c.name)}</button>`).join("");
  const picked = classes.filter((c) => jointPick.has(c.id)).map((c) => c.name);
  $("jointHint").innerHTML = picked.length > 1 ? `Вместе: <b>${escapeHtml(combinedName(picked))}</b>` : "Отметьте хотя бы два класса.";
  $("jointOpen").disabled = picked.length < 2;
}

function toggleJointPick(id) {
  if (jointPick.has(id)) jointPick.delete(id);
  else jointPick.add(id);
  renderJointPicker();
  $("jointList").querySelector(`[data-class="${id}"]`)?.focus();
}

function confirmJoint() {
  if (jointPick.size < 2) return;
  closeModal("modalJoint");
  openJoint([...jointPick]);
}

/** Общий журнал классов ids — из полоски «по расписанию» или окна выбора. */
function openJoint(ids) {
  const list = sortedClasses(activeSchool).map((c) => c.id).filter((id) => ids.includes(id));
  if (list.length < 2) { if (list.length) selectClass(list[0]); return; }
  if (journalView === "class" && jointClasses?.join() === list.join()) return;
  leaveDraftGuard(() => {
    draft = {};
    panelId = null;
    rowPop = null;
    jointClasses = list;
    activeClass = list[0];
    lessonClass = list[0];
    journalView = "class";
    setRoute();
    renderAll();
    window.scrollTo(0, 0);
  });
}

/** Все ученики класса — разом: класс, его расписание и карточки других классов остаются. */
function clearClass() {
  closeMenus();
  const cls = classById(activeClass);
  if (!cls) return;
  const count = classStudents().length;
  if (!count) return toast(`В классе ${cls.name} нет учеников`, "error");
  confirmAction(`Удалить всех учеников ${cls.name}?`,
    `${count} ${plural(count, "ученик", "ученика", "учеников")} — вместе со звёздами и историей. ` +
    "Класс и его расписание останутся, учеников можно загрузить заново. Это нельзя отменить.",
    async () => {
      const r = await api(`/api/classes/${cls.id}/students`, { method: "DELETE" });
      await loadState();
      toast(`Удалено: ${r.removed} ${plural(r.removed, "ученик", "ученика", "учеников")}`, "success");
    }, "Удалить всех");
}

function removeClass() {
  closeMenus();
  const cls = classById(activeClass);
  if (!cls) return;
  const count = classStudents().length;
  confirmAction(`Удалить класс ${escapeHtml(cls.name)}?`,
    `Ученики класса (${count}) будут удалены вместе со звёздами и историей. Это нельзя отменить.`,
    async () => {
      await api(`/api/classes/${activeClass}`, { method: "DELETE" });
      activeClass = null;
      await loadState();
      toast("Класс удалён", "success");
    }, "Удалить навсегда");
}

/* ── Оформление школы ─────────────────────────────────────── */

function openTheme() {
  closeMenus();
  const school = schoolById(activeSchool);
  if (!school) return;
  $("themeSchool").textContent = school.name;
  renderThemeTiles();
  openModal("modalTheme");
}

function renderThemeTiles() {
  const chosen = schoolById(activeSchool)?.theme || currentTheme();
  $("themeGrid").innerHTML = THEMES.map((t) => `
    <button type="button" class="theme-tile" data-theme="${t}" role="radio" aria-checked="${t === chosen}"
            onclick="pickTheme('${t}')">
      <span class="tt-hero" data-night>
        <span class="tt-title">Иванов Артём</span>
        <span class="tt-bal">${icon("star")}39<small>звёзд</small></span>
      </span>
      <span class="tt-body">
        <span class="tt-btn">Покажу учителю</span>
        <span class="pill-star">${icon("star")}<b>12</b></span>
      </span>
      <span class="tt-name">${THEME_NAMES[t]}${t === chosen ? icon("check-circle") : ""}</span>
    </button>`).join("");
  const mode = document.documentElement.dataset.mode || "auto";
  document.querySelectorAll("#modeSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
}

async function pickTheme(theme) {
  const school = schoolById(activeSchool);
  if (!school || school.theme === theme) return;
  try {
    await api(`/api/schools/${school.id}/theme`, { method: "PUT", json: { theme } });
    school.theme = theme;
    applyTheme(theme);
    renderThemeTiles();
    if (currentPage() === "schools") renderSchoolsPage();
    toast(`Тема «${THEME_NAMES[theme]}» — для всей школы`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function pickMode(mode) {
  applyMode(mode);
  renderThemeTiles();
}

/* ── Банк дня ─────────────────────────────────────────────── */

/** Сегодня по часам компьютера учителя. toISOString() дал бы дату по Гринвичу:
 *  в Ташкенте до пяти утра это ещё вчера. */
function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function lessonDay() { return $("lessonDate").value || localToday(); }

function dotted(iso) { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; }

function renderDate() {
  const day = lessonDay();
  const [y, m, d] = day.split("-").map(Number);
  $("lessonDateText").textContent = dotted(day);
  $("lessonWeekday").textContent = "· " + new Date(y, m - 1, d).toLocaleDateString("ru-RU", { weekday: "short" });
}

/* Лимиты действуют на дату, а не на одно нажатие «Завершить урок»: классу — 10 звёзд
   за день, ученику — 5. Решает сервер, а панель показывает остаток, чтобы учитель
   не упирался в ошибку. Правило подсчёта то же, что в backend/routers/lessons.py. */
function classGotOn(day) {
  return journalClassIds().reduce((sum, id) => sum + classStarsOn(id, day), 0);
}

function studentGotOn(student, day) {
  const reason = `Урок (${day})`;
  return (student.history || [])
    .filter((h) => h.date === day && h.reason === reason && h.delta > 0)
    .reduce((sum, h) => sum + h.delta, 0);
}

function usedBank() { return journalStudents().reduce((sum, s) => sum + (draft[s.id] || 0), 0); }
function bankLeft() { return Math.max(0, MAX_LESSON_BANK - classGotOn(lessonDay()) - usedBank()); }

function renderBank() {
  renderDate();
  const given = classGotOn(lessonDay());
  const used = usedBank();
  const left = bankLeft();
  $("bankNum").textContent = `${given + used} / ${MAX_LESSON_BANK}`;
  $("bankText").textContent = left ? `осталось ${left}` : "банк исчерпан";
  $("bank").title = (given ? `Раньше за эту дату уже начислено ${given}. ` : "") + (jointClasses
    ? `Общий банк урока ${jointTitle()}: за одну дату все его классы вместе получают не больше ${MAX_LESSON_BANK} звёзд, ученик — не больше ${MAX_STUDENT_STARS}`
    : `За одну дату класс получает не больше ${MAX_LESSON_BANK} звёзд, ученик — не больше ${MAX_STUDENT_STARS}`);
  $("bankDots").innerHTML = Array.from({ length: MAX_LESSON_BANK }, (_, i) => {
    const cls = i < given ? "given" : i < given + used ? "on" : "";
    return `<svg viewBox="0 0 24 24"><polygon class="bank-dot ${cls}" points="${STAR_POINTS}"/></svg>`;
  }).join("");
}

/** Выбрали другую дату — у неё свой банк. Черновик подрезаем под новые остатки. */
function onLessonDateChange() {
  const day = lessonDay();
  let room = MAX_LESSON_BANK - classGotOn(day);
  for (const student of journalStudents()) {
    const cap = Math.max(0, MAX_STUDENT_STARS - studentGotOn(student, day));
    const value = Math.max(0, Math.min(draft[student.id] || 0, cap, room));
    draft[student.id] = value;
    room -= value;
  }
  renderJournalHead();
  renderJournalHome();
  renderTable();
  renderPanel();
}

/* ── Журнал ───────────────────────────────────────────────── */

function passwordVisible(student) { return showPasswords !== flippedPasswords.has(student.id); }

function passwordCell(student) {
  if (!student.password_shown) return `<i>свой пароль</i><span></span>`;
  const visible = passwordVisible(student);
  const label = visible ? "Скрыть пароль" : "Показать пароль";
  return `<span class="mono ${visible ? "" : "masked"}">${visible ? escapeHtml(student.password_shown) : "•••••••••"}</span>
    <button class="icon-btn" type="button" title="${label}" aria-label="${label}"
            onclick="togglePassword(${student.id})">${icon(visible ? "eye-off" : "eye")}</button>`;
}

function credGrid(student, withCode = false) {
  const site = `${location.host}${BASE}/enter`;
  return `<div class="cred-grid">
    ${withCode ? `<span class="muted">Код</span><span><span class="code-chip">${escapeHtml(student.token || "—")}</span></span><span></span>` : ""}
    <span class="muted">Адрес</span><span class="mono">${escapeHtml(site)}</span><span></span>
    <span class="muted">Логин</span><span class="mono">${escapeHtml(student.login || "—")}</span><span></span>
    <span class="muted">Пароль</span>${passwordCell(student)}
  </div>`;
}

function isLoggedIn(student) { return Boolean(student.tg_id || student.last_login); }

/* «Давно без звёзд» — столько уроков класса подряд ученик ничего не получил.
   Видит только учитель: ребёнку такая метка не показывается. Правило то же,
   что в backend/stats.py (student_activity). */
const IDLE_LESSONS = 3;
function idleSet(classId = activeClass) {
  const lessons = (state.lessons || [])
    .filter((l) => l.class_id === classId && l.awards && (Object.keys(l.awards).length || Object.keys(l.undone || {}).length))
    .sort((a, b) => (a.date === b.date ? (b.id || 0) - (a.id || 0) : a.date < b.date ? 1 : -1));
  const idle = new Set();
  if (lessons.length < IDLE_LESSONS) return idle;
  for (const student of classStudents(classId)) {
    if (lessons.slice(0, IDLE_LESSONS).every((l) => !(String(student.id) in l.awards))) idle.add(student.id);
  }
  return idle;
}

function togglePassword(studentId) {
  if (flippedPasswords.has(studentId)) flippedPasswords.delete(studentId);
  else flippedPasswords.add(studentId);
  renderTable();
  renderPanel();
}

function togglePasswords() {
  showPasswords = !showPasswords;
  flippedPasswords = new Set();
  const button = $("passwordsToggle");
  button.querySelector("span").textContent = showPasswords ? "Скрыть пароли" : "Показать пароли";
  button.querySelector("use").setAttribute("href", showPasswords ? "#i-eye-off" : "#i-eye");
  renderTable();
  renderPanel();
}

function emptySheet(html) {
  $("tableHost").innerHTML = `<div class="empty">${html}</div>`;
}

function renderTable() {
  renderBank();
  renderTodayStrip();
  renderLessonBar();
  const addRow = $("addRow");
  addRow.hidden = !activeClass || Boolean(jointClasses);     // добавить ученика — на вкладке его класса
  $("cardsBtn").hidden = Boolean(jointClasses);

  const school = schoolById(activeSchool);
  const cls = classById(activeClass);
  $("studentsTitle").textContent = `Ученики · ${cls ? journalStudents().length : 0}`;

  if (!school) {
    return emptySheet(isAdmin()
      ? `${icon("school", "ic ic-xl")}<b>Школ пока нет</b>Добавьте первую в меню «Школа».`
      : `${icon("school", "ic ic-xl")}<b>Вам пока не открыта ни одна школа</b>Обратитесь к администратору.`);
  }
  if (!cls) {
    return emptySheet(`${icon("users", "ic ic-xl")}<b>В школе ${quoted(escapeHtml(school.name))} пока нет классов</b>
      <button class="btn btn-md" onclick="openClassGrid()">${icon("plus", "ic ic-sm")}Добавить классы</button>`);
  }
  const list = journalStudents();
  if (!list.length) {
    return emptySheet(jointClasses
      ? `${icon("users", "ic ic-xl")}<b>В классах ${escapeHtml(jointTitle())} пока нет учеников</b>
        Добавьте их на вкладке каждого класса.`
      : `${icon("users", "ic ic-xl")}<b>В классе ${escapeHtml(cls.name)} пока нет учеников</b>
        Добавьте их ниже, загрузите шаблон Excel или вставьте строки из таблицы.`);
  }

  // Фокус клавиатуры остаётся на той же строке после перерисовки
  const focused = document.activeElement?.classList?.contains("jt-row") ? document.activeElement.dataset.row : null;

  const day = lessonDay();
  // Школа без системы наград: журнал — просто список учеников класса
  const roster = !rewardsHere();
  const makeRow = roster ? rosterRowHtml : rowHtml;
  // Общий урок: перед учениками каждого класса — строка-заголовок; номера сквозные,
  // чтобы ↑/↓ и Enter переходили через границу классов.
  let index = 0;
  const rows = jointClasses
    ? jointClasses.map((id) => {
      const kidsOf = classStudents(id);
      return `<div class="jt-group" role="row"><span role="rowheader">${icon("users", "ic ic-sm")}<b>${escapeHtml(classById(id)?.name || "")}</b>
          <span class="jt-group-n">${kidsOf.length} ${kids(kidsOf.length)}</span></span></div>` +
        kidsOf.map((student) => makeRow(student, index++, day)).join("");
    }).join("")
    : list.map((student, i) => makeRow(student, i, day)).join("");
  const title = jointClasses ? jointTitle() : cls.name;
  if (roster) {
    $("tableHost").innerHTML = `
      <div class="jt roster" role="table" aria-label="Ученики ${escapeHtml(title)}">
        <div class="jt-head" role="row">
          <span role="columnheader">№</span><span role="columnheader">Ученик</span><span role="columnheader" class="right">Действия</span>
        </div>
        ${rows}
      </div>`;
    return floatRowPop();
  }
  $("tableHost").innerHTML = `
    <div class="jt ${hasPanel() ? "has-panel" : ""}" role="table" aria-label="Журнал ${escapeHtml(title)}">
      <div class="jt-head" role="row">
        <span role="columnheader">№</span><span role="columnheader">Ученик</span>
        <span role="columnheader" title="Бледные звёзды уже начислены за эту дату">Звёзды за урок · до ${MAX_STUDENT_STARS}</span>
        <span role="columnheader">Баланс</span><span role="columnheader" class="not-clear">Вход</span>
        <span role="columnheader" class="right">Действия</span>
      </div>
      ${rows}
    </div>`;
  if (focused !== null) $("tableHost").querySelector(`.jt-row[data-row="${focused}"]`)?.focus({ preventScroll: true });
  floatRowPop();
}

/** Меню «⋯» и окошко «Вход» выносим в слой поверх страницы. Внутри листа журнала
 *  их перекрывала панель «Школа · Дата · Банк» и обрезал край экрана. Раскрываем
 *  вниз, а если снизу не хватает места — вверх; по горизонтали не выходим за экран. */
function floatRowPop() {
  document.querySelectorAll("body > .row-float").forEach((el) => el.remove());
  const pop = $("tableHost").querySelector(".cell-act .menu, .cell-login .pop");
  if (!pop || !rowPop) return;
  const row = pop.closest(".jt-row");
  const anchor = row.querySelector(rowPop.kind === "more" ? ".act-btn.more" : ".code-btn");
  const a = anchor?.getBoundingClientRect();
  if (!a || !a.width) { pop.remove(); return; }   // кнопка скрыта темой — окошко не к чему привязать
  document.body.appendChild(pop);
  pop.classList.add("row-float");
  const gap = 6;
  const top0 = popTopEdge() + gap;
  const h = pop.offsetHeight;
  const w = pop.offsetWidth;
  let top = a.bottom + gap;
  if (top + h > window.innerHeight - gap && a.top - gap - h >= top0) top = a.top - gap - h;
  top = Math.max(top0, Math.min(top, window.innerHeight - gap - h));
  let left = rowPop.kind === "more" ? a.right - w : a.left;
  left = Math.max(gap, Math.min(left, window.innerWidth - gap - w));
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
  pop.style.maxHeight = `${window.innerHeight - top0 - gap}px`;
}

function rowHtml(student, index, day) {
  const name = escapeHtml(student.name);
  const current = draft[student.id] || 0;
  const got = Math.min(MAX_STUDENT_STARS, studentGotOn(student, day));
  const cap = MAX_STUDENT_STARS - got;
  const room = current + bankLeft();
  const isIn = isLoggedIn(student);
  const status = isIn ? "вошёл" : "ждёт входа";
  const picked = panelId === student.id;
  const popOpen = rowPop?.id === student.id;

  const starButtons = Array.from({ length: MAX_STUDENT_STARS }, (_, i) => {
    const k = i + 1;
    if (k <= got) {
      return `<button class="star-btn given" tabindex="-1" disabled title="Уже начислено за ${dotted(day)}">${starSvg()}</button>`;
    }
    const value = k - got;
    const label = `${value} ${stars(value)} — ${name}`;
    return `<button class="star-btn ${value <= current ? "on" : ""}" tabindex="-1" data-v="${value}"
                    aria-label="${label}" title="${label}"
                    onmouseenter="previewStars(this, true)" onmouseleave="previewStars(this, false)"
                    onclick="setDraft(${student.id}, ${value === current ? 0 : value})">${starSvg()}</button>`;
  }).join("");

  const segs = Array.from({ length: MAX_STUDENT_STARS + 1 }, (_, k) => {
    const off = k > cap || k > room;
    const label = `${k} ${stars(k)} — ${name}`;
    return `<button tabindex="-1" role="radio" aria-checked="${k === current}" aria-label="${label}"
                    title="${off ? (k > cap ? `За эту дату уже ${got}` : "Банк дня исчерпан") : label}"
                    ${off ? "disabled" : ""} onclick="setDraft(${student.id}, ${k})">${k}</button>`;
  }).join("");

  const codePop = rowPop?.kind === "code" && popOpen ? `
    <div class="pop" role="dialog" aria-label="Вход без Telegram" onclick="event.stopPropagation()">
      <span class="caps">Вход без Telegram</span>
      ${credGrid(student)}
      <div class="pop-foot">
        <button class="btn btn-link btn-sm" onclick="resetToken(${student.id})">${icon("refresh", "ic ic-sm")}Перевыпустить вход</button>
        <small>старая карточка перестанет работать</small>
      </div>
    </div>` : "";

  const hidden = student.rating_hidden;
  const moreMenu = rowPop?.kind === "more" && popOpen ? `
    <div class="menu right" role="menu" style="display:block" onclick="event.stopPropagation()">
      <button role="menuitem" onclick="showLoginFor(${student.id})">${icon("eye", "ic ic-sm")}Показать логин и пароль</button>
      <button role="menuitem" onclick="showQr(${student.id})">${icon("qr", "ic ic-sm")}QR и данные для входа…</button>
      <button role="menuitem" onclick="resetToken(${student.id})">${icon("refresh", "ic ic-sm")}Перевыпустить вход</button>
      <button role="menuitem" onclick="openActivity(${student.id})">${icon("chart", "ic ic-sm")}Активность</button>
      <button role="menuitem" onclick="previewCabinet(${student.id})">${icon("user", "ic ic-sm")}Кабинет глазами ученика</button>
      <button role="menuitem" onclick="openDeduct(${student.id})">${icon("minus-circle", "ic ic-sm")}Списать звёзды…</button>
      <button role="menuitem" onclick="resetAvatar(${student.id})">${icon("image", "ic ic-sm")}Сбросить аватар</button>
      <button role="menuitem" onclick="toggleRating(${student.id})">${icon(hidden ? "eye" : "eye-off", "ic ic-sm")}${hidden ? "Вернуть в рейтинг" : "Скрыть из рейтинга"}</button>
      <div class="menu-sep"></div>
      <button role="menuitem" class="danger" onclick="removeStudent(${student.id})">${icon("trash", "ic ic-sm")}Удалить ученика…</button>
    </div>` : "";

  return `
    <div class="jt-row ${picked ? "picked" : ""} ${popOpen ? "menu-open" : ""}" role="row" tabindex="0"
         data-row="${index}" data-id="${student.id}" onkeydown="rowKey(event, ${student.id}, ${index})">
      <span class="cell-num" role="cell">${index + 1}</span>
      <span class="cell-name" role="cell">
        <img class="av-sm" src="${escapeHtml(avatarUrl(student))}" alt="" loading="lazy">
        <span class="st-name" title="${name}" onclick="openPanel(${student.id})">${name}</span>
        <span class="st-dot only-clear ${isIn ? "in" : ""}" role="img" aria-label="${status}" title="${status}"></span>
        ${hidden ? `<span class="tag tag-hidden" title="Скрыт из рейтинга">${icon("eye-off")}вне рейтинга</span>` : ""}
        ${idleSet(student.class_id).has(student.id) ? `<button class="tag tag-idle" onclick="openActivity(${student.id})"
            title="${IDLE_LESSONS} урока подряд без звёзд — посмотреть активность">давно без звёзд</button>` : ""}
      </span>
      <span class="cell-stars" role="cell">
        <span class="star-row not-clear-flex" role="radiogroup" aria-label="Звёзды за урок: ${name}">${starButtons}</span>
        <span class="seg-stars only-clear-flex" role="radiogroup" aria-label="Звёзды за урок: ${name}">${segs}</span>
        <input class="star-num ${current ? "set" : ""}" value="${current}" inputmode="numeric" maxlength="2"
               aria-label="Звёзды за урок, число: ${name}"
               title="${got ? `За этот урок. Раньше за ${dotted(day)} уже ${got}` : "За этот урок"}"
               onchange="numInput(${student.id}, this.value)" onfocus="this.select()">
      </span>
      <span class="cell-bal" role="cell">
        <span class="pill-star">${icon("star")}<b>${student.total_stars}</b></span>
        ${current ? `<span class="after">→ ${student.total_stars + current}</span>` : ""}
      </span>
      <span class="cell-login not-clear-flex" role="cell">
        <button class="code-chip code-btn" onclick="togglePop('code', ${student.id}, event)" aria-haspopup="dialog"
                aria-expanded="${rowPop?.kind === "code" && popOpen}" title="${botLogin() ? "Логин и пароль для входа без Telegram" : "Логин и пароль для входа на сайте"}">
          ${escapeHtml((botLogin() ? student.token : student.login) || "—")}${icon("chevron-down")}</button>
        <span class="login-status ${isIn ? "in" : ""}">${icon(isIn ? "check-circle" : "dash-circle")}${status}</span>
        ${codePop}
      </span>
      <span class="cell-act" role="cell">
        <button class="act-btn" onclick="showQr(${student.id})" aria-label="Карточка с QR-кодом: ${name}" title="Карточка с QR-кодом">
          ${icon("qr", "ic ic-sm")}<span class="act-label">QR</span></button>
        <button class="act-btn" onclick="openGive(${student.id})" aria-label="Выдать приз: ${name}" title="Выдать приз">
          ${icon("gift", "ic ic-sm")}<span class="act-label">Выдать приз</span></button>
        <button class="act-btn more" onclick="togglePop('more', ${student.id}, event)" aria-haspopup="menu"
                aria-expanded="${rowPop?.kind === "more" && popOpen}" aria-label="Ещё действия: ${name}" title="Ещё действия">
          ${icon("more", "ic ic-sm")}</button>
        ${moreMenu}
      </span>
    </div>`;
}

/** Строка ученика в школе без системы наград: без звёзд, баланса, входа и призов. */
function rosterRowHtml(student, index) {
  const name = escapeHtml(student.name);
  return `
    <div class="jt-row" role="row" data-row="${index}" data-id="${student.id}">
      <span class="cell-num" role="cell">${index + 1}</span>
      <span class="cell-name" role="cell">
        <img class="av-sm" src="${escapeHtml(avatarUrl(student))}" alt="" loading="lazy">
        <span class="st-name" title="${name}">${name}</span>
      </span>
      <span class="cell-act" role="cell">
        <button class="act-btn" onclick="removeStudent(${student.id})" aria-label="Удалить ученика: ${name}" title="Удалить ученика">
          ${icon("trash", "ic ic-sm")}<span class="act-label">Удалить</span></button>
      </span>
    </div>`;
}

/** Наведение на звезду: бледная подсказка, сколько станет. */
function previewStars(button, on) {
  const row = button.parentElement;
  const upto = on ? parseInt(button.dataset.v) : 0;
  row.querySelectorAll(".star-btn[data-v]").forEach((b) => {
    b.classList.toggle("preview", on && !b.classList.contains("on") && parseInt(b.dataset.v) <= upto);
  });
}

function togglePop(kind, id, event) {
  event?.stopPropagation();
  closeMenus(false);
  rowPop = rowPop?.kind === kind && rowPop.id === id ? null : { kind, id };
  renderTable();
}

function showLoginFor(id) {
  if (hasPanel()) { rowPop = null; openPanel(id); return; }
  rowPop = { kind: "code", id };
  renderTable();
}

function numInput(studentId, value) {
  const digit = parseInt(String(value).replace(/\D/g, "").slice(-1) || "0", 10);
  setDraft(studentId, digit);
}

function setDraft(studentId, value) {
  const day = lessonDay();
  const student = studentById(studentId);
  const cap = Math.max(0, MAX_STUDENT_STARS - (student ? studentGotOn(student, day) : 0));
  if (value > cap) {
    toast(cap ? `${student.name}: за этот день можно добавить ещё не больше ${cap}`
              : `${student.name} уже получил(а) ${MAX_STUDENT_STARS} звёзд за этот день`, "error");
  }
  value = Math.max(0, Math.min(cap, value));
  const others = journalStudents().reduce((sum, s) => (s.id === studentId ? sum : sum + (draft[s.id] || 0)), 0);
  const available = Math.max(0, MAX_LESSON_BANK - classGotOn(day) - others);
  if (value > available) {
    toast(available ? `В банке дня осталось только ${available}`
                    : `Банк дня исчерпан: за ${dotted(day)} ${jointClasses ? "классам урока" : "классу"} уже начислено ${MAX_LESSON_BANK} звёзд`, "error");
    value = available;
  }
  draft[studentId] = value;
  renderTable();
  if (panelId === studentId) renderPanel();
}

/** Клавиатура учителя: 0–5 ставят звёзды, ↑/↓ и Enter — соседний ученик,
 *  Space — карточка ученика (в темах с боковой панелью). */
function rowKey(event, studentId, index) {
  const inInput = event.target.tagName === "INPUT";
  if (/^[0-5]$/.test(event.key) && !inInput && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    setDraft(studentId, parseInt(event.key));
    return;
  }
  if (event.key === " " && !inInput && event.target.classList.contains("jt-row") && hasPanel()) {
    event.preventDefault();
    openPanel(studentId);
    return;
  }
  const down = event.key === "ArrowDown" || (event.key === "Enter" && !event.ctrlKey && !event.metaKey && event.target.classList.contains("jt-row"));
  if (down || event.key === "ArrowUp") {
    const next = $("tableHost").querySelector(`.jt-row[data-row="${index + (down ? 1 : -1)}"]`);
    if (next) {
      event.preventDefault();
      if (inInput) event.target.dispatchEvent(new Event("change"));
      next.focus();
    }
  }
}

function resetLesson() {
  draft = {};
  renderTable();
  renderPanel();
}

function renderLessonBar() {
  const list = journalStudents();
  const sum = usedBank();
  const count = list.filter((s) => (draft[s.id] || 0) > 0).length;
  $("sumStars").textContent = sum;
  $("sumText").textContent = sum ? `${count} ${kids(count)} из ${list.length}` : "пока никому не поставлено";
  $("resetLessonBtn").hidden = !sum;
  $("finishBtn").disabled = !sum;
  $("lessonBar").hidden = !journalClassIds().length;
}

/* ── Боковая панель ученика (sky, clear, sticker) ─────────── */

function openPanel(id) {
  if (!hasPanel()) return;
  panelId = id;
  rowPop = null;
  renderTable();
  renderPanel();
}

function closePanel() {
  const id = panelId;
  panelId = null;
  renderTable();
  renderPanel();
  $("tableHost").querySelector(`.jt-row[data-id="${id}"]`)?.focus();
}

function historyTitle(entry, student) {
  if (/^Урок \(/.test(entry.reason || "")) return `Урок · ${classById(student.class_id)?.name || ""}`;
  return entry.reason || (entry.delta > 0 ? "Начисление" : "Списание");
}

function renderPanel() {
  const aside = $("sidePanel");
  const student = studentById(panelId);
  if (!student || !hasPanel() || currentPage() !== "journal" || !rewardsHere()) {
    aside.hidden = true;
    aside.innerHTML = "";
    return;
  }
  const current = draft[student.id] || 0;
  const isIn = isLoggedIn(student);
  const hist = (student.history || []).slice(0, 12);
  aside.hidden = false;
  aside.innerHTML = `
    <div class="sp-top">
      <span class="caps">Ученик · ${escapeHtml(classById(student.class_id)?.name || "")}</span>
      <button class="icon-btn" onclick="closePanel()" aria-label="Закрыть панель" title="Закрыть">${icon("close")}</button>
    </div>
    <div class="sp-body">
      <div class="sp-who">
        <img src="${escapeHtml(avatarUrl(student))}" alt="">
        <div><div class="sp-name">${escapeHtml(student.name)}</div>
          <div class="sp-status ${isIn ? "in" : ""}">${isIn ? "вошёл в кабинет" : "ждёт первого входа"}</div></div>
      </div>
      <div class="sp-bal">
        <span class="caps">Баланс</span>
        <span class="sp-bal-num">${fmt(student.total_stars)}${icon("star", "ic ic-lg ic-star")}</span>
        ${current ? `<span class="sp-bal-after">→ ${fmt(student.total_stars + current)}</span>` : ""}
      </div>
      <div class="sp-btns">
        <button class="btn" onclick="openGive(${student.id})">${icon("gift", "ic ic-sm")}Выдать приз</button>
        <button class="btn btn-ghost" onclick="showQr(${student.id})">${icon("qr", "ic ic-sm")}Карточка с QR</button>
      </div>
      <section class="sp-sec">
        <h3 class="caps">Вход без Telegram</h3>
        ${credGrid(student, true)}
        <button class="btn btn-link btn-sm" style="align-self:flex-start;margin-left:-10px" onclick="resetToken(${student.id})">
          ${icon("refresh", "ic ic-sm")}Перевыпустить вход</button>
      </section>
      <section class="sp-sec">
        <h3 class="caps">История</h3>
        <div class="sp-hist">${hist.length ? hist.map((e) => `
          <div class="sp-hist-row">
            <span class="d">${escapeHtml(formatDay(e.date, { year: false }))}</span>
            <span class="t" title="${escapeHtml(historyTitle(e, student))}">${escapeHtml(historyTitle(e, student))}</span>
            <span class="a ${e.delta < 0 ? "minus" : ""}">${e.delta > 0 ? "+" + e.delta : "−" + Math.abs(e.delta)} ★</span>
          </div>`).join("") : `<div class="sp-hist-row"><span class="muted">Пока без начислений</span></div>`}</div>
      </section>
      <section class="sp-sec">
        <h3 class="caps">Ещё</h3>
        <div class="row">
          <button class="btn btn-ghost btn-sm" onclick="openActivity(${student.id})">${icon("chart", "ic ic-sm")}Активность</button>
          <button class="btn btn-ghost btn-sm" onclick="previewCabinet(${student.id})">${icon("user", "ic ic-sm")}Кабинет глазами ученика</button>
          <button class="btn btn-ghost btn-sm" onclick="openDeduct(${student.id})">${icon("minus-circle", "ic ic-sm")}Списать звёзды…</button>
          <button class="btn btn-ghost btn-sm" onclick="resetAvatar(${student.id})">${icon("image", "ic ic-sm")}Сбросить аватар</button>
        </div>
      </section>
      <section class="sp-sec sp-danger">
        <h3 class="caps">Опасная зона</h3>
        <div class="row">
          <button class="btn btn-ghost btn-sm" onclick="toggleRating(${student.id})">
            ${icon(student.rating_hidden ? "eye" : "eye-off", "ic ic-sm")}${student.rating_hidden ? "Вернуть в рейтинг" : "Скрыть из рейтинга"}</button>
          <button class="btn btn-danger-quiet btn-sm" onclick="removeStudent(${student.id})">${icon("trash", "ic ic-sm")}Удалить ученика…</button>
        </div>
      </section>
    </div>`;
}

/* ── Действия с учениками ─────────────────────────────────── */

async function addStudent() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  const nameInput = $("newStudentName");
  const starsInput = $("newStudentStars");
  const name = nameInput.value.trim();
  if (!name) return toast("Введите фамилию и имя", "error");
  try {
    await api("/api/students", {
      method: "POST",
      json: { class_id: activeClass, name, total_stars: parseInt(starsInput.value) || 0 },
    });
    nameInput.value = "";
    starsInput.value = "0";
    await loadState();
    toast(`${name} — в журнале`, "success");
    nameInput.focus();
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeStudent(id) {
  rowPop = null;
  const student = studentById(id);
  if (!student) return;
  confirmAction(`Удалить ученика ${escapeHtml(student.name)}?`,
    `Пропадут баланс <b>${fmt(student.total_stars)} ★</b>, история и карточка входа. Это нельзя отменить. ` +
    "Если ученик просто ушёл на время — лучше скрыть его из рейтинга.",
    async () => {
      await api(`/api/students/${id}`, { method: "DELETE" });
      delete draft[id];
      if (panelId === id) panelId = null;
      await loadState();
      toast("Ученик удалён", "success");
    }, "Удалить навсегда");
}

function resetToken(id) {
  rowPop = null;
  const student = studentById(id);
  confirmAction("Перевыпустить вход?",
    `Старая карточка ${escapeHtml(student?.name || "")} перестанет работать, Telegram будет отвязан. ` +
    "Понадобится распечатать карточку заново.",
    async () => {
      await api(`/api/students/${id}/reset-token`, { method: "POST" });
      await loadState();
      toast(`Вход для ${student?.name || "ученика"} перевыпущен — распечатайте новую карточку`, "success");
    }, "Перевыпустить");
}

async function resetAvatar(id) {
  rowPop = null;
  try {
    await api(`/api/students/${id}/reset-avatar`, { method: "POST" });
    await loadState();
    toast("Аватар сброшен", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function toggleRating(id) {
  rowPop = null;
  const student = studentById(id);
  if (!student) return;
  const hide = !student.rating_hidden;
  try {
    await api(`/api/students/${id}`, { method: "PATCH", json: { rating_hidden: hide } });
    await loadState();
    toast(hide ? `${student.name} скрыт(а) из рейтинга — звёзды копятся как обычно`
               : `${student.name} снова в рейтинге`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Активность ученика ───────────────────────────────────── */

/** Столбики по неделям: одна серия — один цвет звезды, без легенды; подпись и
 *  подсказка у каждого столбика, текстовая версия — для экранного чтеца. */
function shortDay(iso) { return iso ? formatDay(iso, { year: false }) : "—"; }

async function openActivity(studentId) {
  rowPop = null;
  try {
    const data = await api(`/api/stats/student/${studentId}`);
    const a = data.activity;
    const s = data.student;
    $("activityAvatar").src = avatarUrl(s);
    $("activityName").textContent = s.name;
    $("activitySub").textContent = `${s.class_name} · на балансе ${fmt(s.total_stars)} ★ · заработано за всё время ${fmt(a.earned)} ★`;
    const share = a.lessons_30 ? `${a.lessons_30_with_stars} из ${a.lessons_30}` : "уроков не было";
    $("activityBody").innerHTML = `
      <div class="tiles">
        <div class="tile"><span>За неделю</span><b>${fmt(a.week)} ★</b></div>
        <div class="tile"><span>За 30 дней</span><b>${fmt(a.month)} ★</b></div>
        <div class="tile"><span>Серия уроков</span><b>${a.streak}</b></div>
        <div class="tile"><span>Призов</span><b>${a.prizes}</b></div>
      </div>
      <h3 class="caps act-caps">Звёзды по неделям</h3>
      ${weekBars(a.weeks)}
      <div class="act-facts">
        <div><span>Уроки со звёздами за 30 дней</span><b>${share}</b></div>
        <div><span>Последняя звезда</span><b>${escapeHtml(shortDay(a.last_star))}</b></div>
        <div><span>Без звёзд подряд</span><b class="${a.idle ? "warn" : ""}">${a.idle_lessons} ${plural(a.idle_lessons, "урок", "урока", "уроков")}</b></div>
        <div><span>Заходил в кабинет</span><b>${escapeHtml(a.last_seen ? shortDay(a.last_seen) : "ещё не заходил")}</b></div>
      </div>`;
    renderTable();
    openModal("modalActivity");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Данные для входа ─────────────────────────────────────── */

function renderAccessPassword(student) {
  const box = $("accessPassword");
  if (student?.password_shown) {
    box.textContent = student.password_shown;
    box.classList.remove("hidden-pass");
    $("accessNote").textContent = "Этот пароль напечатан на карточке ученика.";
  } else {
    box.textContent = "ученик сменил пароль сам";
    box.classList.add("hidden-pass");
    $("accessNote").textContent = "Посмотреть его нельзя — пароль знает только ребёнок. Забыл? Выдайте новый.";
  }
}

function showQr(id) {
  rowPop = null;
  const student = studentById(id);
  if (!student) return;
  accessTarget = id;
  $("qrName").textContent = student.name;
  $("qrSub").textContent = `${schoolById(activeSchool)?.name || ""} · ${classById(student.class_id)?.name || "—"}`;
  $("qrCode").textContent = student.token || "—";
  const bot = botLogin();
  $("qrCode").hidden = !bot;
  $("qrWayTitle").textContent = bot ? "Через Telegram" : "QR — вход на сайте";
  $("qrWayText").textContent = bot ? "Навести камеру на QR-код или отправить боту код"
    : "Камера откроет страницу входа с логином — останется ввести пароль";
  $("qrSiteTitle").textContent = bot ? "Без Telegram — через сайт" : "Вход на сайте";
  $("qrImage").src = `${BASE}/api/students/${id}/qr.png?size=10&t=${Date.now()}`;
  $("accessSite").textContent = `${location.host}${BASE}/enter`;
  $("accessLogin").value = student.login || "";
  renderAccessPassword(student);
  renderTable();
  openModal("modalQr");
}

async function saveLogin() {
  const login = $("accessLogin").value.trim().toLowerCase();
  if (!login) return toast("Логин не может быть пустым", "error");
  try {
    await api(`/api/students/${accessTarget}`, { method: "PATCH", json: { login } });
    await loadState();
    $("accessLogin").value = login;
    toast("Логин сохранён", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function resetPassword() {
  try {
    const result = await api(`/api/students/${accessTarget}/password`, { method: "POST", json: {} });
    await loadState();
    renderAccessPassword(studentById(accessTarget));
    toast(`Новый пароль: ${result.password}`, "success", "key");
  } catch (error) {
    toast(error.message, "error");
  }
}

function resetTokenFromAccess() {
  const id = accessTarget;
  closeModal("modalQr");
  resetToken(id);
}

function previewCabinet(id) {
  rowPop = null;
  renderTable();
  window.open(`${BASE}/app?preview=${id}&theme=${currentTheme()}`, "_blank");
}

function openCards() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  window.open(`${BASE}/cards/${activeClass}`, "_blank");
}

/* ── Итог урока (T1a) ─────────────────────────────────────── */

function openFinish() {
  const list = journalStudents();
  const given = list.filter((s) => (draft[s.id] || 0) > 0);
  const sum = usedBank();
  if (!sum) return;
  const day = lessonDay();
  const cls = classById(activeClass);
  const zero = list.length - given.length;
  $("finishCaps").textContent = `Итог урока · ${dotted(day)} · ${jointClasses ? jointTitle() : cls?.name || ""}`;
  $("finishTitle").textContent = `Начислить ${sum} ${stars(sum)} ${given.length} ` +
    `${plural(given.length, "ученику", "ученикам", "ученикам")} за ${dotted(day)}?`;
  $("finishList").innerHTML = given.map((s) => `
    <div class="finish-row">
      <img src="${escapeHtml(avatarUrl(s))}" alt="">
      <span class="n">${escapeHtml(s.name)}${jointClasses ? ` <span class="muted">· ${escapeHtml(classById(s.class_id)?.name || "")}</span>` : ""}</span>
      <span class="v">+${draft[s.id]} ★</span>
      <span class="ft">${s.total_stars} → <b>${s.total_stars + draft[s.id]}</b></span>
    </div>`).join("");
  $("finishNote").textContent = (zero ? `Остальным ${zero} — без звёзд за этот урок. ` : "") +
    "Урок сохранится во вкладке «Уроки» — там можно отменить ошибку.";
  $("finishConfirm").textContent = `Начислить ${sum} ★`;
  openModal("modalFinish");
}

async function submitLesson() {
  const ids = journalClassIds();
  if (!ids.length) return;
  const list = journalStudents();
  const awards = list.map((s) => ({ student_id: s.id, stars: draft[s.id] || 0 })).filter((a) => a.stars > 0);
  const total = awards.reduce((sum, a) => sum + a.stars, 0);
  if (!total) return;
  const date = lessonDay();
  const button = $("finishConfirm");
  button.disabled = true;
  try {
    await api("/api/lessons/complete", {
      method: "POST",
      json: { class_id: ids[0], ...(jointClasses ? { class_ids: ids } : {}), date, awards },
    });
    closeModal("modalFinish");
    draft = {};
    await loadState();
    justDone = state.lessons.find((l) => ids.includes(l.class_id))?.id ?? null;   // у общего урока это его group
    if (justDone) openLessons.add(justDone);
    renderLessons();
    toast(`Начислено ${total} ${stars(total)} · урок ${dotted(date)} сохранён в «Уроках»`, "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* ── Выдача приза (T2b) и ручное списание ─────────────────── */

function openGive(id) {
  rowPop = null;
  const student = studentById(id);
  if (!student) return;
  giveTarget = id;
  givePrize = null;
  $("giveAvatar").src = avatarUrl(student);
  $("giveName").textContent = student.name;
  $("giveBal").textContent = fmt(student.total_stars);
  renderGive();
  renderTable();
  openModal("modalGive");
}

function renderGive() {
  const student = studentById(giveTarget);
  if (!student) return;
  const bal = student.total_stars;
  const prizes = schoolPrizes(classById(student.class_id)?.school_id).filter((p) => p.active !== false)
    .slice().sort((a, b) => a.cost - b.cost);
  // Купил сам и ждёт приза — сначала выдать купленное, чтобы не списать звёзды второй раз
  const bought = (state.orders || []).filter((o) => o.student_id === student.id && o.status === "waiting");
  $("giveBought").hidden = !bought.length;
  $("giveBought").innerHTML = bought.length ? `<span class="caps">Купил сам — звёзды уже списаны</span>` + bought.map((o) => `
    <div class="give-item bought">
      ${o.prize_image ? `<img src="${escapeHtml(assetUrl(o.prize_image))}" alt="" loading="lazy">` : `<img alt="">`}
      <span class="gx"><span class="gn">${escapeHtml(o.prize_name)}</span><span class="gs">${escapeHtml(orderAgo(o.created_at))}</span></span>
      <span class="gp">${o.cost} ★</span>
      <button class="btn btn-sm" onclick="issueOrder(${o.id})">${icon("check", "ic ic-sm")}Выдан</button>
    </div>`).join("") : "";
  $("giveList").innerHTML = prizes.length ? prizes.map((p) => {
    const ok = bal >= p.cost;
    const sel = givePrize === p.id;
    const sub = !ok ? `не хватает ${p.cost - bal} ★` : sel ? `останется ${bal - p.cost} ★` : CATEGORY_NAMES[p.category] || "";
    return `<button type="button" class="give-item" role="radio" aria-checked="${sel}" aria-disabled="${!ok}"
                    onclick="${ok ? `pickGive(${p.id})` : ""}">
      <span class="radio"></span>
      ${p.image ? `<img src="${escapeHtml(assetUrl(p.image))}" alt="" loading="lazy">` : `<img alt="">`}
      <span class="gx"><span class="gn">${escapeHtml(p.name)}</span><span class="gs">${escapeHtml(sub)}</span></span>
      <span class="gp">${p.cost} ★</span>
    </button>`;
  }).join("") : `<div class="empty">Призов на витрине пока нет.</div>`;
  const prize = prizes.find((p) => p.id === givePrize);
  $("giveFrom").textContent = `${fmt(bal)} ★`;
  $("giveTo").textContent = `${fmt(prize ? bal - prize.cost : bal)} ★`;
  $("giveConfirm").disabled = !prize;
  $("giveBtnText").textContent = prize ? `Выдать и списать ${prize.cost} ★` : "Выберите приз";
}

function pickGive(id) { givePrize = id; renderGive(); }

async function confirmGive() {
  const student = studentById(giveTarget);
  const prize = (state.prizes || []).find((p) => p.id === givePrize);
  if (!student || !prize) return;
  try {
    await api("/api/students/deduct", {
      method: "POST",
      json: { student_id: student.id, amount: prize.cost, reason: `Приз: ${prize.name}`, prize_id: prize.id },
    });
    closeModal("modalGive");
    await loadState();
    toast(`Выдано: ${prize.name} · списано ${prize.cost} ★ · ${student.name}`, "success", "gift");
  } catch (error) {
    toast(error.message, "error");
  }
}

function openDeductFromGive() {
  const id = giveTarget;
  closeModal("modalGive");
  openDeduct(id);
}

function openDeduct(id) {
  rowPop = null;
  const student = studentById(id);
  if (!student) return;
  deductTarget = id;
  $("deductName").textContent = student.name;
  $("deductMax").textContent = fmt(student.total_stars);
  $("deductAmount").value = Math.min(5, student.total_stars) || 1;
  $("deductReason").value = "";
  renderTable();
  openModal("modalDeduct");
}

async function confirmDeduct() {
  const amount = parseInt($("deductAmount").value) || 0;
  const reason = $("deductReason").value.trim() || "Списание учителем";
  if (amount <= 0) return toast("Укажите количество звёзд", "error");
  try {
    await api("/api/students/deduct", { method: "POST", json: { student_id: deductTarget, amount, reason } });
    closeModal("modalDeduct");
    await loadState();
    toast(`Списано ${amount} ${stars(amount)}`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Уроки (T3): отмена ошибочного начисления ─────────────── */

function renderLessons() {
  const classes = sortedClasses(activeSchool);
  $("lessonClasses").innerHTML = classes.map((c) => `
    <button role="tab" aria-selected="${c.id === lessonClass}" onclick="pickLessonClass(${c.id})">${escapeHtml(c.name)}</button>`).join("");
  $("lessonClasses").hidden = classes.length < 2;

  const host = $("lessonList");
  const lessons = (state.lessons || []).filter((l) => l.class_id === lessonClass).slice(0, 60);
  if (!lessons.length) {
    host.innerHTML = `<div class="card empty">${icon("calendar", "ic ic-xl")}<b>Уроков пока нет</b>
      Здесь появится каждый завершённый урок — с тем, кому и сколько звёзд досталось.</div>`;
    return;
  }
  host.innerHTML = lessons.map(lessonHtml).join("");
}

function pickLessonClass(id) { lessonClass = id; renderLessons(); }

function lessonHtml(lesson) {
  const open = openLessons.has(lesson.id);
  const awards = lesson.awards || null;
  const undone = lesson.undone || {};
  const entries = awards
    ? [...Object.entries(awards).map(([id, v]) => [id, v, false]), ...Object.entries(undone).map(([id, v]) => [id, v, true])]
    : [];
  const count = awards ? Object.keys(awards).length : lesson.students_rewarded || 0;
  const [y, m, d] = lesson.date.split("-").map(Number);
  const weekday = new Date(y, m - 1, d).toLocaleDateString("ru-RU", { weekday: "long" });
  const items = entries.map(([id, value, isUndone]) => {
    const student = studentById(parseInt(id));
    const name = student ? escapeHtml(student.name) : "Ученик удалён";
    const button = !student || !canEdit() ? "" : isUndone
      ? `<button class="act-btn" onclick="restoreAward(${lesson.id}, ${id})">${icon("undo", "ic ic-sm")}Вернуть</button>`
      : `<button class="act-btn" onclick="undoAward(${lesson.id}, ${id})">${icon("undo", "ic ic-sm")}Отменить</button>`;
    return `<div class="lesson-item ${isUndone ? "undone" : ""}">
      <img src="${escapeHtml(avatarUrl(student))}" alt="">
      <span class="n">${name}</span>
      <span class="v">+${value} ★</span>
      <span class="s">${isUndone ? "отменено" : "начислено"}</span>
      <span class="b">${button}</span>
    </div>`;
  }).join("");
  const foot = awards
    ? `<div class="lesson-foot"><span>${canEdit() ? "Отмена списывает звёзды с баланса и убирает запись из истории ученика."
        : `Провёл: ${escapeHtml(staffName(lesson.user_id) || "не указано")}`}</span>
        ${Object.keys(awards).length && canEdit() ? `<button class="btn btn-danger-quiet btn-sm" onclick="undoLesson(${lesson.id})">${icon("undo", "ic ic-sm")}Отменить весь урок…</button>` : ""}</div>`
    : `<div class="lesson-foot"><span>Урок проведён до обновления: в нём не записано, кому сколько звёзд начислено, поэтому отменить его нельзя.</span></div>`;
  return `
    <section class="lesson">
      <button class="lesson-toggle" aria-expanded="${open}" onclick="toggleLesson(${lesson.id})">
        ${icon("chevron-right")}
        <span class="lesson-when">
          <span class="lesson-date">${escapeHtml(formatDay(lesson.date))}</span>
          <span class="lesson-meta">${escapeHtml(weekday)} · ${escapeHtml(lesson.class_name || classById(lesson.class_id)?.name || "")} · ${count} ${kids(count)}${lesson.joint_names ? ` · общий урок ${escapeHtml(lesson.joint_names)}` : ""}</span>
        </span>
        ${justDone && (lesson.id === justDone || lesson.group === justDone) ? `<span class="tag tag-accent">только что</span>` : ""}
        ${lessonRunsTag(lesson)}
        <span class="pill-star plus">${icon("star")}<b>+${lesson.total_stars}</b></span>
      </button>
      ${open ? `<div class="lesson-items">${lessonRunsRow(lesson)}${items}${foot}</div>` : ""}
    </section>`;
}

function toggleLesson(id) {
  if (openLessons.has(id)) openLessons.delete(id); else openLessons.add(id);
  renderLessons();
}

async function undoAward(lessonId, studentId) {
  const student = studentById(studentId);
  try {
    const r = await api(`/api/lessons/${lessonId}/undo`, { method: "POST", json: { student_id: studentId } });
    await loadState();
    toast(`Отменено: −${r.stars} ★ · ${student?.name || ""}`, "success", "undo");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function restoreAward(lessonId, studentId) {
  const student = studentById(studentId);
  try {
    const r = await api(`/api/lessons/${lessonId}/restore`, { method: "POST", json: { student_id: studentId } });
    await loadState();
    toast(`Возвращено: +${r.stars} ★ · ${student?.name || ""}`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function undoLesson(lessonId) {
  const lesson = state.lessons.find((l) => l.id === lessonId);
  if (!lesson) return;
  confirmAction(`Отменить урок ${escapeHtml(formatDay(lesson.date))}?`,
    `У всех учеников этого урока спишутся начисленные звёзды (<b>${lesson.total_stars} ★</b>), записи пропадут из их истории.`,
    async () => {
      const r = await api(`/api/lessons/${lessonId}/undo`, { method: "POST", json: {} });
      await loadState();
      toast(`Урок отменён: −${r.stars} ★`, "success", "undo");
    }, "Отменить урок");
}

/* ── Призы (T2) ───────────────────────────────────────────── */

/** Призы выбранной школы: общие и от её администрации. */
function schoolPrizes(schoolId = activeSchool) {
  return (state.prizes || []).filter((p) => !p.school_id || p.school_id === schoolId);
}

/** Менять приз: администратор системы — любой, администрация школы — только призы своей школы. */
function canEditPrize(prize) { return isAdmin() || (isSchoolAdmin() && Boolean(prize?.school_id)); }

let prizeScope = "all";          // витрина в панели: все, общие или только призы выбранной школы

function pickPrizeScope(scope) { prizeScope = scope; renderPrizes(); }

function renderPrizeScope(prizes) {
  const school = schoolById(activeSchool);
  const own = prizes.filter((p) => p.school_id).length;
  const scopes = [["all", "Все", prizes.length], ["common", "Общие", prizes.length - own],
    ["school", school ? `Только «${school.name}»` : "Только этой школы", own]];
  $("prizeScope").innerHTML = scopes.map(([key, label, count]) => `
    <button type="button" role="radio" aria-checked="${prizeScope === key}" onclick="pickPrizeScope('${key}')">
      <span class="ps-label">${escapeHtml(label)}</span><span class="ps-count">${count}</span></button>`).join("");
}

/* ── Покупки учеников: приз куплен в кабинете, звёзды списаны — ждёт выдачи ── */

/** Покупки учеников выбранной школы (сервер отдаёт только тем, кто выдаёт: учителям и администратору). */
function schoolOrders(status = null) {
  return (state.orders || []).filter((o) => {
    const student = studentById(o.student_id);
    return student && classById(student.class_id)?.school_id === activeSchool && (!status || o.status === status);
  });
}

function orderAgo(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = localToday();
  const day = iso.slice(0, 10);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return day === today ? `сегодня, ${time}` : `${formatDay(day, { year: false })}, ${time}`;
}

function orderRow(o, open) {
  const student = studentById(o.student_id);
  const cls = classById(student?.class_id)?.name || "";
  const name = escapeHtml(student?.name || "Ученик");
  const prize = escapeHtml(o.prize_name);
  const state = o.status === "issued" ? `<span class="ord-tag ok">${icon("check", "ic ic-xs")}выдан · ${escapeHtml(o.closed_by_name || "")}</span>`
    : o.status === "cancelled" ? `<span class="ord-tag off">${icon("refresh", "ic ic-xs")}отменена, звёзды вернулись${o.cancel_reason ? ` · ${escapeHtml(o.cancel_reason)}` : ""}</span>` : "";
  return `
    <div class="ord-line">
      <img class="av-sm" src="${escapeHtml(avatarUrl(student))}" alt="" loading="lazy">
      <span class="ord-who"><b>${name}</b><small>${escapeHtml(cls)} · ${escapeHtml(orderAgo(o.created_at))}</small></span>
      <span class="ord-prize">${icon("gift", "ic ic-sm")}<span>${prize}</span></span>
      <span class="pill-star">${icon("star")}<b>${fmt(o.cost)}</b></span>
      ${open ? `<span class="ord-acts">
        <button class="btn btn-sm" onclick="issueOrder(${o.id})">${icon("check", "ic ic-sm")}Выдан</button>
        <button class="btn btn-ghost btn-sm" onclick="cancelOrder(${o.id})" aria-label="Отменить покупку: ${name}, ${prize}">Отменить…</button>
      </span>` : state}
    </div>`;
}

function renderOrdersPanel() {
  const waiting = schoolOrders("waiting");
  const closed = schoolOrders().filter((o) => o.status !== "waiting").slice(0, 20);
  const box = $("ordersPanel");
  box.hidden = !rewardsHere() || !teachesHere() || (!waiting.length && !closed.length);
  const count = teachesHere() && rewardsHere() ? waiting.length : 0;
  $("prizesNavCount").textContent = count > 99 ? "99+" : String(count);
  $("prizesNavCount").hidden = !count;
  $("ordersHint").hidden = !count;
  $("ordersHint").innerHTML = `${icon("gift", "ic ic-sm")}<span><b>${count} ${plural(count, "приз ждёт", "приза ждут", "призов ждут")} выдачи</b> — ученики купили сами</span>${icon("chevron-right", "ic ic-sm")}`;
  if (box.hidden) return;
  box.innerHTML = `
    <h2 class="caps ord-title" id="ordersPanelTitle">Ждут выдачи · ${waiting.length}</h2>
    <p class="page-sub">Ученики купили сами — звёзды уже списаны. Отдайте приз и нажмите «Выдан»; если приза нет — «Отменить», звёзды вернутся.</p>
    ${waiting.length ? `<div class="card ord-list">${waiting.map((o) => orderRow(o, true)).join("")}</div>`
      : `<p class="muted ord-empty">Сейчас никто не ждёт приза.</p>`}
    ${closed.length ? `<details class="ord-done"><summary>Недавно выданы и отменены · ${closed.length}</summary>
      <div class="card ord-list">${closed.map((o) => orderRow(o, false)).join("")}</div></details>` : ""}`;
}

async function issueOrder(id) {
  const order = (state.orders || []).find((o) => o.id === id);
  try {
    await api(`/api/orders/${id}/issue`, { method: "POST" });
    await loadState();
    if ($("modalGive").classList.contains("open")) renderGive();
    toast(`Выдано: ${order?.prize_name || "приз"} · ${studentById(order?.student_id)?.name || ""}`, "success", "gift");
  } catch (error) {
    toast(error.message, "error");
    loadState().catch(() => {});
  }
}

function cancelOrder(id) {
  const order = (state.orders || []).find((o) => o.id === id);
  if (!order) return;
  const student = studentById(order.student_id);
  confirmAction("Отменить покупку?",
    `${escapeHtml(student?.name || "Ученику")} вернутся <b>${fmt(order.cost)} ★</b> за ${escapeHtml(quoted(order.prize_name))}, запись покупки исчезнет из истории.
     <label class="field ord-reason"><span class="field-label">Причина — ученик её увидит</span>
       <input type="text" id="cancelReason" maxlength="200" placeholder="Например: приз закончился"></label>`,
    async () => {
      await api(`/api/orders/${id}/cancel`, { method: "POST", json: { reason: $("cancelReason")?.value || "" } });
      await loadState();
      toast(`Покупка отменена — ${fmt(order.cost)} ★ вернулись ученику`, "success");
    }, "Отменить покупку");
}

/** Раз в минуту — не купил ли кто-то приз: счётчик в меню и блок «Ждут выдачи». */
async function pollOrders() {
  if (document.hidden || !state.me || !teachesHere() || !rewardsHere()) return;
  try {
    const { orders } = await api("/api/orders/waiting");
    const known = new Set((state.orders || []).filter((o) => o.status === "waiting").map((o) => o.id));
    const fresh = orders.map((o) => o.id);
    if (fresh.length !== known.size || fresh.some((id) => !known.has(id))) {
      if (!topModal() && !(currentPage() === "journal" && journalView === "class" && usedBank())) await loadState();
    }
  } catch (_) {
    // нет связи — проверим в следующий раз
  }
}

function renderPrizes() {
  const grid = $("prizeGrid");
  const all = schoolPrizes();
  renderPrizeScope(all);
  const prizes = all.filter((p) => prizeScope === "all" || (prizeScope === "common" ? !p.school_id : Boolean(p.school_id)));
  if (!prizes.length) {
    grid.innerHTML = `<div class="card empty" style="grid-column:1/-1">${icon("gift", "ic ic-xl")}<b>${all.length
      ? (prizeScope === "school" ? "У этой школы своих призов пока нет" : "Общих призов пока нет") : "Призов пока нет"}</b>${
      prizeScope === "school" && (isAdmin() || isSchoolAdmin()) ? "Добавьте приз — его увидят только дети этой школы." : ""}</div>`;
    return;
  }
  const blank = `<span class="no-photo">${icon("gift", "ic ic-xl")}</span>`;
  grid.innerHTML = prizes.map((prize) => {
    const hidden = prize.active === false;
    const photo = prize.image
      ? `<img src="${escapeHtml(assetUrl(prize.image))}" alt="" loading="lazy" decoding="async"
              data-blank="${escapeHtml(blank)}" onerror="this.outerHTML = this.dataset.blank">`
      : blank;
    const name = escapeHtml(prize.name);
    return `
      <article class="prize-card ${hidden ? "off" : ""}">
        <div class="cat-band cat-${escapeHtml(prize.category || "small")}" aria-hidden="true"></div>
        <div class="prize-photo">
          ${photo}
          <span class="price-badge">${icon("star")}<b>${prize.cost}</b></span>
          ${hidden ? `<span class="hidden-badge">${icon("eye-off")}Скрыт от детей</span>` : ""}
          ${prize.school_id ? `<span class="from-badge">${icon("school")}от администрации школы</span>` : ""}
          ${canEditPrize(prize) ? `<div class="prize-acts">
            <button onclick="openPrize(${prize.id})" aria-label="Изменить: ${name}" title="Изменить">${icon("pencil")}</button>
            <button onclick="togglePrize(${prize.id})" aria-label="${hidden ? "Показать детям" : "Скрыть от детей"}"
                    title="${hidden ? "Показать детям" : "Скрыть от детей"}">${icon(hidden ? "eye" : "eye-off")}</button>
            <button class="danger" onclick="removePrize(${prize.id})" aria-label="Удалить: ${name}" title="Удалить">${icon("trash")}</button>
          </div>` : ""}
        </div>
        <div class="prize-body">
          <h3>${name}</h3>
          <p>${escapeHtml(prize.desc || "")}</p>
          <span class="prize-cat">${CATEGORY_NAMES[prize.category] || ""}</span>
        </div>
      </article>`;
  }).join("");
}

/* Фотография в форме приза: файл выбирают сразу, а отправляем его
   уже после того, как приз создан и у него появился id. */

function renderPrizePhotoPreview(src) {
  const box = $("prizePhotoPreview");
  const ratio = cssToken("--photo-ar").replace(/\s/g, "").replace("/", ":") || "4:3";
  box.innerHTML = src
    ? `<img src="${escapeHtml(src)}" alt="Превью фото приза">`
    : `<span class="no-photo">${icon("image", "ic ic-lg")}нет фото · ${escapeHtml(ratio)}</span>`;
  $("prizePhotoClear").hidden = !src;
}

function releasePrizePhotoPreview() {
  if (prizePhotoPreviewUrl) {
    URL.revokeObjectURL(prizePhotoPreviewUrl);
    prizePhotoPreviewUrl = null;
  }
}

function stagePrizePhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > MAX_PHOTO_BYTES) return toast("Файл больше 5 МБ — выберите фото поменьше", "error");
  releasePrizePhotoPreview();
  prizePhotoFile = file;
  prizePhotoCleared = false;
  prizePhotoPreviewUrl = URL.createObjectURL(file);
  renderPrizePhotoPreview(prizePhotoPreviewUrl);
}

function clearPrizePhoto() {
  releasePrizePhotoPreview();
  prizePhotoFile = null;
  prizePhotoCleared = true;
  renderPrizePhotoPreview("");
}

async function sendPrizePhoto(prizeId, file) {
  const form = new FormData();
  form.append("file", file);
  await api(`/api/prizes/${prizeId}/photo`, { method: "POST", body: form });
}

function pickCategory(cat) {
  prizeCategory = cat;
  document.querySelectorAll("#prizeCategory button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.cat === cat)));
}

function updateDescLen() { $("prizeDescLen").textContent = `${$("prizeDesc").value.length} / 60`; }

/** Подпись окна приза: чей он и кто его увидит. */
function updatePrizeOwner() {
  const owner = isAdmin() ? Number($("prizeSchool").value) || null
    : prizeTarget ? state.prizes.find((p) => p.id === prizeTarget)?.school_id : activeSchool;
  const name = schoolById(owner)?.name || "";
  $("prizeModalSub").textContent = owner
    ? `Приз школы «${name}»: его увидят только дети этой школы, с пометкой «от администрации школы».`
    : "Общий приз: его видят дети всех школ.";
}

function openPrize(id = null) {
  prizeTarget = id;
  const prize = id ? state.prizes.find((p) => p.id === id) : null;
  $("prizeModalTitle").textContent = prize ? "Изменить приз" : "Новый приз";
  if (isAdmin()) {
    // По умолчанию — общий; из фильтра «Только этой школы» — приз выбранной школы
    const owner = prize ? prize.school_id : prizeScope === "school" ? activeSchool : null;
    $("prizeSchool").innerHTML = `<option value="">Все школы — общий приз</option>` + state.schools
      .map((s) => `<option value="${s.id}">${escapeHtml(s.name)}${s.rewards === false ? " (без системы наград)" : ""}</option>`).join("");
    $("prizeSchool").value = owner ? String(owner) : "";
  }
  updatePrizeOwner();
  $("prizeName").value = prize?.name || "";
  $("prizeCost").value = prize?.cost || 30;
  $("prizeDesc").value = (prize?.desc || "").slice(0, 60);
  updateDescLen();
  pickCategory(prize?.category || "small");
  releasePrizePhotoPreview();
  prizePhotoFile = null;
  prizePhotoCleared = false;
  renderPrizePhotoPreview(assetUrl(prize?.image || ""));
  openModal("modalPrize");
}

async function savePrize() {
  const payload = {
    name: $("prizeName").value.trim(),
    cost: parseInt($("prizeCost").value) || 1,
    desc: $("prizeDesc").value.trim(),
    category: prizeCategory,
  };
  if (!payload.name) return toast("Введите название приза", "error");
  try {
    let prizeId = prizeTarget;
    // Чей приз: администратор выбирает в окне; администрация школы — для школы, выбранной в меню
    const owner = isAdmin() ? { school_id: Number($("prizeSchool").value) || null } : prizeTarget ? {} : { school_id: activeSchool };
    if (prizeTarget) {
      await api(`/api/prizes/${prizeTarget}`, { method: "PATCH", json: { ...payload, ...owner } });
    } else {
      const created = await api("/api/prizes", { method: "POST", json: { ...payload, active: true, ...owner } });
      prizeId = created.prize.id;
    }
    if (prizePhotoFile) {
      toast("Загружаем фотографию…");
      await sendPrizePhoto(prizeId, prizePhotoFile);
    } else if (prizePhotoCleared && prizeTarget) {
      await api(`/api/prizes/${prizeTarget}/photo`, { method: "DELETE" });
    }
    releasePrizePhotoPreview();
    prizePhotoFile = null;
    prizePhotoCleared = false;
    closeModal("modalPrize");
    await loadState();
    toast("Приз сохранён — дети увидят его в витрине", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function togglePrize(id) {
  const prize = state.prizes.find((p) => p.id === id);
  try {
    await api(`/api/prizes/${id}`, { method: "PATCH", json: { active: prize.active === false } });
    await loadState();
  } catch (error) {
    toast(error.message, "error");
  }
}

function removePrize(id) {
  const prize = state.prizes.find((p) => p.id === id);
  confirmAction(`Удалить приз ${quoted(escapeHtml(prize?.name || ""))}?`,
    "Он исчезнет из витрины у всех детей. Если приз временно закончился — лучше скрыть его.",
    async () => {
      await api(`/api/prizes/${id}`, { method: "DELETE" });
      await loadState();
      toast("Приз удалён", "success");
    }, "Удалить");
}

/* ── Импорт и экспорт ─────────────────────────────────────── */

function openPaste() {
  if (!activeSchool) return toast("Сначала добавьте школу", "error");
  $("pasteArea").value = "";
  $("pasteSchoolName").textContent = schoolById(activeSchool)?.name || "—";
  openModal("modalPaste");
}

function parseLines(text) {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const fallbackClass = classById(activeClass)?.name || "5-А";
  const items = [];
  lines.forEach((line, index) => {
    const separator = line.includes("\t") ? "\t" : line.includes(";") ? ";" : ",";
    const cols = line.split(separator).map((c) => c.trim().replace(/^"|"$/g, ""));
    if (!cols.length) return;
    const first = cols[0].toLowerCase();
    if (index === 0 && (first.includes("класс") || first.includes("группа") || first.includes("фио"))) return;

    let className = fallbackClass;
    let name = "";
    let starsCount = 0;
    if (cols.length >= 3) {
      className = cols[0] || className;
      name = cols[1];
      starsCount = parseInt(cols[2]) || 0;
    } else if (cols.length === 2) {
      if (!isNaN(parseInt(cols[1]))) {
        name = cols[0];
        starsCount = parseInt(cols[1]) || 0;
      } else {
        className = cols[0] || className;
        name = cols[1];
      }
    } else {
      name = cols[0];
    }
    if (name) items.push({ school_id: activeSchool, class_name: className, name, total_stars: starsCount });
  });
  return items;
}

async function processPaste() {
  const items = parseLines($("pasteArea").value);
  if (!items.length) return toast("Не удалось распознать строки", "error");
  closeModal("modalPaste");
  try {
    const result = await api("/api/students/bulk", { method: "POST", json: items });
    await loadState();
    toast(`Добавлено учеников: ${result.added}`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Меню ─────────────────────────────────────────────────── */

function toggleMenu(id, event) {
  event?.stopPropagation();
  const wrap = $(id);
  const open = !wrap.classList.contains("open");
  closeMenus();
  wrap.classList.toggle("open", open);
  wrap.querySelector("button")?.setAttribute("aria-expanded", String(open));
}

function closeMenus(withRows = true) {
  document.querySelectorAll(".menu-wrap.open").forEach((wrap) => {
    wrap.classList.remove("open");
    wrap.querySelector("button")?.setAttribute("aria-expanded", "false");
  });
  const classMenu = $("classMenu");
  if (classMenu) { classMenu.style.display = "none"; classMenu.classList.remove("shown"); }
  $("navRest")?.classList.remove("open");
  $("navMore")?.setAttribute("aria-expanded", "false");
  if (withRows && rowPop) { rowPop = null; renderTable(); }
  if (!rowPop) document.querySelectorAll("body > .row-float").forEach((el) => el.remove());
  if (typeof closeCellPop === "function") closeCellPop();      // окошко ячейки расписания
}

function anyPopOpen() {
  return Boolean(rowPop) || (typeof cellPopOpen === "function" && cellPopOpen());
}

/** Скачивание файла с сервера: cookie сессии уходит сама, как у обычной ссылки. */
function downloadFile(path) {
  closeMenus();
  const link = document.createElement("a");
  link.href = BASE + path;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadTemplate() { downloadFile("/api/excel/template"); }

function exportExcel() {
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  downloadFile(`/api/excel/export?school_id=${activeSchool}`);
}

async function uploadExcel(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  closeMenus();
  if (!file) return;
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  if (!/\.xlsx$/i.test(file.name)) return toast("Нужен файл Excel .xlsx — скачайте шаблон и заполните его", "error");

  const form = new FormData();
  form.append("file", file);
  const className = encodeURIComponent(classById(activeClass)?.name || "");
  try {
    const result = await api(`/api/excel/import?school_id=${activeSchool}&class_name=${className}`,
                             { method: "POST", body: form });
    await loadState();
    const added = `${plural(result.added, "Добавлен", "Добавлено", "Добавлено")} ` +
                  `${result.added} ${kids(result.added)}`;
    if (result.skipped.length) {
      showInfo("Список загружен не целиком",
        `<p>${added}. Эти строки пропущены — исправьте их в файле и загрузите только их:</p>
         <ul>${result.skipped.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`);
    } else {
      toast(added, "success");
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

/** Окно с сообщением. html собирается только из экранированных частей. */
function showInfo(title, html) {
  $("infoTitle").textContent = title;
  $("infoBody").innerHTML = html;
  openModal("modalInfo");
}

/* ── Учителя (только администратор) ──────────────────────── */

/** Пароль сотрудника виден администратору сразу, пока сотрудник не сменит его сам. */
/* ── Мой профиль ──────────────────────────────────────────── */

/** Блок профиля внизу меню: аватар, имя, роль. */
function renderMe() {
  const me = state.me;
  if (!me) return;
  $("meAvatar").innerHTML = staffAvatar(me);
  $("meName").textContent = me.name;
  $("meRole").textContent = postsLabel(me);
  $("meBtn").title = `${me.name} — мой кабинет`;
}

/** Уроки, звёзды и классы сотрудника за месяц. Общий урок (несколько записей с одним group)
 *  считается одним уроком — как в статистике. */
function monthWork(userId, now = new Date()) {
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const mine = (state.lessons || []).filter((l) => l.user_id === userId && String(l.date).startsWith(month));
  return {
    lessons: new Set(mine.map((l) => (l.group ? `g${l.group}` : `l${l.id}`))).size,
    stars: mine.reduce((sum, l) => sum + (l.total_stars || 0), 0),
    classes: new Set(mine.map((l) => l.class_id)).size,
  };
}

async function uploadMyPhoto(input) {
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api("/api/auth/teacher/photo", { method: "POST", body: form });
    await loadState();
    renderCabinet();
    toast("Фото обновлено", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function dropMyPhoto() {
  try {
    await api("/api/auth/teacher/photo", { method: "DELETE" });
    await loadState();
    renderCabinet();
    toast("Фото убрано — снова инициалы", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function dropUserPhoto(id) {
  try {
    await api(`/api/users/${id}/photo`, { method: "DELETE" });
    await loadState();
    openUser(id);
    toast("Фото сотрудника убрано", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Пароль сотрудника ───────────────────────────────────────
   Открытый пароль хранится, пока сотрудник не сменит его сам. Если его нет (сменил сам,
   учётная запись старая или задана в настройках сервера) — показать нельзя, только выдать новый. */

function staffPassword(user) {
  if (user.role === "superadmin") return `<span class="muted">—</span>`;
  if (!user.password_shown) {
    return `<span class="pw-unknown"><span class="muted" title="Пароль хранится зашифрованным — показать его нельзя">не сохранён</span>
      <button class="btn btn-link btn-sm" onclick="resetUserPassword(${user.id})">${icon("key", "ic ic-sm")}Выдать новый</button></span>`;
  }
  return `<span class="pw-cell"><span class="mono">${escapeHtml(user.password_shown)}</span>
    <button class="icon-btn" title="Скопировать пароль" aria-label="Скопировать пароль: ${escapeHtml(user.name)}"
            onclick="copyStaffPassword(${user.id})">${icon("copy", "ic ic-sm")}</button></span>`;
}

/** Строка «Пароль» в окне сотрудника. */
function staffPasswordBox(user) {
  if (user.password_shown) {
    return `<div class="pw-box"><span class="mono">${escapeHtml(user.password_shown)}</span>
      <button class="btn btn-ghost btn-sm" onclick="copyStaffPassword(${user.id})">${icon("copy", "ic ic-sm")}Скопировать</button></div>
      <p class="field-hint">Виден, пока сотрудник не сменит пароль сам.</p>`;
  }
  return `<div class="pw-box"><span class="muted">не сохранён</span>
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modalUser'); resetUserPassword(${user.id})">${icon("key", "ic ic-sm")}Выдать новый пароль</button></div>
    <p class="field-hint">Пароль хранится зашифрованным: сотрудник сменил его сам, или учётная запись
      создана раньше, чем пароли начали сохранять. Посмотреть его нельзя — выдайте новый, и он будет виден здесь.</p>`;
}

async function copyStaffPassword(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user?.password_shown) return;
  try {
    await navigator.clipboard.writeText(user.password_shown);
    toast("Пароль скопирован", "success");
  } catch {
    toast(`Пароль: ${user.password_shown}`, "success", "key");
  }
}

/** Должности сотрудника тегами: по всем школам, без повторов. */
function postTags(user) {
  const all = new Set(Object.values(user.posts || {}).flat());
  return Object.keys(POSTS).filter((p) => all.has(p)).map((p) =>
    `<span class="tag ${ADMIN_POSTS.includes(p) ? "tag-accent" : ""}" title="${POSTS[p]}">${POSTS_SHORT[p]}</span>`).join("");
}

/** Школы сотрудника; если в разных школах разные должности — они в теге школы. */
function schoolTags(user) {
  const entries = Object.entries(user.posts || {}).filter(([id]) => schoolById(Number(id)));
  const same = new Set(entries.map(([, posts]) => posts.join())).size <= 1;
  return entries.map(([id, posts]) => {
    const name = schoolById(Number(id)).name;
    const label = same ? name : `${name} · ${posts.map((p) => POSTS_SHORT[p]).join(", ")}`;
    return `<span class="tag" title="${escapeHtml(`${name}: ${posts.map((p) => POSTS[p]).join(", ")}`)}">${escapeHtml(label)}</span>`;
  }).join("");
}

function renderUsers() {
  const rank = (u) => u.role === "superadmin" ? 0
    : Object.values(u.posts || {}).flat().some((p) => ADMIN_POSTS.includes(p)) ? 1 : 2;
  const users = (state.users || []).slice()
    .sort((a, b) => (rank(a) - rank(b)) || a.name.localeCompare(b.name, "ru"));
  const rows = users.map((user) => {
    const admin = user.role === "superadmin";
    const self = user.id === state.me?.id;
    const schools = admin ? `<span class="muted">все школы</span>` : schoolTags(user) || `<span class="muted">нет школ</span>`;
    const roleTag = admin ? `<span class="tag tag-star">администратор</span>` : postTags(user) || `<span class="muted">—</span>`;
    const badges = checkinBadges(user);
    return `
      <div class="user-row">
        <span class="n">${staffAvatar(user)}<span class="n-text"><span>${escapeHtml(user.name)}${self ? ` <span class="muted">(вы)</span>` : ""}</span>${badges ? `<span class="user-sub">${badges}</span>` : ""}<span class="user-now" data-user="${user.id}" hidden></span></span></span>
        <span class="login-cell"><span class="code-chip" title="${escapeHtml(user.login)}">${escapeHtml(user.login)}</span></span>
        <span>${staffPassword(user)}</span>
        <span class="tag-row">${roleTag}</span>
        <span class="tag-row">${schools}</span>
        <span class="acts">
          <button class="icon-btn boxed" title="${admin ? "Имя и отметки" : "Имя, должности в школах и отметки"}" aria-label="Изменить" onclick="openUser(${user.id})">${icon("pencil", "ic ic-sm")}</button>
          ${self ? "" : `<button class="icon-btn boxed" title="Выдать новый пароль" aria-label="Новый пароль" onclick="resetUserPassword(${user.id})">${icon("key", "ic ic-sm")}</button>`}
          ${self ? "" : `<button class="icon-btn boxed danger" title="Удалить" aria-label="Удалить" onclick="removeUser(${user.id})">${icon("trash", "ic ic-sm")}</button>`}
        </span>
      </div>`;
  }).join("");
  $("userList").innerHTML = `
    <div class="users-head"><span>Имя</span><span>Логин</span><span>Пароль</span><span>Должности</span><span>Школы</span><span></span></div>${rows}
    <p class="users-empty" id="usersEmpty" hidden>Сейчас на работе никого нет — ни уроков, ни отметок.</p>`;
  renderPresence();          // где сотрудник сейчас и отметка за сегодня — static/js/bell.js
}

/* Окно сотрудника: должности по школам — в каждой строке школы переключатели. */
let userPosts = {};          // { "<id школы>": Set(должности) } — черновик окна

function renderUserPosts() {
  $("userPosts").innerHTML = state.schools.map((s) => {
    const mine = userPosts[String(s.id)] || new Set();
    return `
      <div class="post-row">
        <span class="post-school" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
        <div class="post-chips" role="group" aria-label="Должности: ${escapeHtml(s.name)}">
          ${Object.keys(POSTS).map((post) => `<button type="button" aria-pressed="${mine.has(post)}"
              title="${POSTS[post]}" onclick="toggleUserPost(${s.id}, '${post}')">${POSTS_SHORT[post]}</button>`).join("")}
        </div>
      </div>`;
  }).join("") || `<span class="muted">Сначала добавьте школу</span>`;
  // Отметки в боте — у тех, кто ведёт уроки
  const target = (state.users || []).find((u) => u.id === userTarget);
  const teaches = Object.values(userPosts).some((set) => set.has("teacher"));
  $("userCheckinField").hidden = target?.role !== "superadmin" && !teaches;
}

function toggleUserPost(school, post) {
  const key = String(school);
  const set = userPosts[key] || new Set();
  if (set.has(post)) set.delete(post); else set.add(post);
  userPosts[key] = set;
  renderUserPosts();
}

function postsPayload() {
  const out = {};
  for (const [key, set] of Object.entries(userPosts)) if (set.size) out[key] = Object.keys(POSTS).filter((p) => set.has(p));
  return out;
}

/** preset — для «Новый сотрудник» из раздела «Школы»: { school, post }. */
function openUser(id = null, preset = null) {
  userTarget = id;
  const user = id ? (state.users || []).find((u) => u.id === id) : null;
  const admin = user?.role === "superadmin";
  $("userModalTitle").textContent = !user ? "Новый сотрудник" : admin ? "Администратор" : user.name;
  $("userName").value = user?.name || "";
  $("userLogin").value = user?.login || "";
  $("userLogin").disabled = Boolean(user);
  $("userPassword").value = "";
  $("userPasswordField").hidden = Boolean(user);
  $("userPassInfo").hidden = !user || admin;
  if (user && !admin) {
    $("userPassBox").innerHTML = staffPasswordBox(user) + (user.avatar?.type === "photo"
      ? `<button class="btn btn-quiet btn-sm user-photo-drop" onclick="dropUserPhoto(${user.id})">${staffAvatar(user)}Убрать фото сотрудника</button>` : "");
  }
  $("userTracked").checked = Boolean(user?.checkin_tracked);
  $("userRate").value = user?.hourly_rate ?? "";
  renderUserTelegram(user);
  // Администратор работает во всех школах — должности ему не нужны
  $("userPostsField").hidden = admin;
  userPosts = {};
  if (user) {
    for (const [key, posts] of Object.entries(user.posts || {})) userPosts[key] = new Set(posts);
  } else if (preset?.school || activeSchool) {
    userPosts[String(preset?.school || activeSchool)] = new Set([preset?.post || "teacher"]);
  }
  renderUserPosts();
  openModal("modalUser");
}

async function saveUser() {
  const name = $("userName").value.trim();
  const login = $("userLogin").value.trim().toLowerCase();
  const posts = postsPayload();
  if (!name) return toast("Введите имя", "error");
  let checkin;
  try {
    checkin = { checkin_tracked: $("userTracked").checked, hourly_rate: readRate() };
  } catch (error) {
    return toast(error.message, "error");
  }
  const target = userTarget ? (state.users || []).find((u) => u.id === userTarget) : null;
  const admin = target?.role === "superadmin";
  // Не ведёт уроков — и отмечаться не в чем
  if (!admin && !Object.values(posts).some((list) => list.includes("teacher"))) checkin = { checkin_tracked: false };
  try {
    if (userTarget) {
      const json = admin ? { name, ...checkin } : { name, posts, ...checkin };
      await api(`/api/users/${userTarget}`, { method: "PATCH", json });
      closeModal("modalUser");
      await loadState();
      toast("Сохранено", "success");
    } else {
      if (!login) return toast("Введите логин", "error");
      const result = await api("/api/users", {
        method: "POST", json: { name, login, posts, password: $("userPassword").value || null },
      });
      if (checkin.checkin_tracked || checkin.hourly_rate !== null && checkin.hourly_rate !== undefined) {
        await api(`/api/users/${result.user.id}`, { method: "PATCH", json: checkin });
      }
      closeModal("modalUser");
      await loadState();
      showCredentials(result.user, result.password);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function showCredentials(user, password) {
  showInfo("Данные для входа", `
    <p>Передайте сотруднику. Пароль виден в списке «Сотрудники», пока сотрудник не сменит его сам.
      На карточке сотрудника — то же и QR в Telegram-бот, как у учеников.</p>
    <div class="cred-box">
      <span>сайт</span><b>${escapeHtml(location.host + BASE)}/login</b>
      <span>логин</span><b>${escapeHtml(user.login)}</b>
      <span>пароль</span><b>${escapeHtml(password)}</b>
      ${user.bot_code ? `<span>код для бота</span><b>${escapeHtml(codeText(user.bot_code))}</b>` : ""}
    </div>
    <a class="btn btn-sm" href="${BASE}/cards/staff/${user.id}" target="_blank" rel="noopener">${icon("printer", "ic ic-sm")}Карточка сотрудника</a>`);
}

function resetUserPassword(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Выдать новый пароль?",
    `${escapeHtml(user.name)}: старый пароль перестанет работать, все входы завершатся.`,
    async () => {
      const result = await api(`/api/users/${id}/password`, { method: "POST", json: {} });
      await loadState();
      showCredentials(user, result.password);
    }, "Выдать пароль");
}

function removeUser(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction(`Удалить сотрудника ${escapeHtml(user.name)}?`,
    "Войти под этим логином больше не получится. Классы, ученики и расписание останутся.",
    async () => {
      await api(`/api/users/${id}`, { method: "DELETE" });
      await loadState();
      toast("Сотрудник удалён", "success");
    }, "Удалить");
}

/* ── Свой пароль ──────────────────────────────────────────── */

function openMyPassword() {
  ["myCurrent", "myNew", "myRepeat"].forEach((id) => { $(id).value = ""; });
  openModal("modalMyPassword");
}

async function saveMyPassword() {
  const current = $("myCurrent").value;
  const fresh = $("myNew").value;
  if (!current || !fresh) return toast("Заполните все поля", "error");
  if (fresh !== $("myRepeat").value) return toast("Новые пароли не совпадают", "error");
  try {
    await api("/api/auth/teacher/password", { method: "POST", json: { current, new: fresh } });
    closeModal("modalMyPassword");
    toast("Пароль изменён", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Навигация и старт ────────────────────────────────────── */

function currentPage() { return document.querySelector(".page.active")?.id.replace("page-", "") || "journal"; }

/** Раздел из меню. Повторное нажатие «Журнал» из журнала класса — назад к плиткам. */
function switchPage(name) {
  if (name === "journal" && currentPage() === "journal" && journalView === "class") { closeMenus(); return goJournalHome(); }
  if (name === "schools") schoolView = null;          // из меню — список школ
  showPage(name);
  setRoute();
}

function showPage(name) {
  if (!rewardsHere() && REWARD_PAGES.includes(name)) name = homePage();
  if (!canEdit() && name === "journal") name = rewardsHere() ? "stats" : "schedule";
  if (!isSchoolAdmin() && name === "cards") name = homePage();
  if (!isAdmin() && (name === "users" || name === "schools")) name = homePage();
  closeMenus();
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${name}`));
  document.querySelectorAll(".nav button[data-page]").forEach((button) => {
    if (button.dataset.page === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  // На телефоне раздел из листа «Ещё» подсвечивает саму кнопку «Ещё»
  $("navMore").classList.toggle("on", Boolean($("navRest").querySelector(`[data-page="${name}"]`)));
  renderPanel();
  if (name === "journal") renderJournal();
  if (name === "cards") renderCardsPage();
  if (name === "schools") renderSchoolsPage();
  if (name === "users") loadPresence();
  if (name === "me") loadCabinet();
  if (name === "stats") loadStats();
  if (name === "schedule") renderSchedule();
  if (name === "checkin") loadCheckin();
  if (name === "monitor") loadMonitor();
  if (name === "lessons") loadLessonRuns();
  window.scrollTo(0, 0);
}

/* Адрес помнит место: #schedule, #journal, #journal/12 (класс), #journal/12+13 (общий урок),
   #schools/3 (карточка школы).
   После обновления страницы открывается то же самое, «Назад» в браузере работает. */
function routeHash() {
  const page = currentPage();
  if (page === "schools") return schoolView ? `#schools/${schoolView}` : "#schools";
  if (page !== "journal") return `#${page}`;
  return journalView === "class" ? `#journal/${(jointClasses || [activeClass]).join("+")}` : "#journal";
}

function setRoute(replace = false) {
  const hash = routeHash();
  if (location.hash === hash) return;
  history[replace ? "replaceState" : "pushState"](null, "", location.pathname + location.search + hash);
}

function applyRoute() {
  const [page, arg] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (!page || !$(`page-${page}`)) return;
  if (page === "schools") {
    const id = Number(arg);
    schoolView = arg && schoolById(id) ? id : null;
    if (schoolView && schoolView !== activeSchool) useSchool(schoolView);
    if (schoolView) loadCheckinRules();
  }
  if (page === "journal") {
    const known = sortedClasses(activeSchool).map((c) => c.id);
    const wanted = String(arg || "").split("+").map(Number);
    const ids = known.filter((id) => wanted.includes(id));
    const open = journalView === "class" ? (jointClasses || [activeClass]).join("+") : "";
    if (open !== ids.join("+")) {
      draft = {};
      panelId = null;
      rowPop = null;
      jointClasses = ids.length > 1 ? ids : null;
      journalView = ids.length ? "class" : "home";
      if (ids.length) { activeClass = ids[0]; lessonClass = ids[0]; }
    }
  }
  showPage(page);
  if (page === "journal") renderTable();
}

/** «Назад» и «Вперёд» в браузере. С неначисленными звёздами сначала спрашиваем. */
function onHistoryMove() {
  if (location.hash === routeHash()) return;
  if (journalView === "class" && usedBank()) {
    const target = location.href;
    history.pushState(null, "", location.pathname + location.search + routeHash());   // пока остаёмся
    return leaveDraftGuard(() => { history.replaceState(null, "", target); applyRoute(); });
  }
  applyRoute();
}

/** Верхний край для всплывающих окошек: на телефоне сверху липкая строка с названием. */
function popTopEdge() {
  // Только телефон: там .side — строка сверху. На широком экране .side — колонка слева
  // во всю высоту (тоже sticky), и её нижний край — это низ экрана, а не верх.
  if (!window.matchMedia("(max-width: 700px)").matches) return 0;
  const side = $("side");
  return side ? Math.max(0, side.getBoundingClientRect().bottom) : 0;
}

/** Лист «Ещё» нижней панели на телефоне. */
function toggleNavMore(event) {
  event?.stopPropagation();
  const rest = $("navRest");
  const open = !rest.classList.contains("open");
  closeMenus();
  rest.classList.toggle("open", open);
  $("navMore").setAttribute("aria-expanded", String(open));
}

async function logout() {
  await fetch(BASE + "/api/auth/teacher/logout", { method: "POST" });
  // Вышли сами — без входа через Telegram заново; из бота панель снова откроется кнопкой «Панель»
  try { sessionStorage.removeItem("zv_tg"); } catch (_) {}
  location.href = BASE + "/login";
}

document.addEventListener("DOMContentLoaded", async () => {
  $("lessonDate").value = localToday();
  renderDate();
  $("newStudentName").addEventListener("keydown", (e) => { if (e.key === "Enter") addStudent(); });
  $("schoolName").addEventListener("keydown", (e) => { if (e.key === "Enter") saveSchool(); });
  $("classNameInput").addEventListener("keydown", (e) => { if (e.key === "Enter") saveClassName(); });
  // Путь события, а не e.target.closest(): окошко ячейки перерисовывается по клику,
  // и к этому моменту нажатая кнопка уже вне страницы — closest() её «потерял» бы.
  const insidePop = ".menu-wrap, #classMenu, .cell-login, .cell-act, .row-float, .sg-cell, #cellPop, #navRest, #navMore";
  document.addEventListener("click", (e) => {
    if (!e.composedPath().some((el) => el.matches?.(insidePop))) closeMenus();
  });
  // Окошки строки и ячейки стоят поверх страницы — при прокрутке и смене размера закрываем их
  window.addEventListener("scroll", () => { if (anyPopOpen()) closeMenus(); }, { passive: true });
  window.addEventListener("resize", () => { if (anyPopOpen()) closeMenus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const modal = topModal();
      if (!modal && liveRunId) return closeLive();
      if (modal) return closeModal(modal.id);
      if (anyPopOpen() || document.querySelector(".menu-wrap.open, #classMenu.shown, #navRest.open")) return closeMenus();
      if (panelId) return closePanel();
      // Из журнала класса Esc возвращает к плиткам (не из поля ввода — там Esc отменяет ввод)
      if (currentPage() === "journal" && journalView === "class" && !e.target.closest?.("input, select, textarea")) return goJournalHome();
    }
    // Ctrl+Enter — итог урока из любого места журнала класса
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && currentPage() === "journal" && journalView === "class" && !topModal() && usedBank()) {
      e.preventDefault();
      openFinish();
    }
  });
  // Сменилась тема (выбор школы или окно «Оформление») — у тем разная разметка журнала
  document.addEventListener("themechange", () => {
    if (!hasPanel()) panelId = null;
    renderTable();
    renderPanel();
  });

  // Вернулись на вкладку — проверим, не сменился ли вход в другой вкладке
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sessionSwitched(); });
  window.addEventListener("popstate", onHistoryMove);
  // Закрытие или обновление вкладки с неначисленными звёздами — браузер переспросит
  window.addEventListener("beforeunload", (e) => {
    if (journalView === "class" && usedBank()) { e.preventDefault(); e.returnValue = ""; }
  });
  // «Идёт сейчас» и «следующий» на плитках зависят от часов
  setInterval(() => {
    if (!document.hidden && currentPage() === "journal" && journalView === "home") renderJournalHome();
  }, 60000);
  startBellClock();          // звонок — каждую секунду, «Сотрудники» — раз в 30 секунд
  setInterval(pollOrders, 60000);   // покупки учеников — раз в минуту

  // Кнопка «Открыть в панели» из группы «Отметки» ведёт на /teacher#checkin-lesson-<номер>
  window.addEventListener("hashchange", openCheckinFromHash);

  try {
    await loadState();
    applyRoute();
    setRoute(true);
    reportSwitchedLogin();
    openCheckinFromHash();
    openLiveFromAddress();
  } catch (error) {
    toast(error.message, "error");
  }
});
