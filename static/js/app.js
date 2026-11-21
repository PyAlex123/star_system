/* Кабинет ученика: вход через Telegram, призы, рейтинг, история, аватар.
   Вид — по макету docs/design/«Звёзды - Ученик» в пяти темах. Тема — школы
   ученика: её рисует сервер, а после ответа /api/me кабинет сверяет её. */

const tg = window.Telegram?.WebApp;
const insideTelegram = Boolean(tg?.initData);

let token = null;
let data = null;
let telegramPhotoUrl = null;
let filter = "all";
let view = "prizes";
let affordableBefore = null;   // какие призы были доступны при прошлом обновлении
let balanceBefore = null;
let isPreview = false;         // кабинет открыт учителем «глазами ученика»
let refreshTimer = null;
let draftAvatar = null;        // выбор в шторке — применяется по «Готово»

const STAR_POINTS = "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2";
const AVATAR_NAMES = {
  fox: "Лиса", cat: "Кошка", panda: "Панда", bear: "Медведь", tiger: "Тигр", wolf: "Волк", koala: "Коала",
  owl: "Сова", penguin: "Пингвин", frog: "Лягушка", shark: "Акула", dino: "Динозаврик", dragon: "Дракончик",
  unicorn: "Единорог", ghost: "Привидение", slime: "Клякса", strawberry: "Клубничка", bolt: "Молния",
  robot: "Робот", cyborg: "Киборг", astronaut: "Космонавт", alien: "Пришелец", rocket: "Ракета", planet: "Планета",
};
const SPACE_FIRST = ["astronaut", "rocket", "planet", "alien", "robot"];

const toast = makeToast({ duration: 2600 });

/* ── Помощники ────────────────────────────────────────────── */

const starWord = (n) => plural(n, "звезда", "звезды", "звёзд");
const T = () => currentTheme();
const starIc = (cls = "ic") => icon("star", `${cls} ic-star`);

/** Запрос от имени ученика: сессия передаётся токеном в заголовке. */
function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return requestJson(path, { ...options, headers });
}

function haptic(type = "impact") {
  try {
    if (!tg?.HapticFeedback) return;
    if (type === "success") tg.HapticFeedback.notificationOccurred("success");
    else if (type === "error") tg.HapticFeedback.notificationOccurred("error");
    else tg.HapticFeedback.impactOccurred("light");
  } catch (_) { /* старые версии Telegram — не страшно */ }
}

function img(src, cls = "", alt = "") {
  return src ? `<img class="${cls}" src="${escapeHtml(assetUrl(src))}" alt="${escapeHtml(alt)}" loading="lazy">`
             : `<span class="${cls} no-img">${icon("gift", "ic ic-lg")}</span>`;
}

function firstName(full) {
  const parts = String(full || "").trim().split(/\s+/);
  return parts[1] || parts[0] || "";
}
function surname(full) { return String(full || "").trim().split(/\s+/)[0] || ""; }

/* ── Тема ─────────────────────────────────────────────────── */

/** В Telegram «Авто» у «Ясно и быстро» — по теме самого Telegram, а не системы. */
function syncMode() {
  if (!insideTelegram) return;
  const saved = getCookie("zv_mode") || "auto";
  if (saved === "auto") document.documentElement.dataset.mode = tg.colorScheme === "dark" ? "dark" : "light";
}

function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

/** Шапка Telegram — цвета шапки кабинета, фон — цвет страницы. */
function syncTelegramColors() {
  if (!insideTelegram) return;
  try {
    tg.setHeaderColor(cssToken("--hero-bg"));
    tg.setBackgroundColor(cssToken("--bg"));
    tg.setBottomBarColor?.(cssToken("--surface"));
  } catch (_) { /* старый клиент */ }
}

function pickMode(mode) {
  applyMode(mode);
  syncMode();
  renderModeSeg();
}

function renderModeSeg() {
  const mode = getCookie("zv_mode") || "auto";
  document.querySelectorAll("#modeSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
}

/* ── Вход ─────────────────────────────────────────────────── */

function showScreen(id) {
  ["screenLoading", "screenNet", "screenError", "screenBye", "app"].forEach((s) => { $(s).hidden = s !== id; });
}

function showError(title, text) {
  showScreen("screenError");
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
    const result = await api(`/api/auth/preview?student_id=${encodeURIComponent(previewId)}`, { method: "POST" });
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

  const error = new Error(
    "Кабинет открывается из Telegram: отсканируй QR-код со своей карточки, " +
    "и в боте появится кнопка «Зайти в свой кабинет». " +
    "Нет Telegram — войди по логину и паролю с карточки.");
  error.status = 401;
  throw error;
}

/* ── Призы и цель ─────────────────────────────────────────── */

/** Сначала то, на что хватает (дорогие первыми), потом остальное — от дешёвых. */
function orderedPrizes() {
  const list = data.prizes.slice();
  const ok = list.filter((p) => p.affordable).sort((a, b) => b.cost - a.cost);
  const no = list.filter((p) => !p.affordable).sort((a, b) => a.cost - b.cost);
  return { ok, no, all: [...ok, ...no] };
}

/** Цель в «Звёздном квесте» — приз с булавкой; иначе ближайший недоступный. */
function goalId() {
  try { return parseInt(localStorage.getItem("zv_goal")) || null; } catch (_) { return null; }
}
function nextPrize() {
  const { no } = orderedPrizes();
  const goal = no.find((p) => p.id === goalId());
  return { prize: goal || no[0] || null, isGoal: Boolean(goal) };
}

function togglePin(id, event) {
  event?.stopPropagation();
  try {
    if (goalId() === id) localStorage.removeItem("zv_goal");
    else localStorage.setItem("zv_goal", String(id));
  } catch (_) { /* приватный режим */ }
  haptic();
  renderHero();
  renderPrizes();
}

function pinHelp() {
  if (goalId()) { togglePin(goalId()); return; }
  setFilter("all");
  $("prizesHost").scrollIntoView({ behavior: "smooth", block: "start" });
}

function categoryColor(cost) { return cost >= 70 ? "var(--cat-l)" : cost >= 50 ? "var(--cat-m)" : "var(--cat-s)"; }

/* Созвездие: 10 звёзд-точек в форме по цене приза, горят по прогрессу */
const SHAPES = {
  dipper: { p: [[4,12],[15,16],[26,22],[37,27],[48,30],[52,48],[74,52],[80,34],[64,28],[94,18]], e: [[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,7],[7,8],[8,4],[7,9]] },
  arrow: { p: [[4,50],[16,44],[28,38],[40,33],[52,28],[64,23],[76,18],[92,10],[78,5],[88,26]], e: [[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,7],[7,8],[7,9]] },
  crown: { p: [[6,52],[10,22],[24,38],[34,12],[46,34],[56,6],[66,34],[78,12],[90,24],[94,52]], e: [[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,7],[7,8],[8,9],[9,0]] },
};
function constellation(price, frac, { small = false, draw = false, glowLast = false, h = 34 } = {}) {
  const S = SHAPES[price >= 70 ? "crown" : price >= 50 ? "dipper" : "arrow"];
  const lit = draw ? 10 : Math.max(0, Math.min(10, Math.round(frac * 10)));
  const lines = S.e.map(([a, b], i) => {
    const on = a < lit && b < lit;
    const dash = on ? (draw ? ' stroke-dasharray="1" pathLength="1"' : "") : ` stroke-dasharray="${small ? "2.5 2.5" : "1.6 1.6"}"`;
    const anim = draw && on ? ` style="stroke-dashoffset:1;animation:drawLine 600ms cubic-bezier(.2,.8,.2,1) ${250 + i * 55}ms forwards"` : "";
    return `<line x1="${S.p[a][0]}" y1="${S.p[a][1]}" x2="${S.p[b][0]}" y2="${S.p[b][1]}" stroke="${on ? "var(--star)" : "var(--line-strong)"}"
      stroke-width="${on ? (small ? 1.1 : 0.7) : (small ? 0.9 : 0.5)}" stroke-opacity="${on ? 0.85 : 1}" stroke-linecap="round"${dash}${anim}/>`;
  }).join("");
  const pts = S.p.map(([x, y], i) => {
    const on = i < lit;
    const last = glowLast && i === lit - 1;
    const style = [
      "transform-box:fill-box;transform-origin:center",
      last ? "filter:drop-shadow(0 0 2.5px var(--star))" : "",
      draw ? `animation:starOn 280ms cubic-bezier(.2,.8,.2,1) ${i * 45}ms both` : "",
    ].filter(Boolean).join(";");
    return `<circle cx="${x}" cy="${y}" r="${on ? (small ? 3 : 2) : (small ? 2.2 : 1.5)}" fill="${on ? "var(--star)" : "var(--bg)"}"
      stroke="${on ? "none" : "var(--muted)"}" stroke-width="${small ? 0.9 : 0.5}" stroke-opacity="0.7" style="${style}"/>`;
  }).join("");
  return `<svg class="constel" viewBox="-2 0 104 60" width="100%" height="${h}" role="img"
    aria-label="Созвездие: горит ${lit} из 10 звёзд">${lines}${pts}</svg>`;
}

/* ── Шапка (S1) ───────────────────────────────────────────── */

function placeText() {
  const b = data.leaderboard;
  return data.student.total_stars > 0 && b.my_place ? `${b.my_place}-е место из ${b.class_size}` : "";
}

function goalBlock() {
  const { prize: nx, isGoal } = nextPrize();
  if (!nx) {
    return `<div class="all-ok"><span class="all-ok-mark">${icon("check")}</span>Хватает на любой приз из витрины</div>`;
  }
  const bal = data.student.total_stars;
  const frac = Math.min(1, bal / nx.cost);
  const pct = Math.round(frac * 100);
  const name = escapeHtml(quoted(nx.name));
  const pic = nx.image ? escapeHtml(assetUrl(nx.image)) : "";
  const rem = nx.left;
  const theme = T();

  if (theme === "quest") {
    const checks = [0, 1, 2, 3].map((k) => {
      const on = frac >= k / 4;
      return `<span class="q-check ${on ? "on" : ""}" style="left:calc(12px + (100% - 76px) * ${k / 4})"></span>`;
    }).join("");
    return `<div class="goal goal-q">
      <div class="goal-q-head">
        <span><span class="hm">${isGoal ? "Цель:" : "До приза"}</span> <b>${name}</b> — ещё <b class="rem">${rem}&nbsp;★</b></span>
        <button class="pin-btn ${isGoal ? "on" : ""}" onclick="pinHelp()"
                aria-label="${isGoal ? "Снять цель — вернуть ближайший приз" : "Сделать целью другой приз — выбери булавкой в витрине"}"
                title="${isGoal ? "Снять цель" : "Сделать целью другой приз"}">${icon("pin")}</button>
      </div>
      <div class="q-path" role="progressbar" aria-label="Квест до приза" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
        <span class="q-dash"></span><span class="q-done" style="width:calc((100% - 76px) * ${frac.toFixed(3)})"></span>
        ${checks}
        <span class="q-chest">${pic ? `<img src="${pic}" alt="">` : icon("gift", "ic ic-lg")}</span>
      </div>
    </div>`;
  }
  if (theme === "sticker") {
    const filled = Math.round(frac * 10);
    const cells = Array.from({ length: 10 }, (_, k) => `<span class="${k < filled ? "on" : ""}"></span>`).join("");
    return `<div class="goal goal-k">
      <div class="goal-k-row">
        <div class="k-cells" role="progressbar" aria-label="Прогресс к призу" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">${cells}</div>
        <span class="k-thumb">${pic ? `<img src="${pic}" alt="">` : ""}</span>
      </div>
      <div class="goal-k-text"><span>До приза <b>${name}</b></span>
        <span class="hand">${rem <= 15 ? `ещё ${rem} — почти!` : `ещё ${rem} ★`}</span></div>
    </div>`;
  }
  if (theme === "sky") {
    return `<div class="goal goal-s">
      <div class="goal-s-row"><div class="grow">${constellation(nx.cost, bal / nx.cost, { h: 78, glowLast: true })}</div>
        ${pic ? `<img class="s-thumb" src="${pic}" alt="">` : ""}</div>
      <div class="goal-s-text">${isGoal ? "Цель — созвездие" : "Созвездие"} <b>${name}</b> — зажги ещё <b class="rem">${rem}&nbsp;★</b></div>
    </div>`;
  }
  // «Тетрадь отличника» и «Ясно и быстро» — полоса
  return `<div class="goal goal-nb">
    <div class="goal-nb-head">
      <span class="hm">До приза <b>${name}</b></span>
      <span class="left">осталось <b class="num">${rem}</b>${starIc("ic ic-sm")}</span>
    </div>
    <div class="goal-nb-row">
      <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
      ${pic ? `<img class="nb-thumb" src="${pic}" alt="">` : ""}
    </div>
  </div>`;
}

function renderHero() {
  const st = data.student;
  const bal = st.total_stars;
  const place = placeText();
  const twinkle = T() === "sky"
    ? `<span class="twinkle" aria-hidden="true"><i style="left:18%;top:22%"></i><i style="left:72%;top:14%;animation-delay:1.3s"></i><i style="left:88%;top:58%;animation-delay:2.6s"></i></span>` : "";
  $("hero").innerHTML = `
    ${twinkle}
    <span class="q-water only-q" aria-hidden="true"><svg viewBox="0 0 24 24"><polygon points="${STAR_POINTS}"/></svg></span>
    <div class="hero-who">
      <button class="hero-av" onclick="openAvatarSheet()" aria-label="Сменить аватар">
        <img src="${escapeHtml(assetUrl(st.avatar_url))}" alt="">
        <span class="hero-pen">${icon("pencil", "ic ic-xs")}</span>
      </button>
      <div class="hero-id">
        <div class="hero-name">${escapeHtml(st.name)}</div>
        <div class="hero-chips">
          ${st.school_name ? `<span class="hchip">${icon("school", "ic ic-xs")}${escapeHtml(st.school_name)}</span>` : ""}
          <span class="hchip">${icon("users", "ic ic-xs")}${escapeHtml(st.class_name)}</span>
        </div>
      </div>
    </div>
    <div class="hero-bal">
      <div class="col-gap2">
        <span class="hero-caps">Накоплено</span>
        <span class="counter">${starIc("ic counter-star")}<span class="bal-num">${fmt(bal)}</span><span class="bal-word">${starWord(bal)}</span></span>
      </div>
      ${place ? `<button class="place-btn" onclick="switchView('rating')" aria-label="Открыть рейтинг">${icon("medal", "ic ic-sm")}${place}</button>` : ""}
    </div>
    ${goalBlock()}`;

  $("miniAvatar").src = assetUrl(st.avatar_url);
  $("miniName").textContent = st.name;
  $("miniPill").setAttribute("aria-label", `Баланс: ${bal} ${starWord(bal)}`);
  $("miniPill").innerHTML = `${icon("star", "ic ic-sm")}<span class="num">${fmt(bal)}</span>`;
}

/* ── Витрина (S2) ─────────────────────────────────────────── */

function setFilter(mode) {
  filter = mode;
  $("filterAll").setAttribute("aria-selected", String(mode === "all"));
  $("filterReady").setAttribute("aria-selected", String(mode === "ready"));
  haptic();
  renderPrizes();
}

function prizeView() {
  try { return localStorage.getItem("zv_prize_view") === "list" && T() === "clear" ? "list" : "grid"; } catch (_) { return "grid"; }
}
function toggleView() {
  try { localStorage.setItem("zv_prize_view", prizeView() === "list" ? "grid" : "list"); } catch (_) {}
  renderPrizes();
}

function cardHtml(p, index) {
  const prize = p;
  const bal = data.student.total_stars;
  const theme = T();
  const pct = Math.min(100, Math.round(bal / p.cost * 100));
  const isGoal = goalId() === p.id;
  const aria = `${p.name}, ${p.cost} ${starWord(p.cost)}${p.affordable ? ", хватает" : `, ещё ${p.left} ${starWord(p.left)}`}`;
  const tilt = theme === "sticker" ? (index % 2 ? "1.2deg" : "-1.2deg") : "0deg";
  const pin = !p.affordable && theme === "quest"
    ? `<span class="pin ${isGoal ? "on" : ""}" role="button" tabindex="0" onclick="togglePin(${p.id}, event)"
         aria-label="${isGoal ? "Цель выбрана — нажми, чтобы снять" : "Сделать целью"}" title="${isGoal ? "Цель выбрана" : "Сделать целью"}">${icon("pin", "ic ic-sm")}</span>` : "";
  const status = p.affordable
    ? `<span class="can-take">${icon("check", "ic ic-sm")}<span>Можно забрать — подойди к учителю</span></span>`
    : `<span class="not-yet">
        ${theme === "sky" ? `<span class="mini-constel">${constellation(p.cost, bal / p.cost, { small: true, h: 34 })}</span>`
                          : `<span class="pbar"><i style="width:${pct}%"></i></span>`}
        <span class="still">ещё ${p.left}${starIc("ic ic-xs")}</span>
      </span>`;
  return `
    <button class="pcard ${p.affordable ? "ok" : ""} ${isGoal ? "goal" : ""}" onclick="${p.affordable ? `openCelebrate(${p.id})` : ""}" aria-label="${escapeHtml(aria)}">
      ${theme === "sticker" ? `<span class="tape" style="transform:rotate(${index % 2 ? "3deg" : "-4deg"})" aria-hidden="true"></span>` : ""}
      ${theme === "quest" ? `<span class="cat-strip" style="background:${categoryColor(p.cost)}" aria-hidden="true"></span>` : ""}
      <span class="pphoto" style="transform:rotate(${tilt})">
        ${pin}
        ${prize.image ? `<img src="${escapeHtml(assetUrl(prize.image))}" alt="" loading="lazy" onerror="this.remove()">` : ""}
        <span class="pphoto-blank">${icon("gift", "ic ic-xl")}</span>
        <span class="price not-clear-flex">${starIc("ic ic-xs")}<b>${fmt(p.cost)}</b></span>
        ${p.affordable ? `<span class="ribbon not-clear-flex">${icon("check", "ic ic-sm")}<span>Хватает</span></span>` : ""}
      </span>
      <span class="clear-price only-clear-flex"><span class="price-plain">${starIc("ic ic-sm")}<b>${fmt(p.cost)}</b></span>
        ${p.affordable ? `<span class="ok-chip">${icon("check", "ic ic-xs")}Хватает</span>` : ""}</span>
      <span class="pbody">
        <span class="pname">${escapeHtml(p.name)}</span>
        <span class="pdesc">${escapeHtml(p.desc || "")}</span>
        <span class="pfoot">${status}</span>
      </span>
    </button>`;
}

function listRowHtml(p) {
  const bal = data.student.total_stars;
  const pct = Math.min(100, Math.round(bal / p.cost * 100));
  return `
    <button class="prow" onclick="${p.affordable ? `openCelebrate(${p.id})` : ""}"
            aria-label="${escapeHtml(`${p.name}, ${p.cost} ${starWord(p.cost)}`)}">
      ${img(p.image, "prow-img")}
      <span class="prow-mid">
        <span class="prow-name">${escapeHtml(p.name)}</span>
        ${p.affordable ? `<span class="ok-chip">${icon("check", "ic ic-xs")}Можно забрать</span>`
          : `<span class="prow-bar"><span class="pbar"><i style="width:${pct}%"></i></span><span class="still">ещё ${p.left}&nbsp;★</span></span>`}
      </span>
      <span class="price-plain">${starIc("ic ic-sm")}<b>${fmt(p.cost)}</b></span>
    </button>`;
}

function renderPrizes() {
  const { ok, all } = orderedPrizes();
  const shown = filter === "ready" ? ok : all;
  $("filterReady").textContent = `Уже доступны (${ok.length})`;
  const list = prizeView() === "list";
  $("viewToggle").setAttribute("aria-label", list ? "Показать плиткой" : "Показать списком");
  $("viewToggle").title = list ? "Показать плиткой" : "Показать списком";
  $("viewToggle").querySelector("use").setAttribute("href", list ? "#i-grid" : "#i-list");

  if (!data.prizes.length) {
    $("prizesHost").innerHTML = `<div class="empty-card ruled-bg">${icon("gift", "ic ic-xl")}<b>Призов пока нет</b>Учитель скоро добавит их в витрину.</div>`;
    return;
  }
  if (!shown.length) {
    const { prize: nx } = nextPrize();
    $("prizesHost").innerHTML = `
      <div class="empty-card ruled-bg">
        <span class="empty-ring">${icon("star-line", "ic ic-lg")}</span>
        <b>Пока не хватает ни на один приз</b>
        ${nx ? `<span>Ближайший — ${escapeHtml(quoted(nx.name))}: ещё <span class="still">${nx.left}&nbsp;★</span>. Звёзды ставят на уроке, до 5 за раз.</span>` : ""}
        <button class="btn btn-outline" onclick="setFilter('all')">Смотреть все призы</button>
      </div>`;
    return;
  }
  $("prizesHost").innerHTML = list
    ? `<div class="plist">${shown.map(listRowHtml).join("")}</div>`
    : `<div class="cards">${shown.map(cardHtml).join("")}</div>`;
}

/* ── Рейтинг (S3) ─────────────────────────────────────────── */

function podiumHtml(entry, place) {
  const sizes = { 1: "p1", 2: "p2", 3: "p3" };
  if (!entry) return `<div class="pod ${sizes[place]} empty"><div class="pod-stand"></div></div>`;
  return `
    <div class="pod ${sizes[place]} ${entry.is_me ? "me" : ""}">
      <span class="pod-av"><img src="${escapeHtml(assetUrl(entry.avatar_url))}" alt=""><span class="pod-num">${place}</span></span>
      <span class="pod-name">${escapeHtml(entry.is_me ? "Это ты" : surname(entry.name))}</span>
      <div class="pod-stand">
        <span class="pod-score">${fmt(entry.total_stars)}${starIc("ic")}</span>
        <span class="pod-label">${place}-е место</span>
      </div>
    </div>`;
}

function rankRow(entry, num) {
  return `
    <div class="rrow ${entry.is_me ? "me" : ""}">
      <span class="rnum">${num}</span>
      <img src="${escapeHtml(assetUrl(entry.avatar_url))}" alt="">
      <span class="rname">${entry.is_me
        ? `<span class="rn">${escapeHtml(entry.name)}</span><span class="this-is-you">это ты</span>`
        : `<span class="rn">${escapeHtml(entry.name)}</span>`}</span>
      <span class="rscore">${fmt(entry.total_stars)}${starIc("ic ic-sm")}</span>
    </div>`;
}

function renderRating() {
  const board = data.leaderboard;
  const top = board.top;
  const bal = data.student.total_stars;
  $("ratingTitle").textContent = board.class_size ? `Топ-5 класса · в классе ${board.class_size}` : "Топ-5 класса";

  if (!top.length) {
    $("podium").innerHTML = "";
    $("rankList").innerHTML = `<div class="empty-card">${icon("trophy", "ic ic-xl")}<b>Рейтинг пока пуст</b>Он появится, когда в классе начислят первые звёзды.</div>`;
    $("motivation").hidden = true;
    return;
  }
  $("podium").innerHTML = podiumHtml(top[1], 2) + podiumHtml(top[0], 1) + podiumHtml(top[2], 3);

  let rows = top.slice(3, 5).map((e, i) => rankRow(e, i + 4)).join("");
  if (board.me) {
    if (board.my_place > 6) rows += `<div class="rgap" aria-hidden="true"><i></i><i></i><i></i></div>`;
    rows += rankRow(board.me, bal > 0 ? board.my_place : "—");
  }
  $("rankList").innerHTML = rows;
  $("rankList").hidden = !rows;

  // Мотивация без стыда: «ещё N звёзд до M-го места»
  const mot = $("motivation");
  let text = "";
  if (data.student.rating_hidden) text = "";
  else if (bal === 0) text = "Заработай первую звезду на уроке — и появишься в рейтинге класса.";
  else if (board.my_place === 1) text = `Ты на <b>1-м месте</b> в классе. Так держать!`;
  else if (board.gap_to_next && board.my_place) {
    text = `Ещё <b>${board.gap_to_next}&nbsp;${starWord(board.gap_to_next)}</b> — и ты поднимешься на <b>${board.my_place - 1}-е место</b> в классе!`;
  }
  mot.hidden = !text;
  mot.innerHTML = `${icon("target", "ic ic-lg")}<span>${text}</span>`;
}

/* ── История (S4) ─────────────────────────────────────────── */

function entryTitle(item) {
  if (/^Урок \(/.test(item.reason || "")) return { title: "Урок", sub: `Звёзды за урок в ${data.student.class_name}` };
  if (item.prize) return { title: `Приз: ${item.prize.name}`, sub: "Выдал учитель" };
  if (item.delta > 0) return { title: item.reason || "Начисление", sub: "Начислил учитель" };
  return { title: item.reason || "Списание", sub: "Списал учитель" };
}

function renderHistory() {
  const history = data.history || [];
  if (!history.length) {
    $("monthSummary").hidden = true;
    $("timeline").innerHTML = `
      <div class="hist-empty">
        <span class="empty-ring big">${icon("star-line", "ic ic-xl")}</span>
        <b>Здесь появятся твои звёзды</b>
        <span>Учитель ставит их на уроке — до 5 за раз. Когда заберёшь приз, здесь будет видно, сколько списано.</span>
      </div>`;
    return;
  }

  // «Ясно и быстро»: итог текущего месяца
  const month = new Date().toISOString().slice(0, 7);
  const inMonth = history.filter((h) => String(h.date).startsWith(month));
  const plus = inMonth.filter((h) => h.delta > 0).reduce((s, h) => s + h.delta, 0);
  const minus = inMonth.filter((h) => h.delta < 0).reduce((s, h) => s - h.delta, 0);
  const monthName = new Date().toLocaleDateString("ru-RU", { month: "long" });
  $("monthSummary").hidden = false;
  $("monthSummary").innerHTML = `
    <span class="msum"><span>За ${monthName} получено</span><b class="plus">+${plus} ★</b></span>
    <span class="msum"><span>Потрачено на призы</span><b>${minus ? "−" + minus : "0"} ★</b></span>`;

  // Баланс после каждой записи (история — новые сверху): для шторки «Приз выдан»
  let running = data.student.total_stars;
  const after = history.map((h) => { const value = running; running -= h.delta; return value; });

  const groups = [];
  history.forEach((item, i) => {
    let g = groups[groups.length - 1];
    if (!g || g.date !== item.date) { g = { date: item.date, items: [] }; groups.push(g); }
    g.items.push({ item, i });
  });
  $("timeline").innerHTML = groups.map((g) => `
    <div class="hgroup">
      <div class="hdate">${escapeHtml(formatDay(g.date, { weekday: true, year: false }))}</div>
      <div class="hcard">${g.items.map(({ item, i }) => {
        const { title, sub } = entryTitle(item);
        const plusItem = item.delta > 0;
        const clickable = !plusItem && item.prize;
        return `<${clickable ? "button" : "div"} class="hrow" ${clickable ? `onclick="openIssued(${i}, ${after[i]})"` : ""}>
          ${plusItem ? `<span class="stamp sm"><span>${icon("star")}</span></span>` : `<span class="minus-ring">${icon("gift")}</span>`}
          <span class="htext"><span class="ht">${escapeHtml(title)}</span><span class="hs">${escapeHtml(sub)}</span></span>
          <span class="hamt ${plusItem ? "plus" : "minus"}">${plusItem ? "+" + item.delta : "−" + Math.abs(item.delta)}</span>
        </${clickable ? "button" : "div"}>`;
      }).join("")}</div>
    </div>`).join("");
}

function openIssued(index, balanceAfter) {
  const item = data.history[index];
  if (!item?.prize) return;
  $("issuedBody").innerHTML = `
    <span class="grip"></span>
    <div class="issued-photo">${img(item.prize.image)}<span class="issued-stamp">Выдано</span></div>
    <div class="col-gap6"><div class="sheet-title">Приз у тебя!</div>
      <div class="muted">${escapeHtml(quoted(item.prize.name))} выдан ${escapeHtml(formatDay(item.date, { year: false }))}.</div></div>
    <div class="issued-sum">
      <div><span>Списано</span><b class="muted">−${Math.abs(item.delta)} ★</b></div>
      <div><span>Осталось</span><b>${fmt(balanceAfter)} ★</b></div>
    </div>
    <button class="btn btn-lg btn-block" onclick="closeSheets()">Отлично</button>`;
  openSheet("issuedSheet");
}

/* ── Профиль и аватар (S5, S5a) ───────────────────────────── */

function avatarOrder() {
  const presets = data.avatar_presets.slice();
  if (T() !== "sky") return presets;
  return [...SPACE_FIRST.filter((a) => presets.includes(a)), ...presets.filter((a) => !SPACE_FIRST.includes(a))];
}

function avatarGrid(selected, onPick) {
  return avatarOrder().map((preset) => {
    const sel = preset === selected;
    const name = AVATAR_NAMES[preset] || preset;
    return `<button class="av-opt ${sel ? "sel" : ""}" role="radio" aria-checked="${sel}" aria-label="${name}"
                    onclick="${onPick}('${preset}')">
      <img src="${BASE}/static/img/avatars/${preset}.svg" alt="">
      ${sel ? `<span class="av-check">${icon("check", "ic ic-xs")}</span>` : ""}
    </button>`;
  }).join("");
}

function currentPreset() {
  const a = data.student.avatar || {};
  return a.type === "preset" ? a.value : null;
}

function renderProfile() {
  $("avatarGrid").innerHTML = avatarGrid(currentPreset(), "pickPreset");
  $("tgPhotoBtn").hidden = !telegramPhotoUrl;
  $("infoLogin").textContent = data.student.login || "—";
  renderModeSeg();

  // «Звёздный квест»: полученные призы — цветные, остальные — серые силуэты
  const got = [];
  for (const h of data.history || []) {
    if (h.prize && !got.some((g) => g.id === h.prize.id)) got.push(h.prize);
  }
  const rest = data.prizes.filter((p) => !got.some((g) => g.id === p.id)).slice(0, Math.max(0, 4 - got.length));
  const total = new Set([...data.prizes.map((p) => p.id), ...got.map((g) => g.id)]).size;
  $("trophyTitle").textContent = `Мои призы · ${got.length} из ${total}`;
  $("trophies").innerHTML = [...got.map((p) => ({ ...p, got: true })), ...rest].slice(0, 8).map((p) => `
    <div class="trophy ${p.got ? "got" : ""}">
      <span class="trophy-img">${img(p.image)}</span>
      <span class="trophy-name">${escapeHtml(p.name.split(/[«(/]/)[0].trim() || p.name)}</span>
    </div>`).join("");

  // Учитель в предпросмотре видит блок «Аккаунт» таким, каким его видит ребёнок,
  // но кнопки неактивны: иначе он сменил бы пароль или отвязал Telegram настоящего
  // ученика. Сервер такие запросы из предпросмотра тоже отклоняет.
  $("logoutBlock").querySelectorAll("button, input").forEach((el) => { el.disabled = isPreview; });
  $("previewNote").hidden = !isPreview;
}

async function pickPreset(preset) {
  try {
    await api("/api/me/avatar", { method: "POST", json: { preset } });
    haptic("success");
    await refresh();
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
  }
}

function pickDraft(preset) {
  draftAvatar = preset;
  $("avatarGridSheet").innerHTML = avatarGrid(draftAvatar, "pickDraft");
  haptic();
}

async function saveAvatar() {
  const preset = draftAvatar;
  closeSheets();
  if (preset && preset !== currentPreset()) await pickPreset(preset);
}

async function useTelegramPhoto() {
  try {
    await api("/api/me/avatar", { method: "POST", json: { use_telegram_photo: true, telegram_photo_url: telegramPhotoUrl } });
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
  if (file.size > 5 * 1024 * 1024) return toast("Файл больше 5 МБ — выбери фото поменьше", "error");
  const form = new FormData();
  form.append("file", file);
  toast("Загружаем фото…");
  try {
    await api("/api/me/photo", { method: "POST", body: form });
    haptic("success");
    await refresh();
    closeSheets();
    toast("Готово! Теперь это твой аватар", "success");
  } catch (error) {
    haptic("error");
    toast(error.message, "error");
  }
}

/* ── Шторки и праздник ────────────────────────────────────── */

function openSheet(id) {
  $(id).classList.add("open");
  if (insideTelegram) {
    try { tg.BackButton.show(); } catch (_) {}
  }
}

function closeSheets() {
  document.querySelectorAll(".sheet-wrap.open").forEach((s) => s.classList.remove("open"));
  $("celebrate").hidden = true;
  $("celebrate").innerHTML = "";
  if (insideTelegram) {
    try { tg.BackButton.hide(); } catch (_) {}
  }
}

function openAvatarSheet() {
  haptic();
  draftAvatar = currentPreset() || data.avatar_presets[0];
  $("avatarGridSheet").innerHTML = avatarGrid(draftAvatar, "pickDraft");
  openSheet("avatarSheet");
}

function stampHtml() {
  return `<span class="big-stamp" aria-hidden="true"><span>
    <svg viewBox="0 0 24 24"><polygon points="${STAR_POINTS}"/></svg><b>ОТЛИЧНО</b></span></span>`;
}

/** Праздник «Тебе хватает на приз» — у каждой темы свой. */
function openCelebrate(prizeId) {
  const p = data.prizes.find((x) => x.id === prizeId);
  if (!p || !p.affordable) return;
  haptic("success");
  const st = data.student;
  const first = escapeHtml(firstName(st.name));
  const name = escapeHtml(quoted(p.name));
  const bal = st.total_stars;
  const after = fmt(bal - p.cost);
  const pic = img(p.image);
  const theme = T();
  const later = `<button class="later" onclick="closeSheets()">Позже</button>`;
  const show = `<button class="btn btn-lg btn-block cel-btn" onclick="closeSheets()">Покажу учителю</button>`;
  let html;

  if (theme === "quest") {
    const coins = Array.from({ length: 12 }, (_, i) => {
      const a = (i / 12) * Math.PI * 2 + 0.3;
      const r = 130 + (i % 3) * 26;
      return `<svg class="coin" viewBox="0 0 24 24" style="--dx:${Math.round(Math.cos(a) * r)}px;--dy:${Math.round(Math.sin(a) * r * 0.9)}px;--rot:${(i % 2 ? 1 : -1) * (20 + i * 9)}deg;animation-delay:${i * 25}ms"><polygon points="${STAR_POINTS}"/></svg>`;
    }).join("");
    html = `<div class="cel cel-q" role="dialog" aria-label="Можно забрать приз">
      <div class="coins" aria-hidden="true">${coins}</div>
      <div class="cel-q-photo">${pic}</div>
      <div class="cel-q-title">Можно забрать!</div>
      <div class="cel-q-text">${first}, тебе хватает на ${name}</div>
      <div class="cel-pills"><span>цена <b class="st">${p.cost} ★</b></span><span>останется <b>${after} ★</b></span></div>
      ${show}${later}
    </div>`;
  } else if (theme === "sticker") {
    const spots = [[-8, 6], [90, -4], [102, 40], [-14, 52], [-4, 80], [42, -8], [96, 78], [18, 84]];
    const stars = spots.map(([x, y], i) => `<svg class="k-star" viewBox="0 0 24 24" style="left:${x}%;top:${y}%;animation-delay:${420 + i * 40}ms"><polygon points="${STAR_POINTS}"/></svg>`).join("");
    html = `<div class="cel cel-k" role="dialog" aria-label="Можно забрать приз">
      <div class="k-sticker-wrap" aria-hidden="true"><div class="k-sticker">${pic}</div><span class="k-stamp">МОЖНО ЗАБРАТЬ!</span>${stars}</div>
      <div class="cel-k-title">${first}, тебе хватает!</div>
      <div class="cel-k-text">${name} — цена <b>${p.cost}&nbsp;★</b>, останется <b>${after}&nbsp;★</b></div>
      ${show}${later}
    </div>`;
  } else if (theme === "sky") {
    html = `<div class="cel cel-s" role="dialog" aria-label="Созвездие собрано">
      <div class="cel-s-const">${constellation(p.cost, 1, { h: 170, draw: true })}</div>
      <div class="cel-s-title">Созвездие собрано!</div>
      <div class="cel-s-text">${first}, можно забрать приз <b>${name}</b></div>
      <div class="cel-s-card">${pic}<span>цена <b class="st">${p.cost}&nbsp;★</b><br>останется <b>${after}&nbsp;★</b></span></div>
      ${show}${later}
    </div>`;
  } else if (theme === "clear") {
    html = `<div class="cel-scrim" onclick="closeSheets()"></div>
      <div class="cel-c" role="dialog" aria-label="Тебе хватает на приз">
        <span class="grip"></span>
        <div class="cel-c-photo">${pic}<svg class="flash" viewBox="0 0 24 24" aria-hidden="true"><polygon points="${STAR_POINTS}"/></svg></div>
        <div class="ok-line">${icon("check-circle", "ic ic-sm")}Хватает на приз</div>
        <div class="sheet-title">Тебе хватает!</div>
        <div class="muted">Подойди к учителю — он выдаст <b>${name}</b> и спишет <b>${p.cost}&nbsp;★</b>. Останется <b>${after}&nbsp;★</b>.</div>
        <button class="btn btn-lg btn-block" onclick="closeSheets()">Понятно</button>
      </div>`;
  } else {
    html = `<div class="cel-scrim" onclick="closeSheets()"></div>
      <div class="cel-nb" role="dialog" aria-label="Тебе хватает на приз">
        <div class="cel-nb-frame"><div class="cel-nb-in ruled-bg">
          <span class="cel-nb-caps">Отметка для учителя</span>
          ${stampHtml()}
          <div class="cel-nb-title">${first}, тебе хватает на ${name}!</div>
          <div class="cel-nb-prize">${pic}<span><span>Приз стоит <b class="st">${p.cost}&nbsp;★</b></span>
            <span>У тебя <b>${fmt(bal)}&nbsp;★</b>, останется <b>${after}&nbsp;★</b></span></span></div>
          <div class="muted cel-nb-note">Покажи этот экран учителю — он выдаст приз и спишет звёзды.</div>
          ${show}${later}
        </div></div>
      </div>`;
  }
  $("celebrate").className = `celebrate theme-${theme}`;
  $("celebrate").innerHTML = html;
  $("celebrate").hidden = false;
  if (insideTelegram) {
    try { tg.BackButton.show(); } catch (_) {}
  }
}

/* ── Уведомление «+5 звёзд за урок» ───────────────────────── */

function showStarsToast(delta, entry) {
  const box = $("topToast");
  const when = entry && /^Урок \(/.test(entry.reason || "") ? ` за урок ${escapeHtml(formatDay(entry.date, { year: false }))}` : "";
  box.innerHTML = `
    <span class="stamp sm"><span>${icon("star")}</span></span>
    <span class="tt-text"><b><span class="st">+${delta}&nbsp;${starWord(delta)}</span>${when}</b>
      <span class="muted">Теперь у тебя <span class="nowrap">${fmt(data.student.total_stars)} ★</span></span></span>
    <button class="icon-btn" onclick="$('topToast').hidden = true" aria-label="Закрыть уведомление">${icon("close")}</button>`;
  box.hidden = false;
  clearTimeout(showStarsToast.timer);
  showStarsToast.timer = setTimeout(() => { box.hidden = true; }, 6000);
}

/* ── Общая отрисовка ──────────────────────────────────────── */

function renderAll() {
  renderHero();
  renderPrizes();
  renderRating();
  renderHistory();
  renderProfile();
  updateMini();
}

function afterRefresh() {
  // Новые звёзды — уведомление; новый доступный приз — праздник
  const bal = data.student.total_stars;
  if (balanceBefore !== null && bal > balanceBefore) {
    haptic("success");
    showStarsToast(bal - balanceBefore, data.history?.[0]);
  }
  balanceBefore = bal;
  const now = new Set(data.prizes.filter((p) => p.affordable).map((p) => p.id));
  if (affordableBefore) {
    const fresh = [...now].filter((id) => !affordableBefore.has(id));
    if (fresh.length && $("celebrate").hidden) openCelebrate(fresh[0]);
  }
  affordableBefore = now;
}

function switchView(name) {
  if (name === view) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
  haptic();
  view = name;
  document.querySelectorAll(".view").forEach((v) => {
    const on = v.id === `view-${name}`;
    v.classList.toggle("active", on);
    if (on) { v.classList.remove("tab-in"); void v.offsetWidth; v.classList.add("tab-in"); }
  });
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.view === name) t.setAttribute("aria-current", "page");
    else t.removeAttribute("aria-current");
  });
  document.body.dataset.view = name;
  window.scrollTo(0, 0);
  updateMini();
}

/** Мини-шапка: на «Призах» — после прокрутки за 200 px, на остальных вкладках — всегда. */
function updateMini() {
  const show = view !== "prizes" || window.scrollY > 200;
  $("mini").classList.toggle("shown", show);
}

async function refresh() {
  if (!token) return;
  data = await api("/api/me");
  if (data.student.theme && data.student.theme !== currentTheme()) applyTheme(data.student.theme);
  renderAll();
  afterRefresh();
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
  if (next.length < 5) return toast("Новый пароль — минимум 5 символов", "error");
  try {
    // Смена пароля завершает все сессии, а этому устройству сервер выдаёт новую
    const result = await api("/api/me/password", { method: "POST", json: { current, new: next } });
    if (result.token) {
      token = result.token;
      try { sessionStorage.setItem("stars_token", token); } catch (e) { /* приватный режим */ }
    }
  } catch (error) {
    haptic("error");
    return toast(error.message, "error");
  }
  $("passCurrent").value = "";
  $("passNew").value = "";
  $("passwordForm").hidden = true;
  haptic("success");
  toast("Пароль изменён — теперь его знаешь только ты", "success");
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
    return toast(error.message, "error");
  }
  haptic("success");
  // Опрос звёзд больше не нужен: токен недействителен, запросы вернут 401
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  sessionStorage.removeItem("stars_token");
  token = null;
  data = null;
  closeSheets();
  showScreen("screenBye");
}

function lockTelegramWindow() {
  if (!insideTelegram) return;
  // Свайп вниз на iPhone закрывал кабинет посреди прокрутки рейтинга
  try { tg.disableVerticalSwipes?.(); } catch (e) { /* старый клиент */ }
  try { tg.enableClosingConfirmation?.(); } catch (e) { /* старый клиент */ }
}

function closeCabinet() {
  if (insideTelegram) {
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
    syncMode();
    syncTelegramColors();
    tg.onEvent?.("themeChanged", () => { syncMode(); syncTelegramColors(); });
    tg.BackButton?.onClick?.(closeSheets);
    lockTelegramWindow();
  }
  document.addEventListener("themechange", () => { syncTelegramColors(); if (data) renderAll(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheets(); });

  try {
    await authenticate();
    data = await api("/api/me");
  } catch (error) {
    sessionStorage.removeItem("stars_token");
    // Нет ответа сервера (нет статуса) — это сеть, а не отказ во входе
    if (!error.status && !navigator.onLine) return showScreen("screenNet");
    if (!error.status && error instanceof TypeError) return showScreen("screenNet");
    return showError("Не получилось войти", error.message);
  }

  if (data.student.theme && data.student.theme !== currentTheme()) applyTheme(data.student.theme);
  showScreen("app");
  document.body.dataset.view = view;
  renderAll();
  afterRefresh();
  syncTelegramColors();
  window.addEventListener("scroll", updateMini, { passive: true });

  // Вкладку можно открыть сразу по адресу: /app?view=rating
  const wanted = new URLSearchParams(location.search).get("view");
  if (["rating", "history", "profile"].includes(wanted)) switchView(wanted);

  if (isPreview) {
    $("previewRibbon").hidden = false;
    $("previewText").textContent = `Предпросмотр: кабинет ученика «${data.student.name}»`;
    document.body.classList.add("has-ribbon");
  }

  // Пока кабинет открыт — подтягиваем свежие звёзды (учитель мог начислить прямо сейчас)
  refreshTimer = setInterval(() => { refresh().catch(() => {}); }, 45000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh().catch(() => {});
  });
})();
