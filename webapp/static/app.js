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
}

function val(id) { return document.getElementById(id).value.trim(); }

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

// --- Статистика -------------------------------------------------------------

VIEWS.stats = async function () {
  loading();
  try {
    const s = await api("/stats");
    app.innerHTML = `
      <div class="stat-grid">
        <div class="stat"><div class="num">${s.active_events}</div><div class="lbl">активных мероприятий</div></div>
        <div class="stat"><div class="num">${s.confirmed}</div><div class="lbl">записаны</div></div>
        <div class="stat"><div class="num">${s.waitlist}</div><div class="lbl">в листе ожидания</div></div>
      </div>
      <button class="btn secondary" id="export-all" style="margin-top:16px">📤 Экспорт всех регистраций (CSV в Telegram)</button>
      <button class="btn secondary" id="recount">🔄 Пересчитать места</button>`;
    document.getElementById("export-all").onclick = async () => {
      try { await api("/export", { method: "POST", body: JSON.stringify({}) }); toast("Файл отправлен в Telegram"); }
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
