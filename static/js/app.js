/* Кабинет ученика: вход через Telegram, рейтинг, призы, аватар. */

const tg = window.Telegram?.WebApp;

// Подпуть, на котором развёрнута система ("" в корне, "/stars" на подпути).
// Значение подставляет сервер при отдаче страницы.
const BASE = window.APP_BASE || "";

/** SVG-иконка из набора, вшитого в страницу. Вместо эмодзи: они выглядят
 *  по-разному на Android, iOS и в Windows, и ломают единый вид интерфейса. */
function icon(name, cls = "ic") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

/** Путь к картинке с учётом подпути. Внешние ссылки (фото профиля Telegram)
 *  оставляем как есть. */
function assetUrl(path) {
  if (!path) return "";
  return /^https?:\/\//.test(path) ? path : BASE + path;
}

let token = null;
let data = null;
let telegramPhotoUrl = null;
let filter = "all";
let affordableBefore = new Set();
let isPreview = false;   // кабинет открыт учителем «глазами ученика»
let refreshTimer = null; // опрос свежих звёзд; останавливается при выходе

/* ── Мелкие помощники ─────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

const stars = (n) => `${n} ${plural(n, "звезда", "звезды", "звёзд")}`;

function toast(message, kind = "", iconName = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`.trim();
  if (iconName) el.innerHTML = icon(iconName);
  el.appendChild(document.createTextNode(message));
  $("toastHost").appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s, transform .3s";
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 300);
  }, 2600);
}

function haptic(type = "impact") {
  try {
    if (!tg?.HapticFeedback) return;
    if (type === "success") tg.HapticFeedback.notificationOccurred("success");
    else if (type === "error") tg.HapticFeedback.notificationOccurred("error");
    else tg.HapticFeedback.impactOccurred("light");
  } catch (_) { /* старые версии Telegram — не страшно */ }
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(BASE + path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Что-то пошло не так");
  return payload;
}

/* ── Тема Telegram ────────────────────────────────────────── */

// Внутри Telegram кабинет подстраивается под тему пользователя.
// В обычном браузере (например, у учителя) тему задаёт система, поэтому
// проверяем initData: без неё скрипт Telegram — просто заглушка.
const insideTelegram = Boolean(tg?.initData);

function applyTelegramTheme() {
  if (!insideTelegram) return;
  const mode = tg.colorScheme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", mode);
  try {
    tg.setHeaderColor(mode === "dark" ? "#161d31" : "#4f46e5");
    tg.setBackgroundColor(mode === "dark" ? "#0c1120" : "#eef2f9");
  } catch (_) { /* не поддерживается в старых версиях */ }
}

/* ── Вход ─────────────────────────────────────────────────── */

function showError(title, text) {
  $("screenLoading").hidden = true;
  $("screenError").hidden = false;
  $("errorTitle").textContent = title;
  $("errorText").textContent = text;
  $("errorEnterLink").href = `${BASE}/enter`;
}

async function authenticate() {
  const saved = sessionStorage.getItem("stars_token");

  if (tg?.initData) {
    const result = await api("/api/auth/twa", { method: "POST", json: { init_data: tg.initData } });
    token = result.token;
    telegramPhotoUrl = result.telegram_photo_url || null;
    sessionStorage.setItem("stars_token", token);
    return;
  }

  // Эти режимы проверяются раньше сохранённой сессии, чтобы можно было
  // переключаться между учениками без закрытия вкладки.
  const params = new URLSearchParams(location.search);

  // Предпросмотр из панели учителя: /app?preview=101 (нужна cookie учителя)
  const previewId = params.get("preview");
  if (previewId) {
    const result = await api(`/api/auth/preview?student_id=${encodeURIComponent(previewId)}`,
                             { method: "POST" });
    token = result.token;
    isPreview = true;
    return;
  }

  // Отладочный вход (работает только при DEV_LOGIN=1): /app?dev=101
  const devId = params.get("dev");
  if (devId) {
    const result = await api(`/api/auth/dev?student_id=${encodeURIComponent(devId)}`, { method: "POST" });
    token = result.token;
    sessionStorage.setItem("stars_token", token);
    return;
  }

  if (saved) {
    token = saved;
    return;
  }

  throw new Error(
    "Кабинет открывается из Telegram: отсканируй QR-код со своей карточки, " +
    "и в боте появится кнопка «Зайти в свой кабинет». " +
    "Нет Telegram — войди по логину и паролю с карточки."
  );
}

/* ── Отрисовка ────────────────────────────────────────────── */

function renderHero() {
  const student = data.student;
  const board = data.leaderboard;

  $("heroAvatarImg").src = assetUrl(student.avatar_url);
  $("heroAvatarImg").alt = student.name;
  $("heroName").textContent = student.name;
  $("heroClass").querySelector("span").textContent = student.class_name;
  $("heroRank").querySelector("span").textContent = board.my_place
    ? `${board.my_place}-е место из ${board.class_size}`
    : "—";

  const schoolChip = $("heroSchool");
  if (student.school_name) {
    schoolChip.hidden = false;
    schoolChip.querySelector("span").textContent = student.school_name;
  } else {
    schoolChip.hidden = true;
  }

  $("heroStars").textContent = student.total_stars;
  $("heroStarsWord").textContent = plural(student.total_stars, "звезда", "звезды", "звёзд");

  const next = data.next_prize;
  const goal = $("nextGoal");
  if (next) {
    goal.hidden = false;
    $("goalName").textContent = `«${next.name}»`;
    $("goalLeft").innerHTML = `осталось ${next.left}&nbsp;${icon("star")}`;
    requestAnimationFrame(() => { $("goalBar").style.width = `${next.progress}%`; });
  } else {
    goal.hidden = true;
  }
}

function prizeCard(prize) {
  const blank = `<div class="no-photo">${icon("gift", "ic ic-xl")}</div>`;
  const photo = prize.image
    ? `<img src="${escapeHtml(assetUrl(prize.image))}" alt="${escapeHtml(prize.name)}" loading="lazy"
            onerror="this.outerHTML = this.dataset.blank">`
    : blank;

  const status = prize.affordable
    ? `${icon("check")} Можно забрать — подойди к учителю`
    : `${icon("star")} Осталось ${prize.left}`;

  return `
    <article class="prize ${prize.affordable ? "ready" : ""}">
      <div class="prize-photo">
        ${photo.replace("<img ", `<img data-blank="${escapeHtml(blank)}" `)}
        <span class="prize-cost">${icon("star")} ${prize.cost}</span>
        ${prize.affordable ? '<span class="prize-ready-flag">ХВАТАЕТ</span>' : ""}
      </div>
      <div class="prize-body">
        <div class="prize-name">${escapeHtml(prize.name)}</div>
        <div class="prize-desc">${escapeHtml(prize.desc)}</div>
        <div class="prize-bar"><i style="width:${prize.progress}%"></i></div>
        <div class="prize-status">${status}</div>
      </div>
    </article>`;
}

function renderPrizes() {
  const grid = $("prizesGrid");
  const list = filter === "ready" ? data.prizes.filter((p) => p.affordable) : data.prizes;

  if (!list.length) {
    grid.innerHTML = `<div class="empty-note" style="grid-column:1/-1;">
      Пока нет призов, на которые хватает звёзд.<br>Зарабатывай их на уроках — всё получится.
    </div>`;
    return;
  }
  grid.innerHTML = list.map(prizeCard).join("");
}

function podiumBlock(entry, place) {
  if (!entry) return `<div class="podium-place"></div>`;
  const cls = ["first", "second", "third"][place - 1];
  return `
    <div class="podium-place ${cls}">
      <div class="podium-avatar"><img src="${escapeHtml(assetUrl(entry.avatar_url))}" alt=""></div>
      <div class="podium-name">${escapeHtml(entry.is_me ? "Ты" : entry.name.split(" ")[0])}</div>
      <div class="podium-stand">
        <div class="podium-stars">${entry.total_stars}</div>
        <div class="podium-rank">${place}-е место</div>
      </div>
    </div>`;
}

function rankRow(entry) {
  return `
    <div class="rank-row ${entry.is_me ? "me" : ""}">
      <div class="rank-place">${entry.place}</div>
      <div class="rank-avatar"><img src="${escapeHtml(assetUrl(entry.avatar_url))}" alt=""></div>
      <div class="rank-name">${escapeHtml(entry.name)}${entry.is_me ? " — это ты" : ""}</div>
      <div class="rank-stars">${entry.total_stars} ${icon("star")}</div>
    </div>`;
}

function renderRating() {
  const board = data.leaderboard;
  const top = board.top;
  $("ratingCount").textContent = board.class_size ? `· в классе ${board.class_size}` : "";

  if (!top.length) {
    $("podium").innerHTML = "";
    $("rankList").innerHTML = `<div class="empty-note">В классе пока никого нет.</div>`;
    $("goalNote").hidden = true;
    return;
  }

  // Пьедестал: 2-е место слева, 1-е по центру, 3-е справа
  $("podium").innerHTML =
    podiumBlock(top[1], 2) + podiumBlock(top[0], 1) + podiumBlock(top[2], 3);

  let html = top.slice(3).map(rankRow).join("");
  if (board.me) {
    html += `<div class="rank-divider">•••</div>` + rankRow(board.me);
  }
  $("rankList").innerHTML = html;

  const note = $("goalNote");
  if (board.gap_to_next && board.my_place) {
    // Говорим про место, а не про фамилию: «обгонишь Назаров» звучало бы безграмотно
    note.hidden = false;
    note.innerHTML = `${icon("target")}<span>Ещё <b>${stars(board.gap_to_next)}</b> —
      и ты поднимешься на <b>${board.my_place - 1}-е место</b> в классе!</span>`;
  } else if (board.my_place === 1) {
    note.hidden = false;
    note.innerHTML = `${icon("crown")}<span>Ты на первом месте в классе. Держи планку!</span>`;
  } else {
    note.hidden = true;
  }
}

function renderHistory() {
  const timeline = $("timeline");
  const history = data.history || [];
  if (!history.length) {
    timeline.innerHTML = `<div class="empty-note">Записей пока нет — всё впереди!</div>`;
    return;
  }
  timeline.innerHTML = history.map((item) => {
    const plus = item.delta >= 0;
    return `
      <div class="event ${plus ? "plus" : "minus"}">
        <div class="event-icon">${icon(plus ? "star" : "gift")}</div>
        <div class="event-body">
          <div class="event-reason">${escapeHtml(item.reason)}</div>
          <div class="event-date">${escapeHtml(item.date)}</div>
        </div>
        <div class="event-delta">${plus ? "+" : ""}${item.delta}</div>
      </div>`;
  }).join("");
}

function renderAvatars() {
  const current = data.student.avatar || {};
  const html = data.avatar_presets.map((preset) => `
    <button class="avatar-option ${current.type === "preset" && current.value === preset ? "selected" : ""}"
            onclick="pickPreset('${preset}')" aria-label="${preset}">
      <img src="${BASE}/static/img/avatars/${preset}.svg" alt="">
    </button>`).join("");

  $("avatarGrid").innerHTML = html;
  $("avatarGridSheet").innerHTML = html;
  $("tgPhotoBtn").hidden = !telegramPhotoUrl;

  $("infoSchool").textContent = data.student.school_name || "—";
  $("infoClass").textContent = data.student.class_name;
  $("infoRank").textContent = data.leaderboard.my_place
    ? `${data.leaderboard.my_place} из ${data.leaderboard.class_size}`
    : "—";
  $("infoStars").textContent = data.student.total_stars;
  $("infoLogin").textContent = data.student.login || "—";

  // Учителю, который смотрит кабинет глазами ребёнка, выход не нужен:
  // он бы отвязал Telegram настоящего ученика. Сервер такой запрос тоже отклонит.
  $("logoutBlock").hidden = isPreview;
}

function celebrateNewPrizes() {
  const now = new Set(data.prizes.filter((p) => p.affordable).map((p) => p.id));
  const fresh = [...now].filter((id) => !affordableBefore.has(id));
  if (affordableBefore.size && fresh.length) {
    haptic("success");
    sparkle();
    toast("Открылся новый приз — загляни в витрину", "success", "gift");
  }
  affordableBefore = now;
}

function sparkle() {
  const symbols = ["star", "sparkle", "star"];
  for (let i = 0; i < 10; i++) {
    const node = document.createElement("div");
    node.className = "sparkle";
    node.innerHTML = icon(symbols[i % symbols.length]);
    node.style.left = `${10 + Math.random() * 80}%`;
    node.style.top = `${30 + Math.random() * 35}%`;
    node.style.animationDelay = `${Math.random() * 0.4}s`;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 1700);
  }
}

function renderAll() {
  renderHero();
  renderPrizes();
  renderRating();
  renderHistory();
  renderAvatars();
  celebrateNewPrizes();
}

/* ── Действия ─────────────────────────────────────────────── */

function switchView(name) {
  haptic();
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${name}`);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === name);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setFilter(mode) {
  filter = mode;
  $("filterAll").classList.toggle("active", mode === "all");
  $("filterReady").classList.toggle("active", mode === "ready");
  haptic();
  renderPrizes();
}

function openAvatarSheet() {
  haptic();
  $("avatarSheet").classList.add("open");
}

function closeAvatarSheet() {
  $("avatarSheet").classList.remove("open");
}

async function refresh() {
  if (!token) return;   // после выхода обновлять нечего
  data = await api("/api/me");
  renderAll();
}

async function pickPreset(preset) {
  try {
    await api("/api/me/avatar", { method: "POST", json: { preset } });
    haptic("success");
    await refresh();
    toast("Аватар обновлён", "success");
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
  }
}

async function useTelegramPhoto() {
  try {
    await api("/api/me/avatar", {
      method: "POST",
      json: { use_telegram_photo: true, telegram_photo_url: telegramPhotoUrl },
    });
    haptic("success");
    await refresh();
    toast("Поставили фото из Telegram", "success");
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
  }
}

async function uploadPhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;

  if (file.size > 5 * 1024 * 1024) {
    toast("Файл больше 5 МБ — выбери фото поменьше", "error");
    return;
  }

  const form = new FormData();
  form.append("file", file);
  toast("Загружаем фото…");

  try {
    await api("/api/me/photo", { method: "POST", body: form });
    haptic("success");
    await refresh();
    closeAvatarSheet();
    toast("Готово! Теперь это твой аватар", "success");
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
  }
}

/* ── Пароль для входа с сайта ─────────────────────────────── */

function togglePasswordForm() {
  const box = $("passwordForm");
  box.hidden = !box.hidden;
  if (!box.hidden) $("passCurrent").focus();
}

async function savePassword() {
  const current = $("passCurrent").value;
  const next = $("passNew").value.trim();

  if (next.length < 5) {
    toast("Новый пароль — минимум 5 символов", "error");
    return;
  }

  try {
    // Смена пароля завершает все сессии, а этому устройству сервер выдаёт новую
    const result = await api("/api/me/password", { method: "POST", json: { current, new: next } });
    if (result.token) {
      token = result.token;
      try { sessionStorage.setItem("stars_token", token); } catch (e) { /* приватный режим */ }
    }
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
    return;
  }

  $("passCurrent").value = "";
  $("passNew").value = "";
  $("passwordForm").hidden = true;
  haptic("success");
  toast("Пароль изменён — теперь его знаешь только ты", "success", "check");
}

/* ── Выход из аккаунта ────────────────────────────────────── */

function confirmLogout() {
  const text = "Выйти из аккаунта?\n\nТвой Telegram открепится от карточки. " +
    "Звёзды и аватар останутся — вернуться можно по своему QR-коду.";

  return new Promise((resolve) => {
    // Внутри Telegram показываем родное окно: привычнее и не блокируется WebView
    if (insideTelegram && typeof tg.showConfirm === "function") {
      tg.showConfirm(text, (ok) => resolve(Boolean(ok)));
      return;
    }
    resolve(window.confirm(text));
  });
}

async function logoutAccount() {
  if (!(await confirmLogout())) return;

  try {
    await api("/api/me/unbind", { method: "POST" });
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
    return;
  }

  haptic("success");

  // Опрос звёзд больше не нужен: токен недействителен, запросы вернут 401
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }

  sessionStorage.removeItem("stars_token");
  token = null;
  data = null;

  closeAvatarSheet();
  $("app").hidden = true;
  $("screenBye").hidden = false;
}

function lockTelegramWindow() {
  if (!insideTelegram) return;

  // Главная беда на iPhone: свайп вниз сворачивает и закрывает кабинет
  // прямо посреди работы — ребёнок теряет страницу, пролистывая рейтинг.
  // disableVerticalSwipes появился в Bot API 7.7; в старых клиентах метода
  // просто нет, поэтому вызов защищён.
  try { tg.disableVerticalSwipes?.(); } catch (e) { /* старый клиент */ }

  // Если клиент всё-таки попытается закрыть окно — Telegram спросит подтверждение.
  try { tg.enableClosingConfirmation?.(); } catch (e) { /* старый клиент */ }
}

function closeCabinet() {
  if (insideTelegram) {
    // Своё закрытие — намеренное, лишний вопрос «точно выйти?» здесь только мешает
    try { tg.disableClosingConfirmation?.(); } catch (e) { /* старый клиент */ }
    tg.close?.();
    return;
  }
  location.reload();
}

/* ── Старт ────────────────────────────────────────────────── */

(async function start() {
  if (tg) {
    tg.ready();
    tg.expand();
    applyTelegramTheme();
    tg.onEvent?.("themeChanged", applyTelegramTheme);
    lockTelegramWindow();
  }

  try {
    await authenticate();
    data = await api("/api/me");
  } catch (error) {
    sessionStorage.removeItem("stars_token");
    showError("Не получилось войти", error.message);
    return;
  }

  $("screenLoading").hidden = true;
  $("app").hidden = false;
  $("heroClose").hidden = !insideTelegram;   // вне Telegram закрывать нечего
  renderAll();

  // Вкладку можно открыть сразу по адресу: /app?view=rating
  const wanted = new URLSearchParams(location.search).get("view");
  if (["prizes", "rating", "history", "profile"].includes(wanted)) {
    switchView(wanted);
  }

  if (isPreview) {
    const ribbon = document.createElement("div");
    ribbon.className = "preview-ribbon";
    ribbon.innerHTML = icon("eye") +
      `<span>Предпросмотр: кабинет ученика «${escapeHtml(data.student.name)}»</span>`;
    document.body.appendChild(ribbon);
    document.body.classList.add("has-ribbon");
  }

  // Пока кабинет открыт — подтягиваем свежие звёзды (учитель мог начислить прямо сейчас)
  refreshTimer = setInterval(() => { refresh().catch(() => {}); }, 45000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh().catch(() => {});
  });
})();
