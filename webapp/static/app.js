"use strict";

const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }

const INIT_DATA = tg ? tg.initData : "";
const app = document.getElementById("app");
let ME = null;

// --- Утилиты ----------------------------------------------------------------

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

function confirmAsk(msg) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) tg.showConfirm(msg, (ok) => resolve(ok));
    else resolve(window.confirm(msg));
  });
}

async function api(path, opts = {}) {
  const headers = Object.assign(
    { "Content-Type": "application/json", Authorization: "tma " + INIT_DATA },
    opts.headers || {}
  );
  const res = await fetch("/api" + path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let msg = "Ошибка " + res.status;
    try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ISO (YYYY-MM-DD) <-> RU (DD.MM.YYYY)
function isoToRu(iso) {
  if (!iso) return "";
  const p = iso.split("-");
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : iso;
}
function ruToIso(ru) {
  if (!ru) return "";
  const p = ru.split(".");
  return p.length === 3 ? `${p[2]}-${p[1]}-${p[0]}` : ru;
}

function loading() { app.innerHTML = '<div class="empty">Загрузка…</div>'; }

// --- Навигация --------------------------------------------------------------

const VIEWS = {};
let current = "events";

function go(view) {
  current = view;
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  (VIEWS[view] || (() => {}))();
}

document.getElementById("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) go(b.dataset.view);
});

// --- Мероприятия ------------------------------------------------------------

VIEWS.events = async function () {
  loading();
  try {
    const { events } = await api("/events");
    let html = '<button class="btn" id="new-event">➕ Создать мероприятие</button>';
    if (!events.length) {
      html += '<div class="empty">Мероприятий пока нет.</div>';
    } else {
      html += events.map(eventCard).join("");
    }
    app.innerHTML = html;
    document.getElementById("new-event").onclick = showCreateEvent;
    app.querySelectorAll("[data-event]").forEach((el) =>
      (el.onclick = () => showEventDetail(+el.dataset.event)));
  } catch (e) { app.innerHTML = errBox(e); }
};

function eventCard(e) {
  const badge = e.status === "active" ? "active" : (e.status === "cancelled" ? "cancelled" : "");
  return `<div class="card card-tap" data-event="${e.id}">
    <div class="row">
      <h3>${esc(e.title)}</h3>
      <span class="badge ${badge}">${esc(e.status_label)}</span>
    </div>
    <div class="muted">${esc(e.kind_label)} · ${esc(e.date_human)}, ${esc(e.time)}</div>
  </div>`;
}

function errBox(e) {
  return `<div class="empty">⚠️ ${esc(e.message || e)}</div>`;
}

async function showEventDetail(id) {
  loading();
  let ev;
  try { ev = await api("/events/" + id); }
  catch (e) { app.innerHTML = errBox(e); return; }

  const locs = ev.locations || [];
  const locHtml = locs.length
    ? locs.map((l) => `<div class="card">
        <div class="row"><h3>📍 ${esc(l.name)}</h3>
          <span class="badge">${l.taken}/${l.capacity}</span></div>
        <div class="muted">${esc(l.address || "без адреса")}${l.waitlist ? " · " + l.waitlist + " в листе ожидания" : ""}</div>
        <button class="link-btn" data-editloc="${l.id}">Изменить</button>
      </div>`).join("")
    : '<div class="muted" style="padding:6px 4px">Локаций пока нет.</div>';

  app.innerHTML = `
    <button class="link-btn" id="back">← К списку</button>
    <div class="card">
      <div class="row"><h3>${esc(ev.title)}</h3>
        <span class="badge ${ev.status === "active" ? "active" : (ev.status === "cancelled" ? "cancelled" : "")}">${esc(ev.status_label)}</span></div>
      <label>Название</label><input id="f-title" value="${esc(ev.title)}">
      <label>Описание</label><textarea id="f-desc">${esc(ev.description)}</textarea>
      <label>Часовой пояс</label><input id="f-tz" value="${esc(ev.timezone)}">
      <label>Ссылка на альбом</label><input id="f-album" value="${esc(ev.album_url)}" placeholder="необязательно">
      <button class="btn" id="save-ev">Сохранить</button>
      ${ev.has_image ? '<button class="btn secondary" id="rm-img">Убрать картинку</button>' : ''}
    </div>

    <div class="section-title">Дата и время</div>
    <div class="card">
      <div class="muted">Сейчас: ${esc(ev.date_human)}, ${esc(ev.time)}</div>
      <label>Новая дата</label><input type="date" id="m-date" value="${ruToIso(ev.date)}">
      <label>Новое время</label><input type="time" id="m-time" value="${esc(ev.time)}">
      <button class="btn" id="move-ev">📅 Перенести (уведомить участников)</button>
    </div>

    <div class="section-title">Локации</div>
    ${locHtml}
    <button class="btn secondary" id="add-loc">➕ Добавить локацию</button>

    <div class="section-title">Участники</div>
    <div id="parts"><div class="muted" style="padding:6px 4px">Загрузка…</div></div>

    <div class="section-title">Действия</div>
    <div class="card">
      <button class="btn secondary" id="export-ev">📤 Прислать CSV в Telegram</button>
      ${ev.status !== "cancelled" ? '<button class="btn danger" id="cancel-ev">🚫 Отменить (с уведомлением)</button>' : ''}
      <button class="btn danger" id="del-ev">🗑 Удалить навсегда</button>
    </div>`;

  document.getElementById("back").onclick = () => go("events");

  document.getElementById("save-ev").onclick = async () => {
    try {
      await api("/events/" + id, {
        method: "PATCH",
        body: JSON.stringify({
          title: val("f-title"),
          description: val("f-desc"),
          timezone: val("f-tz"),
          album_url: val("f-album"),
        }),
      });
      toast("Сохранено");
    } catch (e) { toast(e.message); }
  };

  const rmImg = document.getElementById("rm-img");
  if (rmImg) rmImg.onclick = async () => {
    try {
      await api("/events/" + id, { method: "PATCH", body: JSON.stringify({ remove_image: true }) });
      toast("Картинка убрана"); showEventDetail(id);
    } catch (e) { toast(e.message); }
  };

  document.getElementById("move-ev").onclick = async () => {
    const date = isoToRu(val("m-date")), time = val("m-time");
    if (!date || !time) return toast("Укажите дату и время");
    if (!(await confirmAsk("Перенести мероприятие? Участники получат уведомление."))) return;
    try {
      const r = await api("/events/" + id + "/move", { method: "POST", body: JSON.stringify({ date, time }) });
      toast("Перенесено. Уведомлений: " + r.notified); showEventDetail(id);
    } catch (e) { toast(e.message); }
  };

  document.getElementById("add-loc").onclick = () => showLocationForm(id, null);
  app.querySelectorAll("[data-editloc]").forEach((el) =>
    (el.onclick = () => showLocationForm(id, +el.dataset.editloc, locs.find((l) => l.id === +el.dataset.editloc))));

  document.getElementById("export-ev").onclick = async () => {
    try { await api("/export", { method: "POST", body: JSON.stringify({ event_id: id }) }); toast("Файл отправлен в Telegram"); }
    catch (e) { toast(e.message); }
  };

  const cancelBtn = document.getElementById("cancel-ev");
  if (cancelBtn) cancelBtn.onclick = async () => {
    if (!(await confirmAsk("Отменить мероприятие? Все участники получат уведомление."))) return;
    try {
      const r = await api("/events/" + id + "/cancel", { method: "POST" });
      toast("Отменено. Уведомлений: " + r.notified); showEventDetail(id);
    } catch (e) { toast(e.message); }
  };

  document.getElementById("del-ev").onclick = async () => {
    if (!(await confirmAsk("Удалить навсегда? Записи будут удалены БЕЗ уведомления."))) return;
    try { await api("/events/" + id, { method: "DELETE" }); toast("Удалено"); go("events"); }
    catch (e) { toast(e.message); }
  };

  loadParticipants(id);
}

function val(id) { return document.getElementById(id).value.trim(); }

async function loadParticipants(eventId) {
  const box = document.getElementById("parts");
  if (!box) return;
  try {
    const { registrations } = await api("/events/" + eventId + "/registrations");
    if (!registrations.length) {
      box.innerHTML = '<div class="muted" style="padding:6px 4px">Пока никто не записан.</div>';
      return;
    }
    box.innerHTML = registrations.map((r) => `
      <div class="card">
        <div class="row">
          <div>
            <b>${esc(r.full_name)}</b>
            <div class="muted">${esc(r.location)} · ${esc(r.status_label)}${r.queue_pos ? " #" + r.queue_pos : ""}</div>
            <div class="muted">${esc(r.phone || "телефон не указан")}</div>
          </div>
          <button class="link-btn danger-link" data-cancelreg="${r.id}">Снять</button>
        </div>
      </div>`).join("");
    box.querySelectorAll("[data-cancelreg]").forEach((b) => {
      b.onclick = async () => {
        if (!(await confirmAsk("Снять запись участника? Он получит уведомление, место уйдёт следующему в очереди."))) return;
        try {
          const r = await api("/registrations/" + b.dataset.cancelreg + "/cancel", { method: "POST" });
          toast("Снято. Уведомлений: " + r.notified);
          loadParticipants(eventId);
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) { box.innerHTML = errBox(e); }
}

function showCreateEvent() {
  app.innerHTML = `
    <button class="link-btn" id="back">← К списку</button>
    <div class="card">
      <h3>Новое мероприятие</h3>
      <label>Название</label><input id="c-title">
      <label>Описание</label><textarea id="c-desc"></textarea>
      <label>Тип</label>
      <select id="c-kind">
        <option value="run">Пробежка (несколько локаций)</option>
        <option value="simple">Закрытое мероприятие (одна локация)</option>
      </select>
      <label>Дата</label><input type="date" id="c-date">
      <label>Время</label><input type="time" id="c-time">
      <label>Часовой пояс</label><input id="c-tz" value="${esc(ME ? ME.default_timezone : "Europe/Moscow")}">
      <button class="btn" id="create">Создать</button>
      <div class="muted" style="margin-top:8px">После создания добавьте локации. Картинку можно задать в боте фото-сообщением.</div>
    </div>`;
  document.getElementById("back").onclick = () => go("events");
  document.getElementById("create").onclick = async () => {
    try {
      const ev = await api("/events", {
        method: "POST",
        body: JSON.stringify({
          title: val("c-title"),
          description: val("c-desc"),
          kind: val("c-kind"),
          date: isoToRu(val("c-date")),
          time: val("c-time"),
          timezone: val("c-tz"),
        }),
      });
      toast("Создано"); showEventDetail(ev.id);
    } catch (e) { toast(e.message); }
  };
}

function showLocationForm(eventId, locId, loc) {
  const l = loc || { name: "", address: "", capacity: "", timezone: "" };
  app.innerHTML = `
    <button class="link-btn" id="back">← К мероприятию</button>
    <div class="card">
      <h3>${locId ? "Изменить локацию" : "Новая локация"}</h3>
      <label>Название</label><input id="l-name" value="${esc(l.name)}">
      <label>Адрес</label><input id="l-addr" value="${esc(l.address)}">
      <label>Лимит мест</label><input type="number" id="l-cap" value="${esc(l.capacity)}" min="0">
      <label>Часовой пояс (необязательно)</label><input id="l-tz" value="${esc(l.timezone)}">
      <button class="btn" id="save-loc">Сохранить</button>
    </div>`;
  document.getElementById("back").onclick = () => showEventDetail(eventId);
  document.getElementById("save-loc").onclick = async () => {
    const body = { name: val("l-name"), address: val("l-addr"), capacity: val("l-cap") };
    const tz = val("l-tz"); if (tz) body.timezone = tz;
    try {
      if (locId) {
        const r = await api("/locations/" + locId, { method: "PATCH", body: JSON.stringify(body) });
        toast(r.notified ? "Сохранено. Уведомлений: " + r.notified : "Сохранено");
      } else {
        await api("/events/" + eventId + "/locations", { method: "POST", body: JSON.stringify(body) });
        toast("Локация добавлена");
      }
      showEventDetail(eventId);
    } catch (e) { toast(e.message); }
  };
}

// --- Тексты -----------------------------------------------------------------

VIEWS.texts = async function () {
  loading();
  try {
    const { texts } = await api("/texts");
    app.innerHTML = texts.map((t) => `
      <div class="card">
        <h3>${esc(t.label)}</h3>
        <textarea data-key="${t.key}">${esc(t.text)}</textarea>
        <button class="btn" data-save="${t.key}">Сохранить</button>
        ${t.supports_image ? '<div class="muted" style="margin-top:6px">Картинку раздела задайте в боте: фото с подписью.</div>' : ''}
      </div>`).join("");
    app.querySelectorAll("[data-save]").forEach((btn) => {
      btn.onclick = async () => {
        const key = btn.dataset.save;
        const text = app.querySelector(`textarea[data-key="${key}"]`).value;
        try { await api("/texts/" + key, { method: "PATCH", body: JSON.stringify({ text }) }); toast("Сохранено"); }
        catch (e) { toast(e.message); }
      };
    });
  } catch (e) { app.innerHTML = errBox(e); }
};

// --- Люди (пользователи + рассылка) -----------------------------------------

let userQuery = "";

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

VIEWS.users = function () {
  app.innerHTML = `
    <button class="btn secondary" id="broadcast-btn">📣 Рассылка</button>
    <input id="user-search" placeholder="Поиск: имя, телефон, email" value="${esc(userQuery)}">
    <div id="userlist"><div class="empty">Загрузка…</div></div>`;
  document.getElementById("broadcast-btn").onclick = showBroadcast;
  const s = document.getElementById("user-search");
  s.oninput = debounce(() => { userQuery = s.value.trim(); loadUsers(); }, 350);
  loadUsers();
};

async function loadUsers() {
  const box = document.getElementById("userlist");
  if (!box) return;
  try {
    const { users, truncated } = await api("/users" + (userQuery ? "?q=" + encodeURIComponent(userQuery) : ""));
    if (!users.length) { box.innerHTML = '<div class="empty">Никого не найдено.</div>'; return; }
    box.innerHTML = users.map(userCard).join("") +
      (truncated ? '<div class="muted" style="text-align:center;padding:8px">Показаны первые 100 — уточните поиск.</div>' : "");
    box.querySelectorAll("[data-user]").forEach((el) =>
      (el.onclick = () => showUserDetail(+el.dataset.user)));
  } catch (e) { box.innerHTML = errBox(e); }
}

function userCard(u) {
  return `<div class="card card-tap" data-user="${u.id}">
    <div class="row"><h3>${esc(u.full_name)}</h3>${u.reg_count ? `<span class="badge active">${u.reg_count} зап.</span>` : ""}</div>
    <div class="muted">${esc(u.phone || "без телефона")}${u.email ? " · " + esc(u.email) : ""}</div>
    <div class="muted">с ${esc(u.created_at)}</div>
  </div>`;
}

async function showUserDetail(id) {
  loading();
  let u;
  try { u = await api("/users/" + id); } catch (e) { app.innerHTML = errBox(e); return; }
  const regs = u.registrations || [];
  app.innerHTML = `
    <button class="link-btn" id="back">← К списку</button>
    <div class="card">
      <h3>${esc(u.full_name)}</h3>
      <div class="muted">📞 ${esc(u.phone || "—")}</div>
      <div class="muted">✉️ ${esc(u.email || "—")}</div>
      <div class="muted">Telegram ID: ${u.telegram_id}</div>
      <div class="muted">В боте с ${esc(u.created_at)}</div>
      <div class="muted">Согласие на ПДн: ${u.has_pdn_consent ? "да" : "нет"}</div>
    </div>
    <div class="section-title">Записи (${regs.length})</div>
    ${regs.length ? regs.map((r) => `
      <div class="card">
        <div class="row"><b>${esc(r.event_title)}</b><span class="badge">${esc(r.status_label)}</span></div>
        <div class="muted">${esc(r.location)} · ${esc(r.date)}</div>
      </div>`).join("") : '<div class="muted" style="padding:6px 4px">Записей нет.</div>'}`;
  document.getElementById("back").onclick = () => go("users");
}

async function showBroadcast() {
  loading();
  let events = [];
  try { events = (await api("/events")).events.filter((e) => e.status !== "cancelled"); } catch (e) {}
  app.innerHTML = `
    <button class="link-btn" id="back">← К людям</button>
    <div class="card">
      <h3>📣 Рассылка</h3>
      <label>Кому</label>
      <select id="b-target">
        <option value="all">Всем пользователям бота</option>
        <option value="event">Участникам мероприятия</option>
      </select>
      <div id="b-event-wrap" class="hidden">
        <label>Мероприятие</label>
        <select id="b-event">${events.map((e) => `<option value="${e.id}">${esc(e.title)} — ${esc(e.date_human)}</option>`).join("")}</select>
      </div>
      <label>Текст сообщения</label>
      <textarea id="b-text" placeholder="Что отправить участникам?"></textarea>
      <div class="muted" id="b-count">Получателей: …</div>
      <button class="btn" id="b-send">Отправить</button>
    </div>`;
  document.getElementById("back").onclick = () => go("users");
  const target = document.getElementById("b-target");
  const wrap = document.getElementById("b-event-wrap");
  const evSel = document.getElementById("b-event");

  async function refreshCount() {
    const body = { target: target.value, preview: true };
    if (target.value === "event") body.event_id = evSel && evSel.value ? +evSel.value : null;
    document.getElementById("b-count").textContent = "Получателей: …";
    try {
      const r = await api("/broadcast", { method: "POST", body: JSON.stringify(body) });
      document.getElementById("b-count").textContent = "Получателей: " + r.count;
    } catch (e) { document.getElementById("b-count").textContent = "—"; }
  }
  target.onchange = () => { wrap.classList.toggle("hidden", target.value !== "event"); refreshCount(); };
  if (evSel) evSel.onchange = refreshCount;
  refreshCount();

  document.getElementById("b-send").onclick = async () => {
    const text = document.getElementById("b-text").value.trim();
    if (!text) return toast("Введите текст");
    const body = { target: target.value, text };
    if (target.value === "event") {
      if (!evSel || !evSel.value) return toast("Выберите мероприятие");
      body.event_id = +evSel.value;
    }
    if (!(await confirmAsk("Отправить сообщение? Оно уйдёт реальным пользователям."))) return;
    const btn = document.getElementById("b-send");
    btn.disabled = true; btn.textContent = "Отправляю…";
    try {
      const r = await api("/broadcast", { method: "POST", body: JSON.stringify(body) });
      toast("Отправлено: " + r.sent + " из " + r.total);
    } catch (e) { toast(e.message); }
    btn.disabled = false; btn.textContent = "Отправить";
  };
}

// --- Аналитика --------------------------------------------------------------

VIEWS.analytics = async function () {
  loading();
  try {
    const a = await api("/analytics");
    const u = a.users, r = a.registrations;
    const maxG = Math.max(1, ...a.growth.map((g) => g.count));
    const growthBars = a.growth.map((g) =>
      `<div class="bar-col" title="${g.date}: ${g.count}"><div class="bar" style="height:${Math.round(g.count / maxG * 100)}%"></div><div class="bar-x">${g.date.split(".")[0]}</div></div>`).join("");
    const funnelMax = Math.max(1, u.total);
    const frow = (label, v) =>
      `<div class="frow"><div class="frow-l">${label}</div><div class="frow-bar"><div style="width:${Math.round(v / funnelMax * 100)}%"></div></div><div class="frow-v">${v}</div></div>`;

    app.innerHTML = `
      <div class="stat-grid">
        <div class="stat"><div class="num">${u.total}</div><div class="lbl">пользователей</div></div>
        <div class="stat"><div class="num">${u.new_7d}</div><div class="lbl">новых за 7 дн.</div></div>
        <div class="stat"><div class="num">${u.new_30d}</div><div class="lbl">новых за 30 дн.</div></div>
        <div class="stat"><div class="num">${r.confirmed}</div><div class="lbl">записаны</div></div>
        <div class="stat"><div class="num">${r.waitlist}</div><div class="lbl">лист ожидания</div></div>
        <div class="stat"><div class="num">${r.cancelled}</div><div class="lbl">отмен</div></div>
      </div>

      <div class="section-title">Новые пользователи (14 дней)</div>
      <div class="card"><div class="bars">${growthBars}</div></div>

      <div class="section-title">Воронка</div>
      <div class="card">
        ${frow("Зашли в бота", u.total)}
        ${frow("Заполнили анкету", u.complete)}
        ${frow("Записались хоть раз", u.ever_registered)}
      </div>

      <div class="section-title">Топ локаций</div>
      <div class="card">
        ${a.top_locations.length ? a.top_locations.map((l) => `<div class="row"><span>${esc(l.name)}</span><span class="badge">${l.count}</span></div>`).join("") : '<div class="muted">Пока нет данных.</div>'}
      </div>

      <div class="section-title">Ближайшие мероприятия</div>
      ${a.upcoming.length ? a.upcoming.map((e) => `
        <div class="card">
          <div class="row"><b>${esc(e.title)}</b><span class="badge">${e.taken}/${e.capacity}</span></div>
          <div class="muted">${esc(e.date)}</div>
          <div class="frow-bar" style="margin-top:8px"><div style="width:${e.percent}%"></div></div>
        </div>`).join("") : '<div class="muted" style="padding:6px 4px">Нет предстоящих.</div>'}

      <div class="section-title">Обслуживание</div>
      <div class="card">
        <button class="btn secondary" id="export-all">📤 Экспорт регистраций (CSV)</button>
        <button class="btn secondary" id="export-users">👥 Экспорт пользователей (CSV)</button>
        <button class="btn secondary" id="recount">🔄 Пересчитать места</button>
      </div>`;

    document.getElementById("export-all").onclick = async () => {
      try { await api("/export", { method: "POST", body: JSON.stringify({}) }); toast("Файл отправлен в Telegram"); }
      catch (e) { toast(e.message); }
    };
    document.getElementById("export-users").onclick = async () => {
      try { await api("/users/export", { method: "POST" }); toast("Файл отправлен в Telegram"); }
      catch (e) { toast(e.message); }
    };
    document.getElementById("recount").onclick = async () => {
      try { const r = await api("/recount", { method: "POST" }); toast("Готово. Исправлено: " + r.fixed); }
      catch (e) { toast(e.message); }
    };
  } catch (e) { app.innerHTML = errBox(e); }
};

// --- Старт ------------------------------------------------------------------

(async function init() {
  try {
    ME = await api("/me");
    document.getElementById("topbar-sub").textContent =
      ME.user && ME.user.name ? "Вы вошли как " + ME.user.name : "";
  } catch (e) {
    app.innerHTML = `<div class="empty">⚠️ ${esc(e.message)}<br><br>Открывайте панель из бота командой /admin.</div>`;
    document.getElementById("tabs").style.display = "none";
    return;
  }
  go("events");
})();
