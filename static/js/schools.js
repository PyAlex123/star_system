/* Раздел «Школы» — только администратор. Плитки школ и карточка школы: сотрудники
   с должностями (добавить из общего списка или создать нового), место на карте для отметок,
   время уроков, оформление и правила отметок. Адреса: #schools и #schools/<id>.
   Подключается после teacher.js, schedule.js и checkin.js. Права проверяет сервер. */

let schoolView = null;          // открытая карточка школы (id) или null — список школ
let schoolStaffPick = null;     // окно «Сотрудник в школе»: { school, user, edit, posts: Set }
let checkinRules = null;        // правила отметок для карточки: { before, grace }

function schoolStaff(id) {
  return (state.users || []).filter((u) => u.role !== "superadmin" && postsOf(u, id).length);
}

function schoolCounts(id) {
  const classIds = new Set(state.classes.filter((c) => c.school_id === id).map((c) => c.id));
  return {
    classes: classIds.size,
    students: state.students.filter((s) => classIds.has(s.class_id)).length,
    staff: schoolStaff(id).length,
  };
}

function countsLine(id) {
  const n = schoolCounts(id);
  return `${n.classes} ${plural(n.classes, "класс", "класса", "классов")} · ${n.students} ${kids(n.students)} · `
    + `${n.staff} ${plural(n.staff, "сотрудник", "сотрудника", "сотрудников")}`;
}

function hasPoint(school) { return school?.lat !== null && school?.lat !== undefined; }

function bellsSummary(school) {
  const bells = school.bells || [];
  const days = (school.days || 5) >= 6 ? "6 учебных дней" : "5 учебных дней";
  if (!bells.length) return `Время уроков не задано · ${days}`;
  return `${bells.length} ${plural(bells.length, "урок", "урока", "уроков")}, ${bells[0].start}–${bells[bells.length - 1].end} · ${days}`;
}

/* ── Список и карточка ───────────────────────────────────── */

function renderSchoolsPage() {
  if (!isAdmin() || !$("page-schools")) return;
  const school = schoolView ? schoolById(schoolView) : null;
  if (schoolView && !school) schoolView = null;
  $("schoolsHome").hidden = Boolean(school);
  $("schoolCard").hidden = !school;
  if (school) return renderSchoolCard(school);
  $("schoolTiles").innerHTML = state.schools.map((s) => {
    const directors = schoolStaff(s.id).filter((u) => postsOf(u, s.id).includes("director")).map((u) => u.name);
    return `
      <button type="button" class="school-tile" onclick="openSchoolCard(${s.id})" aria-label="${escapeHtml(s.name)}: открыть школу">
        <span class="st-top"><span class="st-swatch" data-theme="${escapeHtml(s.theme || "notebook")}" aria-hidden="true"></span>
          <b>${escapeHtml(s.name)}</b></span>
        <span class="st-meta">${countsLine(s.id)}</span>
        <span class="st-meta">${directors.length ? `Директор: ${escapeHtml(directors.join(", "))}` : "Директор не назначен"}</span>
        <span class="st-flag ${hasPoint(s) ? "ok" : ""}">${icon(hasPoint(s) ? "pin" : "warning", "ic ic-xs")}${hasPoint(s)
          ? "Место на карте задано" : "Место на карте не задано"}</span>
      </button>`;
  }).join("") + `
    <button type="button" class="school-tile add" onclick="openSchool()">${icon("plus")}<span>Добавить школу</span></button>`;
}

function staffRows(users, schoolId) {
  return users.map((user) => {
    const posts = postsOf(user, schoolId);
    const badges = checkinBadges(user);
    const name = escapeHtml(user.name);
    return `
      <div class="sc-row">
        ${staffAvatar(user)}
        <span class="sc-name"><b>${name}</b><span class="sc-login">${escapeHtml(user.login)}</span></span>
        <span class="tag-row sc-posts">${posts.map((p) =>
          `<span class="tag ${ADMIN_POSTS.includes(p) ? "tag-accent" : ""}" title="${POSTS[p]}">${POSTS_SHORT[p]}</span>`).join("")}${badges}</span>
        <span class="acts">
          <button class="icon-btn boxed" title="Должности в этой школе" aria-label="Должности: ${name}"
                  onclick="openSchoolStaff(${user.id})">${icon("pencil", "ic ic-sm")}</button>
          <button class="icon-btn boxed danger" title="Убрать из школы" aria-label="Убрать из школы: ${name}"
                  onclick="dropFromSchool(${user.id})">${icon("close", "ic ic-sm")}</button>
        </span>
      </div>`;
  }).join("");
}

function renderSchoolCard(school) {
  const staff = schoolStaff(school.id).sort((a, b) => a.name.localeCompare(b.name, "ru"));
  const lead = staff.filter((u) => postsOf(u, school.id).some((p) => ADMIN_POSTS.includes(p)));
  const teachers = staff.filter((u) => !lead.includes(u));
  const point = hasPoint(school);
  const rules = checkinRules
    ? `Приход засчитывается за <b>${checkinRules.before} мин</b> до начала урока, опоздание — позже
       <b>${checkinRules.grace} мин</b> после начала. Одна отметка — на уроки подряд.`
    : "Приход и опоздание считаются по урокам расписания.";
  $("schoolCard").innerHTML = `
    <div class="j-head sc-top">
      <div class="j-title">
        <button class="btn btn-ghost btn-md j-back" onclick="closeSchoolCard()" title="Все школы">
          ${icon("arrow-left", "ic ic-sm")}<span>Все школы</span></button>
        <div class="j-title-text">
          <h1 class="page-title" title="${escapeHtml(school.name)}">${escapeHtml(school.name)}</h1>
          <p class="page-sub">${countsLine(school.id)}</p>
        </div>
      </div>
      <button class="btn btn-ghost" onclick="openSchool(${school.id})">${icon("pencil", "ic ic-sm")}Переименовать</button>
    </div>
    <div class="sc-grid">
      <section class="card sc-staff" aria-labelledby="scStaffTitle">
        <div class="sc-head">
          <h2 class="sc-title" id="scStaffTitle">Сотрудники школы</h2>
          <div class="sc-acts">
            <button class="btn btn-ghost btn-sm" onclick="openSchoolStaff()">${icon("users", "ic ic-sm")}Добавить из сотрудников</button>
            <button class="btn btn-sm" onclick="openUser(null, { school: ${school.id}, post: 'teacher' })">${icon("plus", "ic ic-sm")}Новый сотрудник</button>
            <a class="btn btn-ghost btn-sm" href="${BASE}/cards/school/${school.id}/staff" target="_blank" rel="noopener"
               title="Код для бота, QR и вход в панель — как у учеников">${icon("printer", "ic ic-sm")}Карточки сотрудников</a>
          </div>
        </div>
        <h3 class="sc-group">Руководство <span>директор и завучи: оформление, призы школы, карточки, расписание</span></h3>
        ${lead.length ? staffRows(lead, school.id) : `<p class="sc-empty">Директор и завучи пока не назначены.</p>`}
        <h3 class="sc-group">Учителя <span>ведут журнал и выдают звёзды</span></h3>
        ${teachers.length ? staffRows(teachers, school.id) : `<p class="sc-empty">Учителей пока нет — добавьте из сотрудников или создайте нового.</p>`}
      </section>
      <div class="sc-side">
        <section class="card sc-block">
          <h2 class="sc-title">${icon("pin", "ic ic-sm")}Место на карте</h2>
          ${point ? `
            <p>${school.address ? `${escapeHtml(school.address)}<br>` : ""}<span class="mono">${escapeHtml(formatCoords(school.lat, school.lon))}</span>
              · радиус ${fmt(school.radius_m || 150)} м</p>
            <a class="btn btn-link btn-sm" href="${escapeHtml(mapLink(school.lat, school.lon))}" target="_blank" rel="noopener">Открыть на карте</a>`
            : `<p class="sc-warn">${icon("warning", "ic ic-xs")}Не задано: без точки отметки учителей уходят на проверку.</p>`}
          <button class="btn btn-ghost btn-sm" onclick="openSchoolLocation()">${point ? "Изменить" : "Задать место"}</button>
        </section>
        <section class="card sc-block">
          <h2 class="sc-title">${icon("clock", "ic ic-sm")}Время уроков</h2>
          <p>${escapeHtml(bellsSummary(school))}</p>
          <button class="btn btn-ghost btn-sm" onclick="openBells()">Изменить</button>
        </section>
        <section class="card sc-block">
          <h2 class="sc-title">${icon("palette", "ic ic-sm")}Оформление</h2>
          <p class="sc-theme"><span class="st-swatch" data-theme="${escapeHtml(school.theme || "notebook")}" aria-hidden="true"></span>${escapeHtml(THEME_NAMES[school.theme] || "")}</p>
          <button class="btn btn-ghost btn-sm" onclick="openTheme()">Изменить</button>
        </section>
        <section class="card sc-block">
          <h2 class="sc-title">${icon("check-circle", "ic ic-sm")}Правила отметок</h2>
          <p>${rules}</p>
          <p class="sc-note">Общие для всех школ.</p>
          <button class="btn btn-ghost btn-sm" onclick="openCheckinRules()">Изменить</button>
        </section>
        <button class="btn btn-danger-quiet btn-sm sc-delete" onclick="removeSchool()">${icon("trash", "ic ic-sm")}Удалить школу…</button>
      </div>
    </div>`;
}

/** Открыть карточку школы. Школа становится выбранной: окна «Оформление», «Время уроков»
 *  и «Место на карте» работают с выбранной школой. */
function openSchoolCard(id) {
  const go = () => {
    schoolView = id;
    showPage("schools");
    setRoute();
    loadCheckinRules();
  };
  if (id === activeSchool) return go();
  leaveDraftGuard(() => { useSchool(id); go(); });
}

function closeSchoolCard() {
  schoolView = null;
  renderSchoolsPage();
  setRoute();
  window.scrollTo(0, 0);
}

async function loadCheckinRules() {
  if (checkinRules) return;
  try {
    const { values } = await api("/api/checkin/settings");
    checkinRules = { before: values.checkin_window_before_min, grace: values.grace_minutes };
    if (currentPage() === "schools") renderSchoolsPage();
  } catch (_) {
    // без правил карточка покажет общую фразу
  }
}

function openCheckinRules() {
  checkinRules = null;          // вернутся — прочитаем заново
  switchPage("checkin");
  setCheckinTab("settings");
}

/* ── Сотрудник в школе: добавить из общего списка или поменять должности ── */

function openSchoolStaff(userId = null) {
  const school = schoolById(schoolView || activeSchool);
  if (!school) return;
  const user = userId ? (state.users || []).find((u) => u.id === userId) : null;
  schoolStaffPick = { school: school.id, user: user?.id ?? null, edit: Boolean(user),
                      posts: new Set(user ? postsOf(user, school.id) : ["teacher"]) };
  $("ssTitle").textContent = user ? user.name : "Добавить из сотрудников";
  $("ssSub").textContent = user
    ? `Должности в школе «${school.name}». Снимите все — сотрудник уйдёт из этой школы.`
    : `Сотрудник появится в школе «${school.name}» с выбранными должностями. Его логин и пароль не меняются.`;
  $("ssSearchField").hidden = Boolean(user);
  $("ssSearch").value = "";
  renderSchoolStaffPick();
  openModal("modalSchoolStaff");
  if (!user) setTimeout(() => $("ssSearch").focus(), 50);
}

function renderSchoolStaffPick() {
  const pick = schoolStaffPick;
  if (!pick) return;
  if (pick.edit) {
    $("ssList").hidden = true;
  } else {
    const query = $("ssSearch").value.trim().toLowerCase();
    const others = (state.users || []).filter((u) => u.role !== "superadmin" && !postsOf(u, pick.school).length)
      .filter((u) => !query || u.name.toLowerCase().includes(query) || u.login.includes(query))
      .sort((a, b) => a.name.localeCompare(b.name, "ru"));
    $("ssList").hidden = false;
    $("ssList").innerHTML = others.length ? others.map((u) => {
      const where = Object.keys(u.posts || {}).map((id) => schoolById(Number(id))?.name).filter(Boolean);
      return `
        <button type="button" class="ss-item" aria-pressed="${pick.user === u.id}" onclick="pickSchoolStaff(${u.id})">
          ${staffAvatar(u)}
          <span class="sc-name"><b>${escapeHtml(u.name)}</b>
            <span class="sc-login">${escapeHtml(u.login)} · ${where.length ? escapeHtml(where.join(", ")) : "пока без школы"}</span></span>
          ${pick.user === u.id ? icon("check-circle", "ic ic-sm") : ""}
        </button>`;
    }).join("") : `<p class="sc-empty">${query ? "Никого не нашли." : "Все сотрудники уже в этой школе."}
        Новый — кнопка «Новый сотрудник» в карточке школы.</p>`;
  }
  $("ssPosts").innerHTML = Object.keys(POSTS).map((post) => `
    <button type="button" aria-pressed="${pick.posts.has(post)}" title="${POSTS[post]}"
            onclick="toggleSchoolStaffPost('${post}')">${POSTS_SHORT[post]}</button>`).join("");
  $("ssSave").disabled = !pick.user;
}

function pickSchoolStaff(id) {
  schoolStaffPick.user = id;
  renderSchoolStaffPick();
}

function toggleSchoolStaffPost(post) {
  const posts = schoolStaffPick.posts;
  if (posts.has(post)) posts.delete(post); else posts.add(post);
  renderSchoolStaffPick();
}

async function putSchoolPosts(schoolId, userId, posts) {
  await api(`/api/schools/${schoolId}/staff/${userId}`, { method: "PUT", json: { posts } });
  await loadState();
}

async function saveSchoolStaff() {
  const pick = schoolStaffPick;
  if (!pick?.user) return toast("Выберите сотрудника", "error");
  const posts = Object.keys(POSTS).filter((p) => pick.posts.has(p));
  if (!pick.edit && !posts.length) return toast("Отметьте хотя бы одну должность", "error");
  const user = (state.users || []).find((u) => u.id === pick.user);
  const wasTeacher = postsOf(user, pick.school).includes("teacher");
  const run = async () => {
    try {
      await putSchoolPosts(pick.school, pick.user, posts);
      closeModal("modalSchoolStaff");
      toast(!posts.length ? `${user.name} — больше не в этой школе` : pick.edit ? "Должности сохранены"
        : `${user.name} — в школе`, "success");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  if (wasTeacher && !posts.includes("teacher")) {
    return confirmAction("Снять с должности учителя?",
      `${escapeHtml(user.name)} больше не будет вести журнал этой школы. Его уроки в расписании школы останутся без учителя.`,
      run, "Снять");
  }
  run();
}

function dropFromSchool(userId) {
  const user = (state.users || []).find((u) => u.id === userId);
  const school = schoolById(schoolView || activeSchool);
  if (!user || !school) return;
  const teaches = postsOf(user, school.id).includes("teacher");
  confirmAction(`Убрать ${user.name} из школы?`,
    `Школа «${escapeHtml(school.name)}» пропадёт у сотрудника сразу.${teaches ? " Его уроки в расписании этой школы останутся без учителя." : ""}
     Учётная запись остаётся — её можно добавить снова или в другую школу.`,
    async () => {
      await putSchoolPosts(school.id, user.id, []);
      toast(`${user.name} — больше не в этой школе`, "success");
    }, "Убрать");
}
