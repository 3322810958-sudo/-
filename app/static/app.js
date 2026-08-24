"use strict";

const state = {
  csrf: "", user: null, members: [], categories: [], fundingSources: [], productTypes: [],
  settings: {}, dashboard: null, invoices: [], users: [], currentView: "dashboard", socket: null,
  resizeTimer: null, publicSettings: {}, loginSlideTimer: null, loginSlideIndex: 0,
  loginActiveLayer: "A", appearanceSlides: [], appearanceBackground: null,
  wallpapers: [], classificationRules: [],
};

const $ = (id) => document.getElementById(id);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const money = (value) => `¥${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const dateText = (value) => value ? String(value).slice(0, 10) : "-";
const nowDate = () => new Date().toISOString().slice(0, 10);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const roleLabel = (role) => ({ admin: "管理员", member: "成员", viewer: "公共只读" }[role] || role);
const statusLabel = (status) => ({ pending: "未报销", partial: "部分报销", reimbursed: "已报销" }[status] || status);
const burdenLabel = (type) => ({ team_aa: "全队 AA", specified_split: "指定成员", self_paid: "个人承担" }[type] || type);
const actionLabel = (action) => ({ create: "新增", update: "修改", delete: "删除", login: "登录", logout: "退出", restore: "版本回溯", upload: "上传附件", seed: "初始化", archive: "停用", sync_apply: "同步应用", sync_conflict: "同步冲突", create_snapshot: "建立版本", delete_demo: "清除演示数据", batch_import: "批量导入", ocr_complete: "OCR 完成", change_credentials: "修改账号" }[action] || action);

function canWrite() { return Boolean(state.user?.permissions?.write); }
function isAdmin() { return state.user?.role === "admin"; }

async function api(path, options = {}) {
  const config = { credentials: "same-origin", ...options };
  config.headers = { ...(options.headers || {}) };
  if (state.csrf && String(config.method || "GET").toUpperCase() !== "GET") config.headers["X-CSRF-Token"] = state.csrf;
  if (config.body && !(config.body instanceof FormData) && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  if (response.status === 401) {
    showLogin();
    throw new Error("登录已失效，请重新登录");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `请求失败 (${response.status})`);
  return payload;
}

function toast(message, type = "success", duration = 3300) {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("toastRegion").appendChild(item);
  setTimeout(() => item.remove(), duration);
}

function loading(show, text = "正在处理") {
  $("loadingText").textContent = text;
  $("loadingOverlay").classList.toggle("hidden", !show);
}

function showLogin() {
  state.user = null; state.csrf = "";
  $("loginPassword").type = "password"; $("toggleLoginPassword").textContent = "显示"; $("toggleLoginPassword").setAttribute("aria-pressed", "false"); $("toggleLoginPassword").setAttribute("aria-label", "显示密码");
  $("appShell").classList.add("hidden");
  $("loginView").classList.remove("hidden");
  if (state.socket) { state.socket.close(); state.socket = null; }
  startLoginSlideshow();
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appShell").classList.remove("hidden");
  stopLoginSlideshow();
}

function setAccent(hex) {
  const safe = /^#[0-9a-fA-F]{6}$/.test(hex || "") ? hex : "#27d3ff";
  const rgb = [1, 3, 5].map((index) => parseInt(safe.slice(index, index + 2), 16)).join(", ");
  document.documentElement.style.setProperty("--accent", safe);
  document.documentElement.style.setProperty("--accent-rgb", rgb);
}

function stopLoginSlideshow() {
  clearTimeout(state.loginSlideTimer); state.loginSlideTimer = null;
  $$("#loginMediaStage video").forEach((video) => video.pause());
}

function fillLoginSlide(layer, slide) {
  layer.replaceChildren();
  const media = document.createElement(slide.kind === "video" ? "video" : "img");
  media.src = slide.url;
  if (slide.kind === "video") { media.muted = true; media.loop = true; media.autoplay = true; media.playsInline = true; }
  else media.alt = "登录界面轮播图片";
  layer.appendChild(media);
  if (slide.kind === "video") media.play().catch(() => {});
}

function showLoginSlide(index, immediate = false) {
  const settings = state.publicSettings || {};
  const slides = settings.login_slides || [];
  if (!settings.login_slideshow_enabled || !slides.length || $("loginView").classList.contains("hidden")) {
    $("loginMediaStage").classList.add("hidden"); return;
  }
  $("loginMediaStage").classList.remove("hidden");
  state.loginSlideIndex = ((index % slides.length) + slides.length) % slides.length;
  const slide = slides[state.loginSlideIndex];
  const current = $(`loginSlideLayer${state.loginActiveLayer}`);
  const nextKey = immediate ? state.loginActiveLayer : state.loginActiveLayer === "A" ? "B" : "A";
  const next = $(`loginSlideLayer${nextKey}`);
  fillLoginSlide(next, slide);
  if (!immediate) {
    next.classList.toggle("slide-enter", settings.login_transition === "slide");
    requestAnimationFrame(() => { next.classList.add("active"); current.classList.remove("active"); });
    setTimeout(() => { current.replaceChildren(); next.classList.remove("slide-enter"); }, 1000);
  } else next.classList.add("active");
  state.loginActiveLayer = nextKey;
  $("loginSlideCaption").textContent = slide.title || "";
  $("loginSlideCaption").classList.toggle("hidden", !slide.title);
  clearTimeout(state.loginSlideTimer);
  state.loginSlideTimer = setTimeout(() => showLoginSlide(state.loginSlideIndex + 1), Math.max(2, Number(slide.duration || 8)) * 1000);
}

function startLoginSlideshow() {
  stopLoginSlideshow(); showLoginSlide(state.loginSlideIndex || 0, true);
}

function applyPublicAppearance(settings = {}) {
  state.publicSettings = settings;
  setAccent(settings.accent_color || "#27d3ff");
  const teamName = String(settings.team_name || "燕翔车队").replace(/\s*Racing Team\s*/i, "").trim() || "燕翔车队";
  $("loginTeamName").textContent = teamName;
  if (!$("loginView").classList.contains("hidden")) startLoginSlideshow();
}

async function loadPublicAppearance() {
  try { const result = await api("/api/public/appearance"); applyPublicAppearance(result.settings || {}); }
  catch (_) { applyPublicAppearance({}); }
}

function applyTheme() {
  const settings = state.settings || {};
  const mediaUrl = settings.background_media_url || settings.background_image || "";
  const mediaKind = settings.background_media_kind || "image";
  const background = mediaUrl && mediaKind !== "video" ? `url("${String(mediaUrl).replace(/"/g, "%22")}")` : "none";
  document.documentElement.style.setProperty("--custom-background", background);
  document.documentElement.style.setProperty("--background-overlay", String(settings.background_overlay || ".82"));
  setAccent(settings.accent_color);
  const video = $("appBackgroundVideo");
  if (mediaUrl && mediaKind === "video") {
    if (video.getAttribute("src") !== mediaUrl) video.src = mediaUrl;
    video.classList.add("active"); video.play().catch(() => {});
  } else { video.pause(); video.removeAttribute("src"); video.load(); video.classList.remove("active"); }
  document.title = `${settings.team_name || "燕翔车队"} · 经费管理系统 V2.1`;
}

function applyAccess() {
  $$('[data-admin]').forEach((element) => element.classList.toggle("hidden", !isAdmin()));
  $$(".write-only").forEach((element) => element.classList.toggle("hidden", !canWrite()));
  if (!isAdmin() && state.currentView === "history") navigate("dashboard");
  $("userName").textContent = state.user.display_name;
  $("userRole").textContent = roleLabel(state.user.role);
  $("userAvatar").textContent = state.user.display_name.slice(0, 1);
}

function renderSync(sync = {}) {
  const lamp = $("syncLamp");
  lamp.className = "status-lamp";
  if (sync.last_error) {
    lamp.classList.add("error");
    $("syncLabel").textContent = "同步异常";
    $("syncDetail").textContent = sync.last_error.slice(0, 28);
    $("dashboardSync").textContent = "异常";
  } else if (sync.enabled) {
    lamp.classList.add("cloud");
    $("syncLabel").textContent = "云端同步已启用";
    $("syncDetail").textContent = sync.pending_events ? `${sync.pending_events} 项待上传` : "本地与云端一致";
    $("dashboardSync").textContent = "云端";
  } else {
    $("syncLabel").textContent = "本地模式";
    $("syncDetail").textContent = "数据安全保存在本机";
    $("dashboardSync").textContent = "本地";
  }
  $("syncSettingText").textContent = sync.enabled ? `已连接 ${sync.remote_url || "云端服务器"}` : "未配置云端服务器。";
}

async function loadBootstrap() {
  const data = await api("/api/bootstrap");
  state.csrf = data.csrf_token;
  state.user = data.user;
  state.members = data.members || [];
  state.categories = data.categories || [];
  state.fundingSources = data.funding_sources || [];
  state.productTypes = data.product_types || [];
  state.settings = data.settings || {};
  state.dashboard = data.dashboard;
  state.sync = data.sync || {};
  $("versionLabel").textContent = `V${data.version}`;
  state.publicSettings = state.settings; applyTheme(); applyAccess(); renderSync(state.sync); renderReferenceOptions(); renderDashboard(); showApp(); connectSocket();
  if (state.user.must_change_password) {
    setTimeout(() => { toast("首次登录请先修改账号与密码", "error", 5000); openCredentials(); }, 350);
  }
}

function renderReferenceOptions() {
  const activeMembers = state.members.filter((item) => item.active && !item.deleted_at);
  const memberOptions = activeMembers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.department || "未分组")}</option>`).join("");
  ["invoicePayer", "batchPayer", "settlementFrom", "settlementTo"].forEach((id) => { if ($(id)) $(id).innerHTML = memberOptions; });
  $("editUserMember").innerHTML = `<option value="">不关联</option>${memberOptions}`;
  const categoryOptions = state.categories.filter((item) => item.active && !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  ["invoiceCategory", "batchCategory"].forEach((id) => { $(id).innerHTML = `<option value="">未分类</option>${categoryOptions}`; });
  $("invoiceCategoryFilter").innerHTML = `<option value="">全部分类</option>${categoryOptions}`;
  const sourceOptions = state.fundingSources.filter((item) => item.active && !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  ["invoiceSource", "batchSource"].forEach((id) => { $(id).innerHTML = `<option value="">未选择</option>${sourceOptions}`; });
  $("invoiceSourceFilter").innerHTML = `<option value="">全部来源</option>${sourceOptions}`;
  $("invoiceProduct").innerHTML = state.productTypes.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  renderReferenceManagers();
}

const sourceTypeLabel = (value) => ({ team: "车队经费", reimbursement: "报销款", sponsor: "赞助款", aa: "成员 AA", loan: "借款", other: "其他" }[value] || "其他");

function renderReferenceManagers() {
  if (!$("categoryManagerList") || !$("sourceManagerList")) return;
  $("categoryManagerList").innerHTML = state.categories.map((item) => `<div class="reference-item${item.active ? "" : " inactive"}"><i class="reference-swatch" style="--reference-color:${escapeHtml(item.color || "#27d3ff")}"></i><div><b>${escapeHtml(item.name)}</b><small>${item.active ? "已启用" : "已停用"}</small></div><button type="button" class="btn secondary compact" data-category-edit="${escapeHtml(item.id)}">编辑</button></div>`).join("") || `<div class="empty-state">暂无费用分类</div>`;
  $("sourceManagerList").innerHTML = state.fundingSources.map((item) => `<div class="reference-item${item.active ? "" : " inactive"}"><i class="reference-swatch" style="--reference-color:${escapeHtml(item.color || "#27d3ff")}"></i><div><b>${escapeHtml(item.name)}</b><small>${sourceTypeLabel(item.source_type)} · ${item.active ? "已启用" : "已停用"}</small></div><button type="button" class="btn secondary compact" data-source-edit="${escapeHtml(item.id)}">编辑</button></div>`).join("") || `<div class="empty-state">暂无资金来源</div>`;
}

function resetCategoryEditor() {
  $("categoryForm").reset(); $("categoryId").value = ""; $("categoryColor").value = "#27d3ff"; $("categoryActive").checked = true; $("categorySaveBtn").textContent = "添加分类";
}

function resetSourceEditor() {
  $("sourceForm").reset(); $("sourceId").value = ""; $("sourceColor").value = "#27d3ff"; $("sourceActive").checked = true; $("sourceType").value = "team"; $("sourceSaveBtn").textContent = "添加来源";
}

function openReferences() {
  resetCategoryEditor(); resetSourceEditor(); renderReferenceManagers(); $("referencesDialog").showModal();
}

function editCategory(item) {
  if (!item) return; $("categoryId").value = item.id; $("categoryName").value = item.name; $("categoryColor").value = item.color || "#27d3ff"; $("categoryActive").checked = Boolean(item.active); $("categorySaveBtn").textContent = "保存修改"; $("categoryName").focus();
}

function editSource(item) {
  if (!item) return; $("sourceId").value = item.id; $("sourceName").value = item.name; $("sourceType").value = item.source_type || "other"; $("sourceColor").value = item.color || "#27d3ff"; $("sourceActive").checked = Boolean(item.active); $("sourceSaveBtn").textContent = "保存修改"; $("sourceName").focus();
}

function upsertReference(list, item) {
  const index = list.findIndex((entry) => entry.id === item.id); if (index >= 0) list[index] = item; else list.push(item);
  list.sort((left, right) => Number(Boolean(right.active)) - Number(Boolean(left.active)) || Number(left.sort_order || 0) - Number(right.sort_order || 0) || String(left.name).localeCompare(String(right.name), "zh-CN"));
}

async function saveCategory(event) {
  event.preventDefault(); const id = $("categoryId").value;
  try {
    const item = await api(id ? `/api/categories/${encodeURIComponent(id)}` : "/api/categories", { method: id ? "PUT" : "POST", body: { name: $("categoryName").value, color: $("categoryColor").value, active: $("categoryActive").checked } });
    upsertReference(state.categories, item); renderReferenceOptions(); resetCategoryEditor(); toast(id ? "费用分类已更新" : "费用分类已添加");
  } catch (error) { toast(error.message, "error"); }
}

async function saveSource(event) {
  event.preventDefault(); const id = $("sourceId").value;
  try {
    const item = await api(id ? `/api/funding-sources/${encodeURIComponent(id)}` : "/api/funding-sources", { method: id ? "PUT" : "POST", body: { name: $("sourceName").value, source_type: $("sourceType").value, color: $("sourceColor").value, active: $("sourceActive").checked } });
    upsertReference(state.fundingSources, item); renderReferenceOptions(); resetSourceEditor(); toast(id ? "资金来源已更新" : "资金来源已添加");
  } catch (error) { toast(error.message, "error"); }
}

const routes = {
  dashboard: ["数据驾驶舱 / 01", "数据驾驶舱"], invoices: ["发票台账 / 02", "发票台账"],
  settlements: ["成员结算 / 03", "AA 结算"], reports: ["经费统计 / 04", "分类统计"],
  members: ["成员权限 / 05", "成员与账号"], history: ["版本控制 / 06", "日志与回溯"],
  settings: ["系统配置 / 07", "系统设置"],
};

async function navigate(view) {
  if (!routes[view] || (view === "history" && !isAdmin())) return;
  state.currentView = view;
  $$(".view-section").forEach((section) => section.classList.remove("active"));
  $(`${view}Section`).classList.add("active");
  $$("#mainNav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("routeCode").textContent = routes[view][0]; $("routeTitle").textContent = routes[view][1];
  $("mainNav").closest(".sidebar").classList.remove("open");
  try {
    if (view === "dashboard") await refreshDashboard();
    if (view === "invoices") await loadInvoices();
    if (view === "settlements") await loadSettlements();
    if (view === "reports") await loadReports();
    if (view === "members") await loadMembersAndUsers();
    if (view === "history") await loadHistory();
  } catch (error) { toast(error.message, "error"); }
}

function connectSocket() {
  if (state.socket) state.socket.close();
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);
  state.socket = socket;
  socket.addEventListener("open", () => { $("liveBadge").classList.remove("offline"); $("liveBadge").querySelector("span").textContent = "实时"; socket.send("ready"); });
  socket.addEventListener("close", () => {
    $("liveBadge").classList.add("offline"); $("liveBadge").querySelector("span").textContent = "离线";
    if (state.user) setTimeout(connectSocket, 3500);
  });
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data || "{}");
    if (payload.event === "connected") return;
    clearTimeout(state.liveRefreshTimer);
    state.liveRefreshTimer = setTimeout(() => refreshCurrentView(true), 500);
  });
}

async function refreshCurrentView(silent = false) {
  try {
    const data = await api("/api/bootstrap");
    state.members = data.members; state.categories = data.categories; state.fundingSources = data.funding_sources;
    state.settings = data.settings; state.dashboard = data.dashboard; state.sync = data.sync; state.csrf = data.csrf_token;
    state.publicSettings = state.settings; applyTheme(); renderReferenceOptions(); renderSync(state.sync);
    if (state.currentView === "dashboard") renderDashboard();
    if (state.currentView === "invoices") await loadInvoices();
    if (state.currentView === "settlements") await loadSettlements();
    if (state.currentView === "reports") await loadReports();
    if (state.currentView === "members") await loadMembersAndUsers();
    if (state.currentView === "history" && isAdmin()) await loadHistory();
    if (!silent) toast("数据已刷新");
  } catch (error) { if (!silent) toast(error.message, "error"); }
}

async function refreshDashboard() {
  state.dashboard = await api("/api/dashboard");
  renderDashboard();
}

function renderDashboard() {
  const data = state.dashboard || { categories: [], monthly: [], recent: [] };
  $("metricTotal").textContent = money(data.total_amount); $("metricPending").textContent = money(data.pending_amount);
  $("metricReimbursed").textContent = money(data.reimbursed_amount); $("metricAA").textContent = money(data.aa_outstanding);
  $("metricInvoiceCount").textContent = `${data.invoice_count || 0} 张发票`;
  $("dashboardSubtitle").textContent = `${state.settings.team_name || "燕翔车队"} · ${data.invoice_count || 0} 笔有效记录`;
  $("recentTable").innerHTML = (data.recent || []).map((item) => `<tr>
    <td>${escapeHtml(item.invoice_date)}</td><td><span class="cell-main">${escapeHtml(item.vendor || "待补充")}</span><span class="cell-sub">${escapeHtml(item.product_type)}</span></td>
    <td>${colorTag(item.category_name || "未分类", item.category_color || "#9aa7b7")}</td><td>${escapeHtml(item.payer_name || "-")}</td>
    <td>${statusTag(item.reimbursement_status)}</td><td class="numeric amount-cell">${money(item.total_amount)}</td></tr>`).join("") || emptyRow(6, "暂无发票记录");
  requestAnimationFrame(() => { drawMonthly(data.monthly || []); drawDonut(data.categories || []); });
}

function prepareCanvas(canvas, height) {
  const rect = canvas.getBoundingClientRect(); const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, rect.width * ratio); canvas.height = Math.max(1, height * ratio);
  canvas.style.height = `${height}px`; const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); return { ctx, width: rect.width, height };
}

function drawMonthly(items) {
  const canvas = $("monthlyChart"); if (!canvas || !canvas.offsetParent) return;
  const { ctx, width, height } = prepareCanvas(canvas, 210); ctx.clearRect(0, 0, width, height);
  const pad = { l: 46, r: 16, t: 14, b: 30 }, values = items.map((item) => Number(item.amount || 0)); const max = Math.max(...values, 1);
  ctx.font = "9px Bahnschrift, sans-serif"; ctx.textAlign = "right"; ctx.fillStyle = "#53697d"; ctx.strokeStyle = "rgba(156,183,211,.09)"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const y = pad.t + (height - pad.t - pad.b) * i / 4; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke(); ctx.fillText(money(max * (4 - i) / 4).replace("¥", ""), pad.l - 8, y + 3); }
  if (!items.length) { ctx.textAlign = "center"; ctx.fillStyle = "#52687c"; ctx.fillText("暂无月度数据", width / 2, height / 2); return; }
  const xAt = (index) => pad.l + (width - pad.l - pad.r) * (items.length === 1 ? .5 : index / (items.length - 1));
  const yAt = (value) => pad.t + (height - pad.t - pad.b) * (1 - value / max);
  const gradient = ctx.createLinearGradient(0, pad.t, 0, height - pad.b); gradient.addColorStop(0, "rgba(39,211,255,.28)"); gradient.addColorStop(1, "rgba(39,211,255,0)");
  ctx.beginPath(); items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); index ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.lineTo(xAt(items.length - 1), height - pad.b); ctx.lineTo(xAt(0), height - pad.b); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath(); items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); index ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(); ctx.lineWidth = 2; ctx.stroke();
  items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fillStyle = "#07101a"; ctx.fill(); ctx.strokeStyle = "#27d3ff"; ctx.stroke(); ctx.fillStyle = "#52687c"; ctx.textAlign = "center"; ctx.fillText(item.month.slice(5), x, height - 10); });
}

function drawDonut(items) {
  const canvas = $("categoryChart"); if (!canvas || !canvas.offsetParent) return;
  const { ctx, width, height } = prepareCanvas(canvas, 180); ctx.clearRect(0, 0, width, height);
  const total = items.reduce((sum, item) => sum + Number(item.amount || 0), 0); const cx = width / 2, cy = height / 2, radius = Math.min(width, height) * .34;
  let angle = -Math.PI / 2;
  if (!total) { ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.strokeStyle = "rgba(255,255,255,.06)"; ctx.lineWidth = 20; ctx.stroke(); }
  items.slice(0, 7).forEach((item) => { const portion = item.amount / total * Math.PI * 2; ctx.beginPath(); ctx.arc(cx, cy, radius, angle, angle + portion - .02); ctx.strokeStyle = item.color; ctx.lineWidth = 20; ctx.stroke(); angle += portion; });
  ctx.fillStyle = "#71869a"; ctx.font = "8px Bahnschrift"; ctx.textAlign = "center"; ctx.fillText("TOTAL", cx, cy - 6); ctx.fillStyle = "#edf5fc"; ctx.font = "700 14px Bahnschrift"; ctx.fillText(money(total), cx, cy + 12);
  $("categoryLegend").innerHTML = items.slice(0, 6).map((item) => `<div><i style="background:${escapeHtml(item.color)}"></i><span>${escapeHtml(item.name)}</span><b>${money(item.amount)}</b></div>`).join("") || `<div><span>暂无分类数据</span></div>`;
}

function colorTag(label, color) { return `<span class="color-tag" style="--tag-color:${escapeHtml(color)};--tag-border:${escapeHtml(color)}44;--tag-bg:${escapeHtml(color)}0d"><i></i>${escapeHtml(label)}</span>`; }
function statusTag(status) { return `<span class="status-tag ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`; }
function emptyRow(columns, text) { return `<tr><td colspan="${columns}" class="empty-state">${escapeHtml(text)}</td></tr>`; }

async function loadInvoices() {
  const params = new URLSearchParams();
  const mappings = [["invoiceSearch", "search"], ["invoiceStatusFilter", "status"], ["invoiceCategoryFilter", "category_id"], ["invoiceSourceFilter", "source_id"]];
  mappings.forEach(([id, key]) => { if ($(id).value) params.set(key, $(id).value); });
  const data = await api(`/api/invoices?${params}`); state.invoices = data.items || []; renderInvoices();
}

function renderInvoices() {
  const sum = state.invoices.reduce((total, item) => total + Number(item.total_amount || 0), 0);
  $("invoiceCount").textContent = `${state.invoices.length} 条记录`; $("invoiceSum").textContent = `合计 ${money(sum)}`;
  $("invoiceTable").innerHTML = state.invoices.map((item) => {
    const attachment = item.attachment_id ? `<a class="row-action" href="/api/attachments/${encodeURIComponent(item.attachment_id)}/content" target="_blank" title="打开附件">↗</a>` : "";
    const actions = canWrite() ? `${attachment}<button class="row-action" data-invoice-action="edit" data-id="${escapeHtml(item.id)}" title="编辑">✎</button><button class="row-action delete" data-invoice-action="delete" data-id="${escapeHtml(item.id)}" title="删除">×</button>` : `${attachment}<button class="row-action" data-invoice-action="view" data-id="${escapeHtml(item.id)}" title="查看">⌕</button>`;
    const splitNames = item.splits.map((split) => split.member_name).join("、");
    const progress = item.total_amount > 0 ? Math.round(item.reimbursed_amount / item.total_amount * 100) : 0;
    return `<tr>
      <td><span class="cell-main">${escapeHtml(item.invoice_date)}</span><span class="cell-sub">${escapeHtml(item.invoice_no || "无发票号")}</span></td>
      <td><span class="cell-main">${escapeHtml(item.vendor || "待补充")}</span><span class="cell-sub">${escapeHtml(item.product_type)}${item.ocr_status === "queued" ? " · OCR排队中" : ""}</span></td>
      <td>${colorTag(item.category_name || "未分类", item.category_color || "#9aa7b7")}</td>
      <td><span class="cell-main">${escapeHtml(item.payer_name || "-")} 垫付</span><span class="cell-sub">${escapeHtml(burdenLabel(item.burden_type))} · ${escapeHtml(splitNames || "-")}</span></td>
      <td>${item.funding_source_name ? colorTag(item.funding_source_name, item.funding_source_color || "#9aa7b7") : "-"}</td>
      <td>${statusTag(item.reimbursement_status)}<span class="cell-sub">${progress}% · ${money(item.reimbursed_amount)}</span></td>
      <td class="numeric amount-cell">${money(item.total_amount)}</td><td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }).join("") || emptyRow(8, "没有符合条件的记录");
}

function renderSplitMembers(selected = [], weights = {}) {
  const burden = document.querySelector('input[name="burdenType"]:checked')?.value || "team_aa";
  const payer = $("invoicePayer").value;
  const active = state.members.filter((item) => item.active && !item.deleted_at);
  if (burden === "team_aa") selected = active.map((item) => item.id);
  if (burden === "self_paid") selected = [payer];
  $("splitMemberPicker").innerHTML = active.map((item) => {
    const checked = selected.includes(item.id); const disabled = burden !== "specified_split";
    return `<label class="split-member ${disabled ? "disabled" : ""}" style="--member-color:${escapeHtml(item.avatar_color)}">
      <input type="checkbox" value="${escapeHtml(item.id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <strong>${escapeHtml(item.name)}</strong><input class="split-weight" data-member-id="${escapeHtml(item.id)}" type="number" min="0.01" step="0.01" value="${escapeHtml(weights[item.id] || 1)}" title="分摊权重" ${$("splitMode").value !== "weighted" || !checked ? "disabled" : ""}>
    </label>`;
  }).join("");
}

function selectedSplitIds() { return $$("#splitMemberPicker input[type=checkbox]:checked").map((input) => input.value); }
function selectedWeights() { return Object.fromEntries($$("#splitMemberPicker .split-weight:not(:disabled)").map((input) => [input.dataset.memberId, Number(input.value || 1)])); }

function resetInvoiceForm() {
  $("invoiceForm").reset(); $("invoiceId").value = ""; $("invoiceVersion").value = ""; $("attachmentId").value = "";
  $("ocrText").value = ""; $("ocrConfidence").value = "0"; $("ocrStatus").value = "manual"; $("invoiceDate").value = nowDate();
  $("invoiceTax").value = "0"; $("reimbursedAmount").value = "0"; $("invoiceProduct").value = "其他";
  const preferredPayer = state.user.member_id && state.members.some((item) => item.id === state.user.member_id && item.active) ? state.user.member_id : state.members.find((item) => item.active)?.id;
  $("invoicePayer").value = preferredPayer || ""; $("fileState").textContent = "尚未选择附件"; $("runOcrBtn").disabled = true;
  $("ocrMessage").textContent = "电子 PDF 将优先直接读取文字"; $("ocrProgressBar").style.width = "0";
  $("classificationHint").textContent = ""; $("classificationHint").classList.add("hidden");
  document.querySelector('input[name="burdenType"][value="team_aa"]').checked = true; $("splitMode").value = "equal";
  renderSplitMembers(state.members.filter((item) => item.active).map((item) => item.id)); updateReimbursementStatus();
  $("invoiceDialogTitle").textContent = "新增发票记录"; setInvoiceReadonly(false);
}

function setInvoiceReadonly(readonly) {
  $$("#invoiceForm input, #invoiceForm select, #invoiceForm textarea").forEach((element) => {
    if (element.type !== "hidden") element.disabled = readonly;
  });
  $("saveInvoiceBtn").classList.toggle("hidden", readonly); $("runOcrBtn").classList.toggle("hidden", readonly);
}

function openNewInvoice() { resetInvoiceForm(); $("invoiceDialog").showModal(); }

async function openInvoice(id, readonly = false) {
  const item = await api(`/api/invoices/${encodeURIComponent(id)}`); resetInvoiceForm();
  $("invoiceId").value = item.id; $("invoiceVersion").value = item.version; $("attachmentId").value = item.attachment_id || "";
  $("ocrText").value = item.ocr_text || ""; $("ocrConfidence").value = item.ocr_confidence || 0; $("ocrStatus").value = item.ocr_status || "manual";
  $("invoiceDate").value = item.invoice_date; $("invoiceAmount").value = item.total_amount; $("invoiceTax").value = item.tax_amount;
  $("invoiceNo").value = item.invoice_no; $("invoiceVendor").value = item.vendor; $("invoiceProduct").value = item.product_type;
  $("invoiceCategory").value = item.category_id || ""; $("invoicePayer").value = item.payer_member_id || ""; $("invoiceSource").value = item.funding_source_id || "";
  $("reimbursedAmount").value = item.reimbursed_amount; $("reimbursementDate").value = item.reimbursement_date || ""; $("invoiceNote").value = item.note || "";
  const burdenRadio = document.querySelector(`input[name="burdenType"][value="${CSS.escape(item.burden_type)}"]`); if (burdenRadio) burdenRadio.checked = true;
  const weights = Object.fromEntries(item.splits.map((split) => [split.member_id, Math.max(.01, split.share_amount)]));
  renderSplitMembers(item.splits.map((split) => split.member_id), weights);
  if (item.attachment_id) { $("fileState").textContent = item.attachment_name || "已关联附件"; $("runOcrBtn").disabled = false; }
  $("invoiceDialogTitle").textContent = readonly ? "查看发票记录" : "编辑发票记录"; updateReimbursementStatus(); setInvoiceReadonly(readonly); $("invoiceDialog").showModal();
}

function invoiceFormPayload() {
  return {
    version: Number($("invoiceVersion").value || 0), invoice_date: $("invoiceDate").value,
    total_amount: $("invoiceAmount").value, tax_amount: $("invoiceTax").value,
    invoice_no: $("invoiceNo").value, vendor: $("invoiceVendor").value,
    product_type: $("invoiceProduct").value, category_id: $("invoiceCategory").value,
    payer_member_id: $("invoicePayer").value, funding_source_id: $("invoiceSource").value,
    burden_type: document.querySelector('input[name="burdenType"]:checked').value,
    split_mode: $("splitMode").value, split_member_ids: selectedSplitIds(), split_weights: selectedWeights(),
    reimbursed_amount: $("reimbursedAmount").value || 0, reimbursement_date: $("reimbursementDate").value,
    note: $("invoiceNote").value, attachment_id: $("attachmentId").value,
    ocr_text: $("ocrText").value, ocr_confidence: Number($("ocrConfidence").value || 0), ocr_status: $("ocrStatus").value,
  };
}

function updateReimbursementStatus() {
  const total = Number($("invoiceAmount").value || 0), reimbursed = Number($("reimbursedAmount").value || 0);
  const label = reimbursed <= 0 ? "未报销" : reimbursed >= total && total > 0 ? "已报销" : "部分报销";
  $("computedReimbursementStatus").textContent = label;
}

async function uploadInvoiceFile(file) {
  if (!file) return;
  const form = new FormData(); form.append("file", file);
  $("fileState").textContent = `正在保存 ${file.name}`; $("ocrProgressBar").style.width = "16%";
  try {
    const data = await api("/api/attachments", { method: "POST", body: form });
    $("attachmentId").value = data.attachment.id; $("fileState").textContent = data.attachment.original_name;
    $("runOcrBtn").disabled = false; $("ocrProgressBar").style.width = "28%"; toast("附件已安全保存");
    await runOcr();
  } catch (error) { $("fileState").textContent = "附件上传失败"; toast(error.message, "error"); }
}

async function runOcr() {
  const attachmentId = $("attachmentId").value; if (!attachmentId) return;
  $("runOcrBtn").disabled = true; $("ocrMessage").textContent = "离线 OCR 正在识别，可继续使用其他页面"; $("ocrProgressBar").style.width = "42%";
  try {
    const start = await api(`/api/ocr/${encodeURIComponent(attachmentId)}`, { method: "POST" });
    for (let attempt = 0; attempt < 900; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, attempt < 8 ? 900 : 1800));
      const job = await api(`/api/ocr/jobs/${encodeURIComponent(start.job_id)}`);
      $("ocrProgressBar").style.width = `${Math.min(92, 48 + attempt * 2)}%`;
      if (job.status === "done") { applyOcr(job.result || {}); $("ocrProgressBar").style.width = "100%"; $("ocrMessage").textContent = `识别完成 · 置信度 ${Math.round((job.result.ocr_confidence || 0) * 100)}%`; toast("离线 OCR 识别完成"); break; }
      if (job.status === "failed") throw new Error(job.error || "OCR 识别失败");
    }
  } catch (error) { $("ocrMessage").textContent = error.message; $("ocrProgressBar").style.width = "0"; toast(error.message, "error", 5000); }
  finally { $("runOcrBtn").disabled = false; }
}

function applyOcr(result) {
  if (result.invoice_date) $("invoiceDate").value = result.invoice_date;
  if (Number(result.total_amount) > 0) $("invoiceAmount").value = result.total_amount;
  if (Number(result.tax_amount) >= 0) $("invoiceTax").value = result.tax_amount;
  if (result.invoice_no) $("invoiceNo").value = result.invoice_no;
  if (result.vendor) $("invoiceVendor").value = result.vendor;
  if (result.product_type && state.productTypes.includes(result.product_type)) $("invoiceProduct").value = result.product_type;
  if (result.category_id && state.categories.some((item) => item.id === result.category_id && item.active)) $("invoiceCategory").value = result.category_id;
  $("ocrText").value = result.ocr_text || ""; $("ocrConfidence").value = result.ocr_confidence || 0; $("ocrStatus").value = result.ocr_status || "recognized";
  const classification = $("classificationHint");
  const categoryText = result.category_name ? `费用分类：${result.category_name}` : "费用分类：请人工选择";
  const confidenceText = result.classification_confidence ? ` · 分类可信度 ${Math.round(result.classification_confidence * 100)}%` : "";
  classification.textContent = `智能分类建议：${result.product_type || "其他"} · ${categoryText}${confidenceText} · ${result.classification_reason || "请人工确认"}`;
  classification.classList.remove("hidden");
  updateReimbursementStatus();
}

async function saveInvoiceForm(event) {
  event.preventDefault();
  if (!selectedSplitIds().length) return toast("请至少选择一名分摊成员", "error");
  loading(true, "正在保存发票记录");
  try {
    const id = $("invoiceId").value; await api(id ? `/api/invoices/${encodeURIComponent(id)}` : "/api/invoices", { method: id ? "PUT" : "POST", body: invoiceFormPayload() });
    $("invoiceDialog").close(); toast(id ? "发票记录已更新" : "发票记录已新增"); await refreshCurrentView(true);
  } catch (error) { toast(error.message, "error", 5000); }
  finally { loading(false); }
}

async function deleteInvoiceRecord(id) {
  const item = state.invoices.find((value) => value.id === id); if (!confirm(`确认删除“${item?.vendor || "该发票"}”？删除前会自动建立回溯版本。`)) return;
  try { await api(`/api/invoices/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("发票记录已删除"); await loadInvoices(); }
  catch (error) { toast(error.message, "error"); }
}

async function submitBatch(event) {
  event.preventDefault(); const file = $("batchFile").files[0]; if (!file) return;
  const form = new FormData(); form.append("file", file); form.append("category_id", $("batchCategory").value); form.append("payer_member_id", $("batchPayer").value);
  form.append("funding_source_id", $("batchSource").value); form.append("burden_type", $("batchBurden").value); form.append("note", $("batchNote").value);
  form.append("split_member_ids", JSON.stringify(state.members.filter((item) => item.active).map((item) => item.id)));
  loading(true, "正在解压并建立 OCR 队列");
  try {
    const result = await api("/api/import/zip", { method: "POST", body: form });
    $("batchResult").textContent = `已导入 ${result.count} 个文件并建立草稿；${result.skipped.length} 个文件被跳过。OCR 将在后台依次完成。`;
    toast(`批量导入完成：${result.count} 条记录`); await loadInvoices();
    if (result.jobs.length) pollBatchJobs(result.jobs.map((item) => item.job_id));
  } catch (error) { $("batchResult").textContent = error.message; toast(error.message, "error", 5000); }
  finally { loading(false); }
}

async function pollBatchJobs(jobIds) {
  let remaining = [...jobIds];
  for (let round = 0; round < 900 && remaining.length; round++) {
    await new Promise((resolve) => setTimeout(resolve, 1800));
    const results = await Promise.all(remaining.map((id) => api(`/api/ocr/jobs/${encodeURIComponent(id)}`).catch(() => ({ status: "failed" }))));
    remaining = remaining.filter((_, index) => !["done", "failed"].includes(results[index].status));
    $("batchResult").textContent = `OCR 处理中：已完成 ${jobIds.length - remaining.length}/${jobIds.length}`;
  }
  $("batchResult").textContent = `OCR 队列完成，共处理 ${jobIds.length} 个文件。请检查低置信度记录。`;
  if (state.currentView === "invoices") await loadInvoices();
}

async function loadSettlements() {
  const data = await api("/api/settlements/summary"); state.settlements = data; renderSettlements();
}

function renderSettlements() {
  const data = state.settlements || { members: [], recommendations: [], history: [], outstanding_amount: 0 };
  $("settlementOutstanding").textContent = money(data.outstanding_amount);
  $("memberBalanceGrid").innerHTML = data.members.map((item) => `<article class="balance-card" style="--member-color:${escapeHtml(item.avatar_color)}"><header><span class="member-avatar">${escapeHtml(item.name.slice(0, 1))}</span><div><h4>${escapeHtml(item.name)}</h4><small>${escapeHtml(item.department || "未分组")}</small></div></header><div class="balance-amounts"><div><span>实际垫付</span><b>${money(item.paid_out)}</b></div><div><span>应承担</span><b>${money(item.owed)}</b></div></div><div class="net-balance"><span>净额</span><strong class="${item.balance < 0 ? "negative" : ""}">${item.balance >= 0 ? "+" : ""}${money(item.balance)}</strong></div></article>`).join("") || `<div class="empty-state">暂无成员结算数据</div>`;
  $("transferRecommendations").innerHTML = data.recommendations.map((item) => `<div class="transfer-item"><div><b>${escapeHtml(item.from_name)}</b><small>付款</small></div><i>→</i><div class="to"><b>${escapeHtml(item.to_name)}</b><small>收款</small></div><button class="btn secondary write-only" data-transfer-from="${escapeHtml(item.from_member_id)}" data-transfer-to="${escapeHtml(item.to_member_id)}" data-transfer-amount="${item.amount}">${money(item.amount)}</button></div>`).join("") || `<div class="empty-state">当前账目已结清</div>`;
  $("settlementHistory").innerHTML = data.history.map((item) => `<div class="compact-item"><div><b>${escapeHtml(item.from_name)} → ${escapeHtml(item.to_name)}</b><small>${escapeHtml(dateText(item.settled_at))} · ${escapeHtml(item.note || "无备注")}</small></div><strong>${money(item.amount)}</strong>${canWrite() ? `<button class="row-action delete" data-settlement-delete="${escapeHtml(item.id)}">×</button>` : ""}</div>`).join("") || `<div class="empty-state">暂无还款记录</div>`;
  applyAccess();
}

function openSettlement(from = "", to = "", amount = "") {
  $("settlementForm").reset(); $("settlementDate").value = nowDate();
  if (from) $("settlementFrom").value = from; if (to) $("settlementTo").value = to; if (amount) $("settlementAmount").value = amount;
  $("settlementDialog").showModal();
}

async function saveSettlement(event) {
  event.preventDefault();
  try {
    await api("/api/settlements", { method: "POST", body: { from_member_id: $("settlementFrom").value, to_member_id: $("settlementTo").value, amount: $("settlementAmount").value, settled_at: $("settlementDate").value, status: "paid", note: $("settlementNote").value } });
    $("settlementDialog").close(); toast("还款记录已登记"); await loadSettlements();
  } catch (error) { toast(error.message, "error"); }
}

async function deleteSettlement(id) {
  if (!confirm("确认删除这条还款记录？")) return;
  try { await api(`/api/settlements/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("还款记录已删除"); await loadSettlements(); }
  catch (error) { toast(error.message, "error"); }
}

async function loadReports() {
  const params = new URLSearchParams(); if ($("reportFrom").value) params.set("date_from", $("reportFrom").value); if ($("reportTo").value) params.set("date_to", $("reportTo").value);
  const data = await api(`/api/reports/summary?${params}`); renderReportBars("categoryReport", data.categories, "total", "count"); renderReportBars("sourceReport", data.sources, "total", "count"); renderReportBars("payerReport", data.payers, "total", "count");
}

function renderReportBars(id, items, valueKey, countKey) {
  const max = Math.max(...items.map((item) => Number(item[valueKey] || 0)), 1);
  $(id).innerHTML = items.map((item) => `<div class="report-row"><span title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><div class="bar"><i style="--bar-width:${Math.max(1, Number(item[valueKey]) / max * 100)}%;--bar-color:${escapeHtml(item.color || "#27d3ff")}"></i></div><b>${money(item[valueKey])}<small>${item[countKey] || 0} 笔</small></b></div>`).join("") || `<div class="empty-state">该时间段暂无数据</div>`;
}

async function loadMembersAndUsers() {
  renderMemberCards();
  if (isAdmin()) { const result = await api("/api/admin/users"); state.users = result.items || []; renderUsers(); }
}

function renderMemberCards() {
  $("memberCards").innerHTML = state.members.filter((item) => !item.deleted_at).map((item) => `<article class="member-card ${item.active ? "" : "inactive"}" style="--member-color:${escapeHtml(item.avatar_color)}"><header><span class="member-avatar">${escapeHtml(item.name.slice(0, 1))}</span><div><h4>${escapeHtml(item.name)}</h4><small>${escapeHtml(item.department || "未分组")}</small></div></header><p>${item.student_id ? `学号 ${escapeHtml(item.student_id)}<br>` : ""}${item.phone ? `电话 ${escapeHtml(item.phone)}<br>` : ""}${item.active ? "有效成员" : "已停用，历史数据保留"}</p>${isAdmin() ? `<footer><button class="row-action" data-member-edit="${escapeHtml(item.id)}">✎</button>${item.active ? `<button class="row-action delete" data-member-archive="${escapeHtml(item.id)}">×</button>` : ""}</footer>` : ""}</article>`).join("");
}

function renderUsers() {
  $("userTable").innerHTML = state.users.map((item) => `<tr><td><span class="cell-main">${escapeHtml(item.username)}</span></td><td>${escapeHtml(item.display_name)}</td><td>${escapeHtml(item.member_name || "-")}</td><td>${escapeHtml(roleLabel(item.role))}</td><td>${item.active ? `<span class="status-tag reimbursed">启用</span>` : `<span class="status-tag pending">停用</span>`}</td><td><button class="row-action" data-user-edit="${escapeHtml(item.id)}">✎</button></td></tr>`).join("") || emptyRow(6, "暂无账号");
}

function openMember(item = null) {
  $("memberForm").reset(); $("memberId").value = item?.id || ""; $("memberName").value = item?.name || ""; $("memberDepartment").value = item?.department || "";
  $("memberStudentId").value = item?.student_id || ""; $("memberPhone").value = item?.phone || ""; $("memberEmail").value = item?.email || ""; $("memberColor").value = item?.avatar_color || "#27d3ff";
  $("memberDialogTitle").textContent = item ? "编辑成员" : "新增成员"; $("memberDialog").showModal();
}

async function saveMember(event) {
  event.preventDefault(); const id = $("memberId").value;
  const payload = { name: $("memberName").value, department: $("memberDepartment").value, student_id: $("memberStudentId").value, phone: $("memberPhone").value, email: $("memberEmail").value, avatar_color: $("memberColor").value, active: true };
  try { await api(id ? `/api/members/${encodeURIComponent(id)}` : "/api/members", { method: id ? "PUT" : "POST", body: payload }); $("memberDialog").close(); toast("成员信息已保存"); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
}

async function archiveMember(id) {
  const item = state.members.find((value) => value.id === id); if (!confirm(`确认停用成员“${item?.name}”？关联账号也会停用，历史账目仍保留。`)) return;
  try { await api(`/api/members/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("成员已停用"); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
}

function openUser(item = null) {
  $("userForm").reset(); $("editUserId").value = item?.id || ""; $("editUsername").value = item?.username || ""; $("editDisplayName").value = item?.display_name || "";
  $("editUserMember").value = item?.member_id || ""; $("editUserRole").value = item?.role || "member"; $("editUserActive").checked = item ? Boolean(item.active) : true;
  $("editUserPassword").value = ""; $("userDialogTitle").textContent = item ? "编辑账号" : "新增账号"; $("userDialog").showModal();
}

async function saveUser(event) {
  event.preventDefault(); const id = $("editUserId").value;
  const payload = { username: $("editUsername").value, display_name: $("editDisplayName").value, member_id: $("editUserMember").value, role: $("editUserRole").value, active: $("editUserActive").checked };
  if ($("editUserPassword").value) payload.password = $("editUserPassword").value;
  try { await api(id ? `/api/admin/users/${encodeURIComponent(id)}` : "/api/admin/users", { method: id ? "PUT" : "POST", body: payload }); $("userDialog").close(); toast("账号已保存"); await loadMembersAndUsers(); }
  catch (error) { toast(error.message, "error"); }
}

async function loadHistory() {
  const [snapshots, logs] = await Promise.all([api("/api/admin/snapshots"), api("/api/audit-logs?limit=300")]);
  $("snapshotList").innerHTML = snapshots.items.map((item) => `<article class="version-item"><h4>${escapeHtml(item.label)}</h4><p>${escapeHtml(item.reason || "自动保护点")}<br><time>${escapeHtml(item.created_at.replace("T", " ").slice(0, 19))} · ${escapeHtml(item.created_by_name || "系统")}</time></p><button class="btn secondary" data-snapshot-restore="${escapeHtml(item.id)}">回溯</button></article>`).join("") || `<div class="empty-state">暂无历史版本</div>`;
  $("auditList").innerHTML = logs.items.map((item) => `<article class="audit-item"><h4>${escapeHtml(actionLabel(item.action))} · ${escapeHtml(item.entity_type)}</h4><p>${escapeHtml(item.user_name || "系统任务")} · ${escapeHtml(item.entity_id || "全局")}<br><time>${escapeHtml(item.created_at.replace("T", " ").slice(0, 19))}</time></p></article>`).join("") || `<div class="empty-state">暂无操作日志</div>`;
}

async function restoreSnapshot(id) {
  if (!confirm("确认回溯到该版本？当前状态会先自动建立保护点，登录账号不会被回退。")) return;
  loading(true, "正在恢复历史版本");
  try { const result = await api(`/api/admin/snapshots/${encodeURIComponent(id)}/restore`, { method: "POST" }); toast(result.message); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
  finally { loading(false); }
}

async function createSnapshotManually() {
  const label = prompt("请输入版本名称", `手动版本 ${new Date().toLocaleString("zh-CN")}`); if (!label) return;
  try { await api("/api/admin/snapshots", { method: "POST", body: { label, reason: "管理员手动建立" } }); toast("版本已建立"); await loadHistory(); }
  catch (error) { toast(error.message, "error"); }
}

function openCredentials() {
  $("credentialsForm").reset(); $("newUsername").value = state.user.username; $("credentialsDialog").showModal();
}

async function saveCredentials(event) {
  event.preventDefault();
  try {
    const result = await api("/api/auth/change-credentials", { method: "POST", body: { current_password: $("currentPassword").value, username: $("newUsername").value, password: $("newPassword").value } });
    state.user = result.user; applyAccess(); $("credentialsDialog").close(); toast("登录信息已修改，请妥善保存新密码");
  } catch (error) { toast(error.message, "error", 5000); }
}

function appearanceMediaUrl(media) { return media?.private_url || media?.url || ""; }

function mediaPreviewHtml(media, muted = true) {
  const url = escapeHtml(appearanceMediaUrl(media));
  if (!url) return "";
  return media.kind === "video" ? `<video src="${url}" ${muted ? "muted" : ""} loop autoplay playsinline></video>` : `<img src="${url}" alt="${escapeHtml(media.title || "界面媒体")}">`;
}

function renderBackgroundPreview() {
  const media = state.appearanceBackground;
  $("backgroundPreview").classList.toggle("empty", !media);
  $("backgroundPreview").innerHTML = media ? mediaPreviewHtml(media) : "当前使用默认赛车背景";
}

function renderLoginSlideEditor() {
  $("loginSlideEditor").innerHTML = state.appearanceSlides.map((slide, index) => `<article class="login-slide-item" data-slide-index="${index}">
    <div class="login-slide-thumb">${mediaPreviewHtml(slide)}</div>
    <input data-slide-title="${index}" value="${escapeHtml(slide.title || "")}" maxlength="160" aria-label="轮播标题">
    <label class="slide-duration"><input data-slide-duration="${index}" type="number" min="2" max="600" value="${Number(slide.duration || 8)}"><span>秒</span></label>
    <div class="slide-actions"><button type="button" data-slide-move="up" data-index="${index}" title="上移">↑</button><button type="button" data-slide-move="down" data-index="${index}" title="下移">↓</button><button type="button" data-slide-remove="${index}" title="移除">×</button></div>
  </article>`).join("") || `<div class="empty-state">尚未添加登录轮播媒体</div>`;
}

function openAppearance() {
  $("appearanceForm").reset(); $("teamNameSetting").value = state.settings.team_name || "燕翔车队 Racing Team";
  $("backgroundOverlay").value = Math.round(Number(state.settings.background_overlay || .82) * 100); $("overlayValue").textContent = `${$("backgroundOverlay").value}%`;
  $("accentColor").value = state.settings.accent_color || "#27d3ff";
  $("loginSlideshowEnabled").checked = Boolean(state.settings.login_slideshow_enabled);
  $("loginTransition").value = state.settings.login_transition || "fade";
  state.appearanceSlides = (state.settings.login_slides || []).map((slide) => ({ ...slide }));
  state.appearanceBackground = state.settings.background_media_id ? {
    attachment_id: state.settings.background_media_id,
    kind: state.settings.background_media_kind || "image",
    title: "当前系统背景",
    url: state.settings.background_media_url,
    private_url: state.settings.background_media_url,
  } : null;
  renderBackgroundPreview(); renderLoginSlideEditor(); $("appearanceDialog").showModal();
  if (!state.wallpapers.length) scanWallpapers(false);
}

async function uploadAppearanceMedia(file) {
  const form = new FormData(); form.append("file", file);
  const result = await api("/api/admin/appearance/media", { method: "POST", body: form });
  return result.media;
}

async function chooseBackgroundFile(file) {
  if (!file) return;
  loading(true, "正在导入自定义背景");
  try { state.appearanceBackground = await uploadAppearanceMedia(file); renderBackgroundPreview(); toast("背景已导入，保存设置后生效"); }
  catch (error) { toast(error.message, "error"); }
  finally { loading(false); $("backgroundFile").value = ""; }
}

async function addLoginMediaFiles(files) {
  const list = [...(files || [])]; if (!list.length) return;
  loading(true, `正在导入 ${list.length} 个轮播媒体`);
  try {
    const media = await Promise.all(list.map((file) => uploadAppearanceMedia(file)));
    state.appearanceSlides.push(...media); renderLoginSlideEditor(); toast(`已添加 ${media.length} 个轮播媒体`);
  } catch (error) { toast(error.message, "error", 5000); }
  finally { loading(false); $("loginMediaFiles").value = ""; }
}

async function scanWallpapers(force = true) {
  $("wallpaperStatus").textContent = "正在扫描 Steam 与 Wallpaper Engine 壁纸库……";
  try {
    const result = await api(`/api/admin/wallpaper-engine?refresh=${force ? "true" : "false"}`);
    state.wallpapers = result.items || []; $("wallpaperStatus").textContent = `${result.message}，共 ${result.count || 0} 项。`;
    $("wallpaperGrid").innerHTML = state.wallpapers.map((item) => `<article class="wallpaper-card"><figure><img loading="lazy" src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.title)}"></figure><div class="wallpaper-card-body"><b title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</b><small>${escapeHtml(item.type_label)} · ${escapeHtml(item.import_note)}</small><div class="wallpaper-actions"><button type="button" class="btn secondary" data-wallpaper-background="${escapeHtml(item.id)}">设为背景</button><button type="button" class="btn secondary" data-wallpaper-login="${escapeHtml(item.id)}">加入轮播</button></div></div></article>`).join("") || `<div class="empty-state">未发现可导入壁纸。请确认 Steam 已下载 Wallpaper Engine 内容。</div>`;
  } catch (error) { $("wallpaperStatus").textContent = error.message; toast(error.message, "error"); }
}

async function importWallpaper(id, target) {
  loading(true, "正在从 Wallpaper Engine 导入壁纸");
  try {
    const result = await api(`/api/admin/wallpaper-engine/${encodeURIComponent(id)}/import`, { method: "POST" });
    if (target === "background") { state.appearanceBackground = result.media; renderBackgroundPreview(); }
    else { state.appearanceSlides.push(result.media); renderLoginSlideEditor(); }
    toast(result.media.uses_preview ? "已导入该壁纸的预览图" : "Wallpaper Engine 壁纸已导入");
  } catch (error) { toast(error.message, "error", 5000); }
  finally { loading(false); }
}

async function saveAppearance(event) {
  event.preventDefault(); loading(true, "正在更新系统界面");
  try {
    const result = await api("/api/admin/settings", { method: "PUT", body: {
      team_name: $("teamNameSetting").value,
      background_media_id: state.appearanceBackground?.attachment_id || "",
      background_overlay: Number($("backgroundOverlay").value) / 100,
      accent_color: $("accentColor").value,
      login_slideshow_enabled: $("loginSlideshowEnabled").checked,
      login_transition: $("loginTransition").value,
      login_slides: state.appearanceSlides.map((slide) => ({ id: slide.id, attachment_id: slide.attachment_id, title: slide.title, duration: Number(slide.duration || 8) })),
    } });
    state.settings = result.settings; state.publicSettings = result.settings; applyTheme(); applyPublicAppearance(result.settings); $("appearanceDialog").close(); toast("系统界面与登录轮播已更新");
  } catch (error) { toast(error.message, "error"); }
  finally { loading(false); }
}

function resetClassificationEditor() {
  $("classificationRuleForm").reset(); $("classificationRuleId").value = ""; $("classificationRulePriority").value = "100"; $("classificationRuleActive").checked = true; $("classificationRuleSaveBtn").textContent = "添加规则";
}

function renderClassificationRules() {
  const categoryName = (id) => state.categories.find((item) => item.id === id)?.name || "未分类";
  $("classificationRuleList").innerHTML = state.classificationRules.map((rule) => `<article class="classification-rule-item${rule.active ? "" : " inactive"}"><div><b>${escapeHtml(rule.name)}</b><p>${escapeHtml(rule.keywords.join("、"))}<br>${escapeHtml(rule.product_type)} → ${escapeHtml(categoryName(rule.category_id))} · 优先级 ${Number(rule.priority || 100)}</p></div><div class="rule-buttons"><button type="button" class="btn secondary" data-rule-edit="${escapeHtml(rule.id)}">编辑</button><button type="button" class="btn danger subtle" data-rule-delete="${escapeHtml(rule.id)}">删除</button></div></article>`).join("") || `<div class="empty-state">当前没有启用的智能分类规则</div>`;
}

async function openClassification() {
  $("classificationRuleCategory").innerHTML = `<option value="">仅填写产品类型</option>${state.categories.filter((item) => !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  $("classificationRuleProduct").innerHTML = state.productTypes.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  try { const result = await api("/api/admin/classification-rules"); state.classificationRules = result.items || []; resetClassificationEditor(); renderClassificationRules(); $("classificationDialog").showModal(); }
  catch (error) { toast(error.message, "error"); }
}

async function persistClassificationRules(message) {
  const result = await api("/api/admin/classification-rules", { method: "PUT", body: { items: state.classificationRules } });
  state.classificationRules = result.items || []; renderClassificationRules(); toast(message);
}

async function saveClassificationRule(event) {
  event.preventDefault();
  const id = $("classificationRuleId").value || `rule_custom_${Date.now().toString(36)}`;
  const rule = {
    id, name: $("classificationRuleName").value.trim(),
    keywords: $("classificationRuleKeywords").value.split(/[,，;；\n]/).map((item) => item.trim()).filter(Boolean),
    category_id: $("classificationRuleCategory").value, product_type: $("classificationRuleProduct").value,
    priority: Number($("classificationRulePriority").value || 100), active: $("classificationRuleActive").checked,
  };
  if (!rule.keywords.length) return toast("请至少填写一个关键词", "error");
  const index = state.classificationRules.findIndex((item) => item.id === id);
  if (index >= 0) state.classificationRules[index] = rule; else state.classificationRules.push(rule);
  try { await persistClassificationRules(index >= 0 ? "智能分类规则已更新" : "智能分类规则已添加"); resetClassificationEditor(); }
  catch (error) { toast(error.message, "error"); }
}

function editClassificationRule(id) {
  const rule = state.classificationRules.find((item) => item.id === id); if (!rule) return;
  $("classificationRuleId").value = rule.id; $("classificationRuleName").value = rule.name; $("classificationRuleKeywords").value = rule.keywords.join("，");
  $("classificationRuleCategory").value = rule.category_id || ""; $("classificationRuleProduct").value = rule.product_type || "其他"; $("classificationRulePriority").value = rule.priority || 100; $("classificationRuleActive").checked = Boolean(rule.active); $("classificationRuleSaveBtn").textContent = "保存修改";
}

async function deleteClassificationRule(id) {
  const rule = state.classificationRules.find((item) => item.id === id); if (!rule || !confirm(`确认删除规则“${rule.name}”？`)) return;
  state.classificationRules = state.classificationRules.filter((item) => item.id !== id);
  try { await persistClassificationRules("智能分类规则已删除"); resetClassificationEditor(); } catch (error) { toast(error.message, "error"); }
}

async function resetClassificationRules() {
  if (!confirm("确认恢复系统默认智能分类规则？自定义规则将被替换。")) return;
  try { const result = await api("/api/admin/classification-rules", { method: "PUT", body: { reset: true } }); state.classificationRules = result.items || []; renderClassificationRules(); resetClassificationEditor(); toast("已恢复默认智能分类规则"); }
  catch (error) { toast(error.message, "error"); }
}

async function openSync() {
  try {
    const config = await api("/api/admin/sync"); state.sync = config; $("syncEnabled").checked = config.enabled; $("remoteUrl").value = config.remote_url || ""; $("syncSecret").value = "";
    $("syncDialogStatus").textContent = config.last_error ? `最近错误：${config.last_error}` : config.last_pull_at ? `最近同步：${config.last_pull_at.replace("T", " ").slice(0, 19)} · 待上传 ${config.pending_events} 项` : "尚未执行云端同步";
    $("syncDialog").showModal();
  } catch (error) { toast(error.message, "error"); }
}

async function saveSync(event) {
  event.preventDefault();
  try {
    state.sync = await api("/api/admin/sync", { method: "PUT", body: { enabled: $("syncEnabled").checked, remote_url: $("remoteUrl").value, secret: $("syncSecret").value } });
    renderSync(state.sync); $("syncDialog").close(); toast("同步配置已保存");
  } catch (error) { toast(error.message, "error", 5000); }
}

async function syncNow() {
  loading(true, "正在与云端双向同步");
  try { const result = await api("/api/admin/sync/run", { method: "POST" }); toast(`同步完成：上传 ${result.pushed || 0}，接收 ${result.pulled || 0}`); await refreshCurrentView(true); await openSync(); }
  catch (error) { toast(error.message, "error", 6000); $("syncDialogStatus").textContent = error.message; }
  finally { loading(false); }
}

async function restoreBackup(file) {
  if (!file || !confirm("恢复完整备份会替换当前账号、经费数据和设置。系统会先自动保存恢复前备份，是否继续？")) return;
  const form = new FormData(); form.append("file", file); loading(true, "正在验证并恢复完整备份");
  try { const result = await api("/api/admin/restore-backup", { method: "POST", body: form }); toast(result.message); setTimeout(() => location.reload(), 1000); }
  catch (error) { toast(error.message, "error", 6000); }
  finally { loading(false); $("restoreBackupInput").value = ""; }
}

async function deleteDemo() {
  if (!confirm("仅删除初始演示发票与演示还款，不影响正式数据。确认继续？")) return;
  try { const result = await api("/api/admin/demo-data", { method: "DELETE" }); toast(`已清除 ${result.deleted_count} 条演示记录`); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
}

function setupEvents() {
  $("toggleLoginPassword").addEventListener("click", () => {
    const visible = $("loginPassword").type === "text"; $("loginPassword").type = visible ? "password" : "text";
    $("toggleLoginPassword").textContent = visible ? "显示" : "隐藏"; $("toggleLoginPassword").setAttribute("aria-pressed", String(!visible)); $("toggleLoginPassword").setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
  });
  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault(); $("loginError").textContent = "";
    try { const result = await api("/api/auth/login", { method: "POST", body: { username: $("loginUsername").value, password: $("loginPassword").value } }); state.csrf = result.csrf_token; state.user = result.user; await loadBootstrap(); }
    catch (error) { $("loginError").textContent = error.message; }
  });
  $("logoutBtn").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {} showLogin(); });
  $("userMenuBtn").addEventListener("click", openCredentials); $("changeCredentialsBtn").addEventListener("click", openCredentials);
  $("credentialsForm").addEventListener("submit", saveCredentials);
  $("mainNav").addEventListener("click", (event) => { const button = event.target.closest("button[data-view]"); if (button) navigate(button.dataset.view); });
  $$('[data-goto]').forEach((button) => button.addEventListener("click", () => navigate(button.dataset.goto)));
  $("mobileMenuBtn").addEventListener("click", () => $("mainNav").closest(".sidebar").classList.toggle("open"));
  $("quickAddBtn").addEventListener("click", openNewInvoice); $("invoiceAddBtn").addEventListener("click", openNewInvoice);
  $("invoiceFilterBtn").addEventListener("click", loadInvoices); $("invoiceSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") loadInvoices(); });
  $("invoiceTable").addEventListener("click", (event) => { const button = event.target.closest("[data-invoice-action]"); if (!button) return; if (button.dataset.invoiceAction === "delete") deleteInvoiceRecord(button.dataset.id); else openInvoice(button.dataset.id, button.dataset.invoiceAction === "view").catch((error) => toast(error.message, "error")); });
  $("invoiceForm").addEventListener("submit", saveInvoiceForm); $("invoiceFile").addEventListener("change", (event) => uploadInvoiceFile(event.target.files[0])); $("runOcrBtn").addEventListener("click", runOcr);
  $("invoiceAmount").addEventListener("input", updateReimbursementStatus); $("reimbursedAmount").addEventListener("input", updateReimbursementStatus);
  $("invoicePayer").addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights()));
  $$('input[name="burdenType"]').forEach((input) => input.addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights())));
  $("splitMode").addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights()));
  $("splitMemberPicker").addEventListener("change", (event) => { if (event.target.type === "checkbox") { const weight = event.target.closest(".split-member").querySelector(".split-weight"); weight.disabled = $("splitMode").value !== "weighted" || !event.target.checked; } });
  $("batchImportBtn").addEventListener("click", () => { $("batchForm").reset(); $("batchResult").textContent = ""; renderReferenceOptions(); $("batchDialog").showModal(); }); $("batchForm").addEventListener("submit", submitBatch);
  $("recordSettlementBtn").addEventListener("click", () => openSettlement()); $("settlementForm").addEventListener("submit", saveSettlement);
  $("transferRecommendations").addEventListener("click", (event) => { const button = event.target.closest("[data-transfer-from]"); if (button) openSettlement(button.dataset.transferFrom, button.dataset.transferTo, button.dataset.transferAmount); });
  $("settlementHistory").addEventListener("click", (event) => { const button = event.target.closest("[data-settlement-delete]"); if (button) deleteSettlement(button.dataset.settlementDelete); });
  $("reportRunBtn").addEventListener("click", loadReports);
  $("addMemberBtn").addEventListener("click", () => openMember()); $("memberForm").addEventListener("submit", saveMember);
  $("memberCards").addEventListener("click", (event) => { const edit = event.target.closest("[data-member-edit]"), archive = event.target.closest("[data-member-archive]"); if (edit) openMember(state.members.find((item) => item.id === edit.dataset.memberEdit)); if (archive) archiveMember(archive.dataset.memberArchive); });
  $("addUserBtn").addEventListener("click", () => openUser()); $("userForm").addEventListener("submit", saveUser);
  $("userTable").addEventListener("click", (event) => { const button = event.target.closest("[data-user-edit]"); if (button) openUser(state.users.find((item) => item.id === button.dataset.userEdit)); });
  $("createSnapshotBtn").addEventListener("click", createSnapshotManually); $("snapshotList").addEventListener("click", (event) => { const button = event.target.closest("[data-snapshot-restore]"); if (button) restoreSnapshot(button.dataset.snapshotRestore); });
  $("openReferencesBtn").addEventListener("click", openReferences); $("categoryForm").addEventListener("submit", saveCategory); $("sourceForm").addEventListener("submit", saveSource);
  $("resetCategoryBtn").addEventListener("click", resetCategoryEditor); $("resetSourceBtn").addEventListener("click", resetSourceEditor);
  $("categoryManagerList").addEventListener("click", (event) => { const button = event.target.closest("[data-category-edit]"); if (button) editCategory(state.categories.find((item) => item.id === button.dataset.categoryEdit)); });
  $("sourceManagerList").addEventListener("click", (event) => { const button = event.target.closest("[data-source-edit]"); if (button) editSource(state.fundingSources.find((item) => item.id === button.dataset.sourceEdit)); });
  $("openAppearanceBtn").addEventListener("click", openAppearance); $("appearanceForm").addEventListener("submit", saveAppearance); $("backgroundOverlay").addEventListener("input", () => $("overlayValue").textContent = `${$("backgroundOverlay").value}%`);
  $("backgroundFile").addEventListener("change", (event) => chooseBackgroundFile(event.target.files[0])); $("loginMediaFiles").addEventListener("change", (event) => addLoginMediaFiles(event.target.files));
  $("clearBackgroundBtn").addEventListener("click", () => { state.appearanceBackground = null; renderBackgroundPreview(); }); $("scanWallpapersBtn").addEventListener("click", () => scanWallpapers(true));
  $("wallpaperGrid").addEventListener("click", (event) => { const background = event.target.closest("[data-wallpaper-background]"), login = event.target.closest("[data-wallpaper-login]"); if (background) importWallpaper(background.dataset.wallpaperBackground, "background"); if (login) importWallpaper(login.dataset.wallpaperLogin, "login"); });
  $("loginSlideEditor").addEventListener("input", (event) => { const titleIndex = event.target.dataset.slideTitle, durationIndex = event.target.dataset.slideDuration; if (titleIndex !== undefined && state.appearanceSlides[Number(titleIndex)]) state.appearanceSlides[Number(titleIndex)].title = event.target.value; if (durationIndex !== undefined && state.appearanceSlides[Number(durationIndex)]) state.appearanceSlides[Number(durationIndex)].duration = Math.max(2, Math.min(600, Number(event.target.value || 8))); });
  $("loginSlideEditor").addEventListener("click", (event) => { const remove = event.target.closest("[data-slide-remove]"), move = event.target.closest("[data-slide-move]"); if (remove) state.appearanceSlides.splice(Number(remove.dataset.slideRemove), 1); if (move) { const index = Number(move.dataset.index), target = move.dataset.slideMove === "up" ? index - 1 : index + 1; if (target >= 0 && target < state.appearanceSlides.length) [state.appearanceSlides[index], state.appearanceSlides[target]] = [state.appearanceSlides[target], state.appearanceSlides[index]]; } if (remove || move) renderLoginSlideEditor(); });
  $("openClassificationBtn").addEventListener("click", openClassification); $("classificationRuleForm").addEventListener("submit", saveClassificationRule); $("newRuleBtn").addEventListener("click", resetClassificationEditor); $("resetRulesBtn").addEventListener("click", resetClassificationRules);
  $("classificationRuleList").addEventListener("click", (event) => { const edit = event.target.closest("[data-rule-edit]"), remove = event.target.closest("[data-rule-delete]"); if (edit) editClassificationRule(edit.dataset.ruleEdit); if (remove) deleteClassificationRule(remove.dataset.ruleDelete); });
  $("openSyncBtn").addEventListener("click", openSync); $("syncForm").addEventListener("submit", saveSync); $("syncNowBtn").addEventListener("click", syncNow);
  $("restoreBackupInput").addEventListener("change", (event) => restoreBackup(event.target.files[0])); $("deleteDemoBtn").addEventListener("click", deleteDemo);
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
  $$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
  window.addEventListener("resize", () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(() => { if (state.currentView === "dashboard") renderDashboard(); }, 160); });
}

async function initialize() {
  setupEvents();
  await loadPublicAppearance();
  try { const current = await api("/api/auth/me"); state.csrf = current.csrf_token; state.user = current.user; await loadBootstrap(); }
  catch (_) { showLogin(); }
}

initialize();
