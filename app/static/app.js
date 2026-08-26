"use strict";

const state = {
  csrf: "", user: null, members: [], categories: [], fundingSources: [], productTypes: [],
  season: null, seasons: [], departments: [], creators: [],
  settings: {}, dashboard: null, invoices: [], users: [], currentView: "dashboard", socket: null,
  resizeTimer: null, publicSettings: {}, loginSlideTimer: null, loginSlideIndex: 0,
  loginActiveLayer: "A", appearanceSlides: [], appearanceBackground: null,
  appearanceLoadingCars: [], selectedInvoiceIds: new Set(),
  wallpapers: [], classificationRules: [], decisionResolve: null,
  shortcuts: {}, shortcutDraft: {},
  loadingTimer: null, loadingProgress: 0, loadingToken: 0, updateRelease: null,
  currentInvoicePreview: null, batchPreviewFiles: [], attachmentViewerZoom: 1,
  version: "2.3.1", updateJobId: "", qualityIssues: [], openSourceReferences: [],
  supportingAttachments: [], batchReviewIds: [], userPreferences: {}, personalAudio: [], audioIndex: -1,
  draftHistory: [], draftHistoryIndex: -1, draftHistoryTimer: null, activeDraftForm: null,
  lastOcrResult: null,
};

const DISPLAY_MODE_KEY = "yanxiang-display-mode";
const DISPLAY_MODES = new Set(["clear-dark", "light", "racing-blue", "graphite", "midnight", "teal", "warm"]);
const DISPLAY_MODE_LABELS = { "clear-dark": "清晰深色", light: "护眼浅色", "racing-blue": "赛车蓝（高对比）", graphite: "石墨灰", midnight: "午夜紫", teal: "工程青", warm: "暖灰护眼" };
const SHORTCUT_STORAGE_KEY = "yanxiang-shortcuts-v1";
const AUTO_UPDATE_STORAGE_KEY = "yanxiang-auto-update-check";
const LAST_UPDATE_CHECK_KEY = "yanxiang-last-update-check";
const UPDATE_INSTALL_MODE_KEY = "yanxiang-update-install-mode";
const DEFAULT_LOADING_CARS = [
  { id: "default_formula_1", title: "方程式赛车一", url: "/static/assets/loading-car-formula-1.png" },
  { id: "default_formula_2", title: "方程式赛车二", url: "/static/assets/loading-car-formula-2.png" },
];
const SHORTCUT_DEFINITIONS = [
  { id: "new_invoice", label: "新增发票", description: "直接打开新增发票窗口", defaultKey: "Ctrl+N" },
  { id: "search_invoices", label: "搜索发票", description: "进入发票台账并定位搜索框", defaultKey: "Ctrl+F" },
  { id: "save_form", label: "保存当前窗口", description: "提交当前打开的编辑窗口", defaultKey: "Ctrl+S" },
  { id: "dashboard", label: "数据驾驶舱", description: "切换到数据驾驶舱", defaultKey: "Alt+1" },
  { id: "invoices", label: "发票台账", description: "切换到发票台账", defaultKey: "Alt+2" },
  { id: "settlements", label: "AA 结算", description: "切换到 AA 结算", defaultKey: "Alt+3" },
  { id: "reports", label: "分类统计", description: "切换到分类统计", defaultKey: "Alt+4" },
  { id: "members", label: "成员与账号", description: "切换到成员与账号", defaultKey: "Alt+5" },
  { id: "history", label: "日志与回溯", description: "管理员切换到版本回溯", defaultKey: "Alt+6" },
  { id: "creators", label: "创作者名单", description: "查看全赛季共享创作者", defaultKey: "Alt+7" },
  { id: "settings", label: "系统设置", description: "切换到系统设置", defaultKey: "Alt+8" },
  { id: "cycle_theme", label: "切换显示模式", description: "循环切换三种显示模式", defaultKey: "Alt+T" },
];
const DEFAULT_SHORTCUTS = Object.fromEntries(SHORTCUT_DEFINITIONS.map((item) => [item.id, item.defaultKey]));
const $ = (id) => document.getElementById(id);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const money = (value) => `¥${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const dateText = (value) => value ? String(value).slice(0, 10) : "-";
const nowDate = () => new Date().toISOString().slice(0, 10);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const roleLabel = (role) => ({ admin: "管理员", member: "成员", viewer: "公共只读" }[role] || role);
const statusLabel = (status) => ({ pending: "未报销", partial: "部分报销", reimbursed: "已报销" }[status] || status);
const burdenLabel = (type) => state.settings?.invoice_defaults?.burden_labels?.[type] || ({ team_aa: "全队 AA", specified_split: "指定成员", self_paid: "个人承担" }[type] || type);
const actionLabel = (action) => ({ create: "新增", update: "修改", delete: "删除", login: "登录", logout: "退出", restore: "版本回溯", upload: "上传附件", seed: "初始化", archive: "停用", switch: "切换赛季", sync_apply: "同步应用", sync_conflict: "同步冲突", create_snapshot: "建立版本", delete_demo: "清除演示数据", batch_import: "批量导入", batch_delete: "批量删除", batch_category: "批量修改分类", batch_status: "批量修改状态", ocr_complete: "OCR 完成", change_credentials: "修改账号" }[action] || action);

function savedDisplayMode() {
  try {
    const saved = localStorage.getItem(DISPLAY_MODE_KEY);
    return DISPLAY_MODES.has(saved) ? saved : "clear-dark";
  } catch (_) { return "clear-dark"; }
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function applyDisplayMode(mode, persist = true) {
  const safe = DISPLAY_MODES.has(mode) ? mode : "clear-dark";
  document.documentElement.dataset.theme = safe;
  $$('[data-theme-select]').forEach((select) => { select.value = safe; });
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.content = { "clear-dark": "#09131e", light: "#edf3f7", "racing-blue": "#0b2942", graphite: "#20252a", midnight: "#171329", teal: "#0c2929", warm: "#efe9df" }[safe];
  if (persist) {
    try { localStorage.setItem(DISPLAY_MODE_KEY, safe); } catch (_) { /* 浏览器禁用本地存储时仍可临时切换 */ }
  }
  if (state.dashboard && state.currentView === "dashboard") requestAnimationFrame(() => renderDashboard());
  return safe;
}

function finishDecision(value) {
  const resolve = state.decisionResolve;
  state.decisionResolve = null;
  if ($("decisionDialog").open) $("decisionDialog").close();
  if (resolve) resolve(value);
}

function showDecision({ title = "确认操作", message = "", confirmText = "确认", cancelText = "取消", tone = "danger", eyebrow = "请确认操作", inputLabel = "", inputValue = "" } = {}) {
  if (state.decisionResolve) finishDecision(false);
  const dialog = $("decisionDialog"), inputWrap = $("decisionInputWrap"), input = $("decisionInput");
  dialog.dataset.tone = tone;
  $("decisionEyebrow").textContent = eyebrow; $("decisionTitle").textContent = title; $("decisionMessage").textContent = message;
  $("decisionConfirmBtn").textContent = confirmText; $("decisionCancelBtn").textContent = cancelText;
  $("decisionConfirmBtn").className = `btn ${tone === "danger" ? "danger" : "primary"}`;
  $("decisionIcon").textContent = tone === "info" ? "i" : "!";
  inputWrap.classList.toggle("hidden", !inputLabel); $("decisionInputLabel").textContent = inputLabel || "名称";
  input.value = inputValue || ""; input.required = Boolean(inputLabel); input.setCustomValidity("");
  return new Promise((resolve) => {
    state.decisionResolve = resolve;
    dialog.showModal();
    requestAnimationFrame(() => (inputLabel ? input : $("decisionConfirmBtn")).focus());
  });
}

async function confirmAction(message, options = {}) {
  return Boolean(await showDecision({ message, ...options }));
}

async function requestText(title, inputValue = "") {
  const result = await showDecision({ title, message: "填写名称后确认创建。", confirmText: "创建", tone: "info", eyebrow: "输入内容", inputLabel: "版本名称", inputValue });
  return typeof result === "string" ? result.trim() : "";
}

function loadShortcutSettings() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(SHORTCUT_STORAGE_KEY) || "{}"); } catch (_) { stored = {}; }
  return Object.fromEntries(SHORTCUT_DEFINITIONS.map((item) => [item.id, typeof stored[item.id] === "string" && stored[item.id].length <= 40 ? stored[item.id] : item.defaultKey]));
}

function shortcutFromEvent(event) {
  const modifierCodes = new Set(["ControlLeft", "ControlRight", "AltLeft", "AltRight", "ShiftLeft", "ShiftRight", "MetaLeft", "MetaRight"]);
  if (modifierCodes.has(event.code)) return "";
  const codeMap = { Space: "Space", Enter: "Enter", Tab: "Tab", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", Insert: "Insert", Delete: "Delete", Minus: "-", Equal: "=", Comma: ",", Period: ".", Slash: "/", Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]", Backslash: "\\", Backquote: "`" };
  let key = codeMap[event.code] || "";
  if (/^Key[A-Z]$/.test(event.code)) key = event.code.slice(3);
  else if (/^Digit[0-9]$/.test(event.code)) key = event.code.slice(5);
  else if (/^F([1-9]|1[0-2])$/.test(event.code)) key = event.code;
  if (!key) return "";
  const modifiers = [];
  if (event.ctrlKey) modifiers.push("Ctrl");
  if (event.altKey) modifiers.push("Alt");
  if (event.shiftKey) modifiers.push("Shift");
  if (event.metaKey) modifiers.push("Meta");
  if (!modifiers.some((item) => ["Ctrl", "Alt", "Meta"].includes(item)) && !/^F([1-9]|1[0-2])$/.test(key)) return "";
  return [...modifiers, key].join("+");
}

function shortcutDuplicates(draft) {
  const counts = {};
  Object.values(draft).filter(Boolean).forEach((value) => { counts[value] = (counts[value] || 0) + 1; });
  return new Set(Object.entries(counts).filter(([, count]) => count > 1).map(([value]) => value));
}

function renderShortcutEditor() {
  const duplicates = shortcutDuplicates(state.shortcutDraft);
  $("shortcutList").innerHTML = SHORTCUT_DEFINITIONS.map((item) => {
    const value = state.shortcutDraft[item.id] || "";
    return `<div class="shortcut-row${duplicates.has(value) ? " conflict" : ""}"><div><b>${escapeHtml(item.label)}</b><small>${escapeHtml(item.description)}</small></div><input readonly data-shortcut-capture="${escapeHtml(item.id)}" value="${escapeHtml(value)}" placeholder="未设置" aria-label="${escapeHtml(item.label)}快捷键"><button type="button" class="row-action delete" data-shortcut-clear="${escapeHtml(item.id)}" title="清除快捷键">×</button></div>`;
  }).join("");
  $("shortcutConflict").textContent = duplicates.size ? `存在重复快捷键：${[...duplicates].join("、")}` : "";
  $("saveShortcutsBtn").disabled = Boolean(duplicates.size);
}

function openShortcutSettings() {
  state.shortcutDraft = { ...state.shortcuts };
  renderShortcutEditor();
  $("shortcutsDialog").showModal();
}

function captureShortcut(event) {
  const input = event.target.closest("[data-shortcut-capture]"); if (!input) return;
  event.preventDefault(); event.stopPropagation();
  if (event.key === "Escape") { input.blur(); return; }
  const shortcut = shortcutFromEvent(event);
  if (!shortcut) { $("shortcutConflict").textContent = "请使用 Ctrl 或 Alt 组合键，也可以使用 F1–F12。"; return; }
  state.shortcutDraft[input.dataset.shortcutCapture] = shortcut;
  renderShortcutEditor();
}

async function resetShortcutSettings() {
  const accepted = await confirmAction("当前自定义按键将被默认组合替换，保存后生效。", { title: "恢复默认快捷键", confirmText: "恢复默认", tone: "warning" });
  if (!accepted) return;
  state.shortcutDraft = { ...DEFAULT_SHORTCUTS }; renderShortcutEditor(); toast("已载入默认快捷键，请点击保存");
}

async function saveShortcutSettings(event) {
  event.preventDefault();
  if (shortcutDuplicates(state.shortcutDraft).size) return renderShortcutEditor();
  state.shortcuts = { ...state.shortcutDraft };
  try { localStorage.setItem(SHORTCUT_STORAGE_KEY, JSON.stringify(state.shortcuts)); } catch (_) { return toast("浏览器禁止保存快捷键设置", "error"); }
  await persistPersonalPreferences({ shortcuts: state.shortcuts }).catch(() => {});
  $("shortcutsDialog").close(); toast("快捷键设置已保存");
}

function currentPersonalPreferences(extra = {}) {
  return {
    ...state.userPreferences,
    theme: savedDisplayMode(), shortcuts: state.shortcuts,
    audio_mode: $("personalAudioMode")?.value || state.userPreferences.audio_mode || "sequence",
    audio_muted: $("personalAudioEnabled") ? !$("personalAudioEnabled").checked : state.userPreferences.audio_muted !== false,
    audio_volume: $("personalAudioVolume") ? Number($("personalAudioVolume").value || 35) / 100 : Number(state.userPreferences.audio_volume ?? .35),
    ...extra,
  };
}

async function persistPersonalPreferences(extra = {}) {
  if (!state.user) return;
  const preferences = currentPersonalPreferences(extra);
  const result = await api("/api/user/preferences", { method: "PUT", body: preferences });
  state.userPreferences = result.settings || preferences;
}

async function loadPersonalSettings(render = true) {
  if (!state.user) return;
  const result = await api("/api/user/preferences");
  state.userPreferences = result.settings || state.userPreferences || {};
  state.personalAudio = result.audio || [];
  if (render) renderPersonalSettings();
}

function renderPersonalSettings() {
  $("personalAudioMode").value = state.userPreferences.audio_mode || "sequence";
  $("personalAudioEnabled").checked = state.userPreferences.audio_muted === false;
  $("personalAudioVolume").value = Math.round(Number(state.userPreferences.audio_volume ?? .35) * 100);
  $("personalAudioVolumeText").textContent = `${$("personalAudioVolume").value}%`;
  $("personalAudioList").innerHTML = state.personalAudio.map((item, index) => `<article class="personal-audio-item"><div><b>${escapeHtml(item.title || item.original_name)}</b><small>${escapeHtml(item.original_name)}</small></div><button type="button" class="row-action" data-audio-play="${index}">▶</button><button type="button" class="row-action delete" data-audio-delete="${escapeHtml(item.id)}">×</button></article>`).join("") || `<div class="empty-state">尚未导入音乐</div>`;
  const player = $("personalAudioPlayer"); player.volume = Math.max(0, Math.min(1, Number(state.userPreferences.audio_volume ?? .35))); player.muted = state.userPreferences.audio_muted !== false;
}

async function openPersonalSettings() {
  try { await loadPersonalSettings(true); $("personalSettingsDialog").showModal(); }
  catch (error) { toast(error.message, "error"); }
}

function playPersonalAudio(index) {
  const item = state.personalAudio[index]; if (!item) return;
  state.audioIndex = index; const player = $("personalAudioPlayer");
  if (player.src !== new URL(item.url, location.href).href) player.src = item.url;
  player.muted = !$("personalAudioEnabled").checked; player.volume = Number($("personalAudioVolume").value || 35) / 100;
  player.play().catch(() => toast("浏览器暂时阻止播放，请再次点击播放", "error"));
}

function advancePersonalAudio() {
  if (!state.personalAudio.length || state.audioIndex < 0) return;
  const mode = $("personalAudioMode").value;
  if (mode === "repeat-one") return playPersonalAudio(state.audioIndex);
  const next = mode === "random" ? Math.floor(Math.random() * state.personalAudio.length) : (state.audioIndex + 1) % state.personalAudio.length;
  playPersonalAudio(next);
}

async function uploadPersonalAudio(files) {
  const selected = [...(files || [])]; if (!selected.length) return;
  loading(true, "正在导入个人音乐", 2, `共 ${selected.length} 个文件`, false);
  for (let index = 0; index < selected.length; index++) {
    const form = new FormData(); form.append("file", selected[index]); form.append("title", selected[index].name);
    try { await api("/api/user/audio", { method: "POST", body: form }); } catch (error) { toast(`${selected[index].name}：${error.message}`, "error"); }
    setLoadingProgress(Math.round((index + 1) / selected.length * 100), "正在导入个人音乐", `已完成 ${index + 1}/${selected.length}`);
  }
  loading(false); await loadPersonalSettings(true); $("personalAudioFiles").value = "";
}

async function deletePersonalAudio(id) {
  try { await api(`/api/user/audio/${encodeURIComponent(id)}`, { method: "DELETE" }); await loadPersonalSettings(true); }
  catch (error) { toast(error.message, "error"); }
}

async function exportPersonalSettings() {
  try {
    await persistPersonalPreferences();
    const include = $("settingsIncludeMedia").checked ? "1" : "0";
    const response = await fetch(`/api/user/settings-package/export?include_media=${include}`, { credentials: "same-origin" });
    if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail || "设置包导出失败"); }
    await saveBlob(await response.blob(), `${state.user.username}软件设置.zip`); toast("个人软件设置包已导出");
  } catch (error) { toast(error.message, "error"); }
}

async function importPersonalSettings(file) {
  if (!file) return; const form = new FormData(); form.append("file", file);
  try {
    const result = await api("/api/user/settings-package/import", { method: "POST", body: form });
    state.userPreferences = result.settings || {}; state.shortcuts = { ...DEFAULT_SHORTCUTS, ...(state.userPreferences.shortcuts || {}) };
    if (DISPLAY_MODES.has(state.userPreferences.theme)) applyDisplayMode(state.userPreferences.theme);
    await loadPersonalSettings(true); toast(`设置包已导入${result.imported_media ? `，包含 ${result.imported_media} 个音频` : ""}`);
  } catch (error) { toast(error.message, "error"); }
  finally { $("importPersonalSettingsInput").value = ""; }
}

async function runShortcutAction(action) {
  const openDialog = document.querySelector("dialog[open]");
  if (action === "save_form") {
    const focusedForm = document.activeElement?.closest?.("form");
    const form = focusedForm && openDialog?.contains(focusedForm) ? focusedForm : openDialog?.querySelector("form");
    if (!form || ["decisionForm", "shortcutsForm"].includes(form.id)) return toast("当前没有可保存的编辑窗口", "error");
    form.requestSubmit(); return;
  }
  if (action === "cycle_theme") {
    const modes = [...DISPLAY_MODES], current = modes.indexOf(document.documentElement.dataset.theme);
    const mode = applyDisplayMode(modes[(current + 1) % modes.length]); toast(`已切换为${DISPLAY_MODE_LABELS[mode]}`); return;
  }
  if (!state.user) return;
  if (action === "new_invoice") {
    if (!canWrite()) return toast("当前账号为只读权限", "error");
    openNewInvoice(); return;
  }
  if (action === "search_invoices") {
    await navigate("invoices"); $("invoiceSearch").focus(); $("invoiceSearch").select(); return;
  }
  if (action === "history" && !isAdmin()) return toast("版本回溯仅管理员可用", "error");
  if (["dashboard", "invoices", "settlements", "reports", "members", "history", "creators", "settings"].includes(action)) await navigate(action);
}

function handleGlobalShortcut(event) {
  if (event.defaultPrevented || event.repeat || event.isComposing) return;
  const shortcut = shortcutFromEvent(event); if (!shortcut) return;
  const action = SHORTCUT_DEFINITIONS.find((item) => state.shortcuts[item.id] === shortcut)?.id; if (!action) return;
  const openDialog = document.querySelector("dialog[open]");
  if (openDialog && (action !== "save_form" || ["decisionDialog", "shortcutsDialog"].includes(openDialog.id))) return;
  if (!state.user && action !== "cycle_theme") return;
  event.preventDefault();
  runShortcutAction(action).catch((error) => toast(error.message, "error"));
}

function canWrite() { return Boolean(state.user?.permissions?.write) && state.season?.is_open !== false; }
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
  if (response.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/recover-admin")) {
    showLogin();
    throw new Error("登录已失效，请重新登录");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `请求失败 (${response.status})`);
  return payload;
}

function apiUpload(path, form, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path, true); request.withCredentials = true; request.responseType = "json";
    if (state.csrf) request.setRequestHeader("X-CSRF-Token", state.csrf);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    });
    request.addEventListener("load", () => {
      const payload = request.response || {};
      if (request.status === 401) { showLogin(); reject(new Error("登录已失效，请重新登录")); return; }
      if (request.status < 200 || request.status >= 300) { reject(new Error(payload.detail || payload.error || `请求失败 (${request.status})`)); return; }
      resolve(payload);
    });
    request.addEventListener("error", () => reject(new Error("网络连接中断，文件尚未导入")));
    request.send(form);
  });
}

function toast(message, type = "success", duration = 3300) {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("toastRegion").appendChild(item);
  setTimeout(() => item.remove(), duration);
}

function loadingCars() {
  const configured = state.settings?.loading_cars || state.publicSettings?.loading_cars || [];
  const valid = configured.filter((item) => item && (item.url || item.private_url));
  return valid.length ? valid : DEFAULT_LOADING_CARS;
}

function setLoadingProgress(value, text = "", detail = "") {
  const progress = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  state.loadingProgress = progress;
  $("raceLoader").style.setProperty("--progress", `${progress}%`);
  $("raceLoader").style.setProperty("--car-shift", `${-progress}%`);
  $("loadingPercent").textContent = `${progress}%`;
  if (text) $("loadingText").textContent = text;
  if (detail) $("loadingDetail").textContent = detail;
}

function loading(show, text = "", progress = null, detail = "任务正在本机运行，请勿关闭软件", simulate = true) {
  clearInterval(state.loadingTimer); state.loadingTimer = null;
  if (!show) {
    if ($("loadingOverlay").classList.contains("hidden")) return;
    const token = state.loadingToken;
    setLoadingProgress(100, text || $("loadingText").textContent, "处理完成");
    setTimeout(() => { if (token === state.loadingToken) $("loadingOverlay").classList.add("hidden"); }, 320);
    return;
  }
  state.loadingToken += 1;
  const cars = loadingCars(), car = cars[Math.floor(Math.random() * cars.length)] || DEFAULT_LOADING_CARS[0];
  $("loadingCar").src = car.private_url || car.url; $("loadingCar").alt = car.title || "赛车任务进度";
  $("loadingOverlay").classList.remove("hidden");
  setLoadingProgress(progress === null ? 3 : progress, text || "正在处理", detail);
  if (simulate) {
    state.loadingTimer = setInterval(() => {
      if (state.loadingProgress >= 92) return;
      const step = state.loadingProgress < 35 ? 3 : (state.loadingProgress < 70 ? 2 : 1);
      setLoadingProgress(Math.min(92, state.loadingProgress + step));
    }, 520);
  }
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
  stopLoginSlideshow();
  const slides = state.publicSettings?.login_slides || [];
  if (state.publicSettings?.login_random_enabled && slides.length) state.loginSlideIndex = Math.floor(Math.random() * slides.length);
  showLoginSlide(state.loginSlideIndex || 0, true);
}

function applyPublicAppearance(settings = {}) {
  state.publicSettings = settings;
  setAccent(settings.accent_color || "#27d3ff");
  const teamName = String(settings.team_name || "燕翔车队").replace(/\s*Racing Team\s*/i, "").trim() || "燕翔车队";
  $("loginTeamName").textContent = teamName;
  document.documentElement.style.setProperty("--login-panel-opacity", String(Number(settings.login_panel_opacity || .78)));
  if (!$("loginView").classList.contains("hidden")) startLoginSlideshow();
}

async function loadPublicAppearance() {
  try { const result = await api("/api/public/appearance"); applyPublicAppearance(result.settings || {}); }
  catch (_) { applyPublicAppearance({}); }
}

function applyTheme() {
  const settings = state.settings || {};
  const randomSlides = (settings.login_slides || []).filter((item) => item.kind !== "video");
  const randomBackground = settings.global_background_random && randomSlides.length ? randomSlides[Math.floor(Math.random() * randomSlides.length)] : null;
  const mediaUrl = randomBackground?.private_url || randomBackground?.url || settings.background_media_url || settings.background_image || "";
  const mediaKind = randomBackground ? "image" : (settings.background_media_kind || "image");
  const background = mediaUrl && mediaKind !== "video" ? `url("${String(mediaUrl).replace(/"/g, "%22")}")` : "none";
  document.documentElement.style.setProperty("--custom-background", background);
  document.documentElement.style.setProperty("--background-overlay", String(settings.background_overlay || ".82"));
  setAccent(settings.accent_color);
  const video = $("appBackgroundVideo");
  if (mediaUrl && mediaKind === "video") {
    if (video.getAttribute("src") !== mediaUrl) video.src = mediaUrl;
    video.classList.add("active"); video.play().catch(() => {});
  } else { video.pause(); video.removeAttribute("src"); video.load(); video.classList.remove("active"); }
  document.title = `${settings.team_name || "燕翔车队"} · 经费管理系统 V${state.version || "2.3.1"}`;
}

function applyAccess() {
  $$('[data-admin]').forEach((element) => element.classList.toggle("hidden", !isAdmin()));
  $$(".write-only").forEach((element) => element.classList.toggle("hidden", !canWrite() || (element.hasAttribute("data-admin") && !isAdmin())));
  if (!isAdmin() && state.currentView === "history") navigate("dashboard");
  const season = state.season || { name: "2026赛季", is_open: true };
  $("currentSeasonLabel").textContent = season.name || "2026赛季";
  $("seasonBadgeBtn").classList.toggle("archived", season.is_open === false);
  $("seasonBadgeBtn").title = isAdmin() ? "管理和切换赛季" : `当前赛季：${season.name || "2026赛季"}`;
  $("seasonReadOnlyBanner").classList.toggle("hidden", season.is_open !== false);
  $("userName").textContent = state.user.display_name;
  $("userRole").textContent = roleLabel(state.user.role);
  const ownMember = state.members.find((item) => item.id === state.user.member_id);
  $("userAvatar").innerHTML = ownMember?.avatar_url ? `<img src="${escapeHtml(ownMember.avatar_url)}" alt="头像">` : escapeHtml(state.user.display_name.slice(0, 1));
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
  state.departments = data.departments || [];
  state.creators = data.creators || [];
  state.season = data.season || null;
  state.productTypes = data.product_types || [];
  state.settings = data.settings || {};
  state.dashboard = data.dashboard;
  state.sync = data.sync || {};
  state.version = data.version || "2.3.1";
  state.qualityIssues = data.quality_issues || [];
  state.openSourceReferences = data.open_source_references || [];
  state.userPreferences = data.user_preferences || {};
  if (state.userPreferences.shortcuts && typeof state.userPreferences.shortcuts === "object") state.shortcuts = { ...state.shortcuts, ...state.userPreferences.shortcuts };
  if (DISPLAY_MODES.has(state.userPreferences.theme)) applyDisplayMode(state.userPreferences.theme, false);
  $("versionLabel").textContent = `V${data.version}`;
  $("updateCurrentVersion").textContent = `V${state.version}`;
  state.publicSettings = state.settings; applyTheme(); applyAccess(); renderSync(state.sync); renderReferenceOptions(); renderDashboard(); renderQualityCenter(); showApp(); connectSocket();
  loadPersonalSettings(false).catch(() => {});
  if (state.user.must_change_password) {
    setTimeout(() => { toast("首次登录请先修改账号与密码", "error", 5000); openCredentials(); }, 350);
  }
  scheduleAutomaticUpdateCheck();
}

function renderReferenceOptions() {
  const activeMembers = state.members.filter((item) => item.active && !item.deleted_at);
  const memberOptions = activeMembers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.department || "未分组")}</option>`).join("");
  ["invoicePayer", "batchPayer", "defaultInvoicePayer", "settlementFrom", "settlementTo"].forEach((id) => { if ($(id)) $(id).innerHTML = memberOptions; });
  $("editUserMember").innerHTML = `<option value="">不关联</option>${memberOptions}`;
  const categoryOptions = state.categories.filter((item) => item.active && !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  ["invoiceCategory", "batchCategory", "defaultInvoiceCategory"].forEach((id) => { if ($(id)) $(id).innerHTML = `<option value="">未分类</option>${categoryOptions}`; });
  $("invoiceCategoryFilter").innerHTML = `<option value="">全部分类</option>${categoryOptions}`;
  const sourceOptions = state.fundingSources.filter((item) => item.active && !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  ["invoiceSource", "batchSource", "defaultInvoiceSource"].forEach((id) => { if ($(id)) $(id).innerHTML = `<option value="">未选择</option>${sourceOptions}`; });
  $("invoiceSourceFilter").innerHTML = `<option value="">全部来源</option>${sourceOptions}`;
  $("invoiceProduct").innerHTML = state.productTypes.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  $("departmentOptions").innerHTML = state.departments.map((item) => `<option value="${escapeHtml(item.name)}"></option>`).join("");
  const burdenOptions = ["team_aa", "specified_split", "self_paid"].map((value) => `<option value="${value}">${escapeHtml(burdenLabel(value))}</option>`).join("");
  ["batchBurden", "defaultInvoiceBurden"].forEach((id) => { if ($(id)) { const current = $(id).value; $(id).innerHTML = burdenOptions; if (["team_aa", "specified_split", "self_paid"].includes(current)) $(id).value = current; } });
  $$('#invoiceForm input[name="burdenType"]').forEach((input) => { const span = input.nextElementSibling; if (span) span.textContent = burdenLabel(input.value); });
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

function activeMembers() { return state.members.filter((item) => item.active && !item.deleted_at); }

function renderSimpleMemberPicker(containerId, selected = [], disabled = false) {
  const selectedSet = new Set(selected);
  $(containerId).innerHTML = activeMembers().map((item) => `<label class="split-member ${disabled ? "disabled" : ""}" style="--member-color:${escapeHtml(item.avatar_color)}"><input type="checkbox" value="${escapeHtml(item.id)}" ${selectedSet.has(item.id) ? "checked" : ""} ${disabled ? "disabled" : ""}><strong>${escapeHtml(item.name)}</strong></label>`).join("") || `<div class="empty-state">当前赛季没有可用成员</div>`;
}

function simpleMemberIds(containerId) { return $$(`#${containerId} input[type="checkbox"]:checked`).map((input) => input.value); }

function updateBatchSplitVisibility() {
  const specified = $("batchBurden").value === "specified_split";
  $("batchSplitWrap").classList.toggle("hidden", !specified);
  if (specified && !simpleMemberIds("batchSplitMembers").length) renderSimpleMemberPicker("batchSplitMembers", activeMembers().map((item) => item.id));
}

function updateDefaultSplitVisibility() {
  const specified = $("defaultInvoiceBurden").value === "specified_split";
  $("defaultSplitWrap").classList.toggle("hidden", !specified);
}

function openInvoiceDefaults() {
  renderReferenceOptions();
  const defaults = state.settings.invoice_defaults || {};
  $("defaultInvoiceCategory").value = defaults.category_id || "";
  $("defaultInvoicePayer").value = activeMembers().some((item) => item.id === defaults.payer_member_id) ? defaults.payer_member_id : (state.user.member_id || activeMembers()[0]?.id || "");
  $("defaultInvoiceSource").value = defaults.funding_source_id || "";
  $("defaultInvoiceBurden").value = defaults.burden_type || "team_aa";
  $("defaultBatchNote").value = defaults.batch_note || "";
  $("burdenLabelTeam").value = burdenLabel("team_aa"); $("burdenLabelSpecified").value = burdenLabel("specified_split"); $("burdenLabelSelf").value = burdenLabel("self_paid");
  renderSimpleMemberPicker("defaultSplitMembers", defaults.split_member_ids || []);
  updateDefaultSplitVisibility(); $("invoiceDefaultsDialog").showModal();
}

async function saveInvoiceDefaults(event) {
  event.preventDefault();
  try {
    const result = await api("/api/admin/invoice-defaults", { method: "PUT", body: {
      category_id: $("defaultInvoiceCategory").value, payer_member_id: $("defaultInvoicePayer").value,
      funding_source_id: $("defaultInvoiceSource").value, burden_type: $("defaultInvoiceBurden").value,
      split_member_ids: simpleMemberIds("defaultSplitMembers"), batch_note: $("defaultBatchNote").value,
      burden_labels: { team_aa: $("burdenLabelTeam").value, specified_split: $("burdenLabelSpecified").value, self_paid: $("burdenLabelSelf").value },
    } });
    state.settings.invoice_defaults = result.defaults; renderReferenceOptions(); $("invoiceDefaultsDialog").close(); toast("录入与批量导入默认值已保存");
  } catch (error) { toast(error.message, "error", 6000); }
}

const routes = {
  dashboard: ["数据驾驶舱 / 01", "数据驾驶舱"], invoices: ["发票台账 / 02", "发票台账"],
  settlements: ["成员结算 / 03", "AA 结算"], reports: ["经费统计 / 04", "分类统计"],
  members: ["成员权限 / 05", "成员与账号"], history: ["版本控制 / 06", "日志与回溯"],
  creators: ["项目署名 / 07", "创作者名单"], settings: ["系统配置 / 08", "系统设置"],
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
    if (view === "creators") await loadCreators();
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
    state.user = data.user; state.members = data.members; state.categories = data.categories; state.fundingSources = data.funding_sources;
    state.departments = data.departments || []; state.creators = data.creators || []; state.season = data.season || null;
    state.settings = data.settings; state.dashboard = data.dashboard; state.sync = data.sync; state.csrf = data.csrf_token;
    state.publicSettings = state.settings; applyTheme(); applyAccess(); renderReferenceOptions(); renderSync(state.sync);
    if (state.currentView === "dashboard") renderDashboard();
    if (state.currentView === "invoices") await loadInvoices();
    if (state.currentView === "settlements") await loadSettlements();
    if (state.currentView === "reports") await loadReports();
    if (state.currentView === "members") await loadMembersAndUsers();
    if (state.currentView === "history" && isAdmin()) await loadHistory();
    if (state.currentView === "creators") renderCreators();
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
  $("dashboardSubtitle").textContent = `${state.settings.team_name || "燕翔车队"} · ${state.season?.name || "当前赛季"} · ${data.invoice_count || 0} 笔有效记录`;
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
  const chartMuted = cssVar("--chart-muted", "#71869a"), chartGrid = cssVar("--chart-grid", "rgba(156,183,211,.16)");
  const chartPoint = cssVar("--chart-point", "#07101a"), accent = cssVar("--accent", "#27d3ff"), accentRgb = cssVar("--accent-rgb", "39, 211, 255");
  ctx.font = "10px Bahnschrift, sans-serif"; ctx.textAlign = "right"; ctx.fillStyle = chartMuted; ctx.strokeStyle = chartGrid; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const y = pad.t + (height - pad.t - pad.b) * i / 4; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke(); ctx.fillText(money(max * (4 - i) / 4).replace("¥", ""), pad.l - 8, y + 3); }
  if (!items.length) { ctx.textAlign = "center"; ctx.fillStyle = chartMuted; ctx.fillText("暂无月度数据", width / 2, height / 2); return; }
  const xAt = (index) => pad.l + (width - pad.l - pad.r) * (items.length === 1 ? .5 : index / (items.length - 1));
  const yAt = (value) => pad.t + (height - pad.t - pad.b) * (1 - value / max);
  const gradient = ctx.createLinearGradient(0, pad.t, 0, height - pad.b); gradient.addColorStop(0, `rgba(${accentRgb},.28)`); gradient.addColorStop(1, `rgba(${accentRgb},0)`);
  ctx.beginPath(); items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); index ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.lineTo(xAt(items.length - 1), height - pad.b); ctx.lineTo(xAt(0), height - pad.b); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath(); items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); index ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.strokeStyle = accent; ctx.lineWidth = 2; ctx.stroke();
  items.forEach((item, index) => { const x = xAt(index), y = yAt(item.amount); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fillStyle = chartPoint; ctx.fill(); ctx.strokeStyle = accent; ctx.stroke(); ctx.fillStyle = chartMuted; ctx.textAlign = "center"; ctx.fillText(item.month.slice(5), x, height - 10); });
}

function drawDonut(items) {
  const canvas = $("categoryChart"); if (!canvas || !canvas.offsetParent) return;
  const { ctx, width, height } = prepareCanvas(canvas, 180); ctx.clearRect(0, 0, width, height);
  const total = items.reduce((sum, item) => sum + Number(item.amount || 0), 0); const cx = width / 2, cy = height / 2, radius = Math.min(width, height) * .34;
  let angle = -Math.PI / 2;
  if (!total) { ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,.10)"); ctx.lineWidth = 20; ctx.stroke(); }
  items.slice(0, 7).forEach((item) => { const portion = item.amount / total * Math.PI * 2; ctx.beginPath(); ctx.arc(cx, cy, radius, angle, angle + portion - .02); ctx.strokeStyle = item.color; ctx.lineWidth = 20; ctx.stroke(); angle += portion; });
  ctx.fillStyle = cssVar("--chart-muted", "#71869a"); ctx.font = "9px Bahnschrift"; ctx.textAlign = "center"; ctx.fillText("总计", cx, cy - 6); ctx.fillStyle = cssVar("--text", "#edf5fc"); ctx.font = "700 14px Bahnschrift"; ctx.fillText(money(total), cx, cy + 12);
  $("categoryLegend").innerHTML = items.slice(0, 6).map((item) => `<div><i style="background:${escapeHtml(item.color)}"></i><span>${escapeHtml(item.name)}</span><b>${money(item.amount)}</b></div>`).join("") || `<div><span>暂无分类数据</span></div>`;
}

function colorTag(label, color) { return `<span class="color-tag" style="--tag-color:${escapeHtml(color)};--tag-border:${escapeHtml(color)}44;--tag-bg:${escapeHtml(color)}0d"><i></i>${escapeHtml(label)}</span>`; }
function statusTag(status) { return `<span class="status-tag ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`; }
function emptyRow(columns, text) { return `<tr><td colspan="${columns}" class="empty-state">${escapeHtml(text)}</td></tr>`; }

function invoiceFilterParams() {
  const params = new URLSearchParams();
  const mappings = [["invoiceSearch", "search"], ["invoiceStatusFilter", "status"], ["invoiceCategoryFilter", "category_id"], ["invoiceSourceFilter", "source_id"]];
  mappings.forEach(([id, key]) => { if ($(id).value) params.set(key, $(id).value); });
  return params;
}

async function loadInvoices() {
  const params = invoiceFilterParams();
  const [data] = await Promise.all([api(`/api/invoices?${params}`), loadQualityIssues(false)]); state.invoices = data.items || []; renderInvoices();
}

function invoiceQualityClass(invoiceId) {
  const types = state.qualityIssues.filter((item) => item.invoice_id === invoiceId).map((item) => item.issue_type);
  if (types.some((value) => ["import_failed", "ocr_failed"].includes(value))) return "invoice-quality-import";
  if (types.some((value) => ["export_failed", "conversion_failed", "attachment_missing"].includes(value))) return "invoice-quality-export";
  if (types.includes("low_confidence")) return "invoice-quality-low";
  return "";
}

function selectedInvoices() { return state.invoices.filter((item) => state.selectedInvoiceIds.has(item.id)); }

function renderInvoiceSelection() {
  const selected = selectedInvoices();
  const total = selected.reduce((sum, item) => sum + Number(item.total_amount || 0), 0);
  $("batchSelectionBar").classList.toggle("hidden", !selected.length);
  $("batchSelectedCount").textContent = `已选择 ${selected.length} 张`;
  $("batchSelectedSum").textContent = `合计 ${money(total)}`;
  const allSelected = state.invoices.length > 0 && state.invoices.every((item) => state.selectedInvoiceIds.has(item.id));
  $("selectAllInvoices").checked = allSelected;
  $("selectAllInvoices").indeterminate = selected.length > 0 && !allSelected;
  $$("#invoiceTable tr[data-invoice-row]").forEach((row) => row.classList.toggle("invoice-row-selected", state.selectedInvoiceIds.has(row.dataset.invoiceRow)));
  applyAccess();
}

function clearInvoiceSelection(render = true) {
  state.selectedInvoiceIds.clear();
  if (render) {
    $$("#invoiceTable .invoice-select").forEach((input) => { input.checked = false; });
    renderInvoiceSelection();
  }
}

function renderInvoices() {
  const validIds = new Set(state.invoices.map((item) => item.id));
  state.selectedInvoiceIds = new Set([...state.selectedInvoiceIds].filter((id) => validIds.has(id)));
  const sum = state.invoices.reduce((total, item) => total + Number(item.total_amount || 0), 0);
  $("invoiceCount").textContent = `${state.invoices.length} 条记录`; $("invoiceSum").textContent = `合计 ${money(sum)}`;
  $("invoiceTable").innerHTML = state.invoices.map((item) => {
    const attachment = item.attachment_id ? `<button class="row-action" type="button" data-attachment-open="${escapeHtml(item.attachment_id)}" title="使用电脑默认程序打开附件">↗</button>` : "";
    const actions = canWrite() ? `${attachment}<button class="row-action" data-invoice-action="edit" data-id="${escapeHtml(item.id)}" title="编辑">✎</button><button class="row-action delete" data-invoice-action="delete" data-id="${escapeHtml(item.id)}" title="删除">×</button>` : `${attachment}<button class="row-action" data-invoice-action="view" data-id="${escapeHtml(item.id)}" title="查看">⌕</button>`;
    const splitNames = item.splits.map((split) => split.member_name).join("、");
    const progress = item.total_amount > 0 ? Math.round(item.reimbursed_amount / item.total_amount * 100) : 0;
    const checked = state.selectedInvoiceIds.has(item.id);
    return `<tr data-invoice-row="${escapeHtml(item.id)}" class="${checked ? "invoice-row-selected " : ""}${invoiceQualityClass(item.id)}">
      <td class="select-cell"><input class="invoice-select" type="checkbox" data-id="${escapeHtml(item.id)}" aria-label="选择 ${escapeHtml(item.vendor || "该发票")}" ${checked ? "checked" : ""}></td>
      <td><span class="cell-main">开票 ${escapeHtml(dateText(item.invoice_date))}</span><span class="cell-sub">上传 ${escapeHtml(dateText(item.uploaded_at || item.created_at))} · ${escapeHtml(item.created_by_name || "系统任务")}</span></td>
      <td><span class="cell-main">${escapeHtml(item.vendor || "待补充")}</span><span class="cell-sub">${escapeHtml(item.product_type)}${item.ocr_status === "queued" ? " · OCR排队中" : ""}</span></td>
      <td>${colorTag(item.category_name || "未分类", item.category_color || "#9aa7b7")}</td>
      <td><span class="cell-main">${escapeHtml(item.payer_name || "-")} 垫付</span><span class="cell-sub">${escapeHtml(burdenLabel(item.burden_type))} · ${escapeHtml(splitNames || "-")}</span></td>
      <td>${item.funding_source_name ? colorTag(item.funding_source_name, item.funding_source_color || "#9aa7b7") : "-"}</td>
      <td>${statusTag(item.reimbursement_status)}<span class="cell-sub">${progress}% · ${money(item.reimbursed_amount)}</span></td>
      <td class="numeric amount-cell">${money(item.total_amount)}</td><td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }).join("") || emptyRow(9, "没有符合条件的记录");
  renderInvoiceSelection();
}

async function loadQualityIssues(render = true) {
  try {
    const result = await api("/api/quality/issues"); state.qualityIssues = result.items || [];
    if (render) { renderQualityCenter(); if (state.currentView === "invoices") renderInvoices(); }
  } catch (_) { /* 旧补丁后端暂不可用时不影响台账 */ }
  return state.qualityIssues;
}

function renderQualityCenter() {
  const items = state.qualityIssues || [];
  $("qualityFaultCenter").classList.toggle("hidden", !state.user || !items.length);
  $("qualityFaultCount").textContent = String(items.length);
  $("qualityFaultList").innerHTML = items.map((item) => `<article class="quality-fault-item ${escapeHtml(item.issue_type)}">
    <i></i><div><b>${escapeHtml(item.label || "待处理问题")} · ${escapeHtml(item.vendor || item.attachment_name || "未关联发票")}</b><small>${escapeHtml(item.message || "请复核")}</small></div>
    <div>${item.invoice_id ? `<button type="button" data-quality-open="${escapeHtml(item.id)}">定位</button>` : ""}<button type="button" data-quality-resolve="${escapeHtml(item.id)}">已处理</button></div>
  </article>`).join("") || `<div class="empty-state">当前没有待处理问题</div>`;
}

async function resolveQualityIssue(id) {
  try { await api(`/api/quality/issues/${encodeURIComponent(id)}/resolve`, { method: "POST" }); await loadQualityIssues(true); toast("问题已标记为处理完成"); }
  catch (error) { toast(error.message, "error"); }
}

async function openQualityIssue(id) {
  const issue = state.qualityIssues.find((item) => item.id === id);
  if (!issue?.invoice_id) return toast("该问题未关联具体发票，可在故障框中直接标记处理", "error");
  try { await navigate("invoices"); await openInvoice(issue.invoice_id, !canWrite()); $("qualityFaultPanel").classList.add("hidden"); }
  catch (error) { toast(error.message, "error"); }
}

function previewKind(item) {
  const mime = String(item?.mime || "").toLowerCase(), name = String(item?.name || "").toLowerCase();
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|bmp|webp|tiff?)$/.test(name)) return "image";
  if (mime === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (mime.startsWith("text/") || name.endsWith(".txt")) return "text";
  if (name.endsWith(".zip")) return "zip";
  if (name.endsWith(".ofd")) return "ofd";
  return "file";
}

function previewThumb(item) {
  const kind = previewKind(item);
  if (kind === "image") return `<span class="attachment-thumb"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}"></span>`;
  if (kind === "pdf") return `<span class="attachment-thumb"><iframe src="${escapeHtml(item.url)}#toolbar=0&navpanes=0&view=FitH" title="${escapeHtml(item.name)}"></iframe></span>`;
  return `<span class="attachment-thumb">${escapeHtml(kind.toUpperCase())}</span>`;
}

function setCurrentInvoicePreview(item) {
  if (state.currentInvoicePreview?.revoke) URL.revokeObjectURL(state.currentInvoicePreview.url);
  state.currentInvoicePreview = item || null;
  if (!item) { $("invoiceAttachmentPreview").innerHTML = ""; $("invoiceAttachmentPreview").classList.add("hidden"); return; }
  $("invoiceAttachmentPreview").innerHTML = `<button type="button" class="attachment-preview-card" data-open-current-preview>${previewThumb(item)}<span><b>${escapeHtml(item.name || "发票附件")}</b><small>点击放大、缩小或打开原文件<br>${escapeHtml((item.mime || "文件").replace("application/", ""))}</small></span></button>`;
  $("invoiceAttachmentPreview").classList.remove("hidden");
}

function clearBatchPreviews() {
  state.batchPreviewFiles.forEach((item) => { if (item.revoke) URL.revokeObjectURL(item.url); });
  state.batchPreviewFiles = [];
}

function renderBatchPreviews(files) {
  clearBatchPreviews();
  state.batchPreviewFiles = [...(files || [])].map((file) => ({ name: file.name, mime: file.type, url: URL.createObjectURL(file), revoke: true }));
  $("batchFilePreview").innerHTML = state.batchPreviewFiles.map((item, index) => `<button type="button" class="batch-file-card" data-batch-preview="${index}">${previewThumb(item)}<span><b>${escapeHtml(item.name)}</b><small>${escapeHtml(previewKind(item).toUpperCase())}</small></span></button>`).join("") || `<div class="empty-state">选择文件后将在这里显示缩略图，点击可放大查看原文件。</div>`;
}

function applyAttachmentZoom(value) {
  state.attachmentViewerZoom = Math.max(.35, Math.min(3, Number(value || 1)));
  const content = $("attachmentViewerCanvas").firstElementChild;
  if (content) content.style.transform = `scale(${state.attachmentViewerZoom})`;
  $("attachmentZoomValue").textContent = `${Math.round(state.attachmentViewerZoom * 100)}%`;
}

function openAttachmentViewer(item) {
  if (!item?.url) return toast("该附件暂时无法预览", "error");
  const kind = previewKind(item), canvas = $("attachmentViewerCanvas");
  state.attachmentViewerItem = item;
  $("attachmentViewerTitle").textContent = item.name || "查看原文件";
  if (kind === "image") canvas.innerHTML = `<img class="attachment-viewer-content image" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}">`;
  else if (["pdf", "text"].includes(kind)) canvas.innerHTML = `<iframe class="attachment-viewer-content frame" src="${escapeHtml(item.url)}" title="${escapeHtml(item.name)}"></iframe>`;
  else canvas.innerHTML = `<div class="empty-state attachment-viewer-content">该格式不能在窗口内直接显示，请点击“打开原文件”。</div>`;
  applyAttachmentZoom(1); $("attachmentViewerDialog").showModal();
}

async function openLocalAttachment(attachmentId = "") {
  const id = attachmentId || state.attachmentViewerItem?.attachmentId || "";
  if (!id) {
    if (state.attachmentViewerItem?.url) window.open(state.attachmentViewerItem.url, "_blank", "noopener");
    return;
  }
  try { await api(`/api/attachments/${encodeURIComponent(id)}/open`, { method: "POST" }); toast("已使用电脑默认程序打开源文件"); }
  catch (error) { toast(error.message, "error", 6000); }
}

async function saveBlob(blob, suggestedName, pickerTypes = []) {
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({ suggestedName, types: pickerTypes });
      const writable = await handle.createWritable(); await writable.write(blob); await writable.close(); return true;
    } catch (error) { if (error?.name === "AbortError") return false; }
  }
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = suggestedName; link.style.display = "none"; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000); return true;
}

async function downloadCsv(ids = []) {
  if (ids.length === 0 && state.invoices.length === 0) return toast("当前没有可导出的发票", "error");
  const suggestedName = `燕翔车队发票_${nowDate()}.csv`;
  let fileHandle = null;
  if (window.showSaveFilePicker) {
    try {
      fileHandle = await window.showSaveFilePicker({ suggestedName, types: [{ description: "CSV 表格", accept: { "text/csv": [".csv"] } }] });
    } catch (error) {
      if (error?.name === "AbortError") return;
    }
  }
  loading(true, "正在生成 CSV", 10, "正在汇总发票数量与金额");
  try {
    const options = ids.length ? {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
      body: JSON.stringify({ ids }),
    } : { credentials: "same-origin" };
    const path = ids.length ? "/api/export/csv" : `/api/export/csv?${invoiceFilterParams()}`;
    const response = await fetch(path, options);
    if (response.status === 401) { showLogin(); throw new Error("登录已失效，请重新登录"); }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `CSV 导出失败 (${response.status})`);
    }
    setLoadingProgress(72, "正在写入 CSV 文件");
    const blob = await response.blob();
    if (fileHandle) {
      const writable = await fileHandle.createWritable(); await writable.write(blob); await writable.close();
    } else {
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = suggestedName; link.style.display = "none";
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
    const count = Number(response.headers.get("X-Export-Count") || ids.length || state.invoices.length);
    const total = Number(response.headers.get("X-Export-Total") || selectedInvoices().reduce((sum, item) => sum + Number(item.total_amount || 0), 0));
    toast(`CSV 导出成功：${count} 张发票，合计 ${money(total)}`, "success", 6000);
  } catch (error) { toast(error.message, "error", 6000); }
  finally { loading(false); }
}

function openPdfExportDialog() {
  const selected = selectedInvoices(), items = selected.length ? selected : state.invoices;
  if (!items.length) return toast("当前没有可导出的发票", "error");
  const scope = selected.length ? `已勾选 ${selected.length} 张发票` : `当前筛选结果 ${items.length} 张发票`;
  $("pdfExportSummary").textContent = `${scope}，合计 ${money(items.reduce((sum, item) => sum + Number(item.total_amount || 0), 0))}。没有原附件的记录会在完成提示中列为跳过。`;
  $("pdfIncludeSupporting").checked = false;
  $("pdfExportDialog").showModal();
}

async function downloadPdfExport(event) {
  event.preventDefault();
  const mode = document.querySelector('input[name="pdfExportMode"]:checked')?.value || "separate";
  const includeSupporting = $("pdfIncludeSupporting").checked;
  const extension = mode === "merged" && !includeSupporting ? ".pdf" : ".zip";
  const suggestedName = `燕翔车队发票${includeSupporting ? "及证明" : ""}_${mode === "merged" ? "合并" : "逐张"}_${nowDate()}${extension}`;
  let fileHandle = null;
  if (window.showSaveFilePicker) {
    try { fileHandle = await window.showSaveFilePicker({ suggestedName, types: [{ description: extension === ".pdf" ? "PDF 文件" : "ZIP 压缩包", accept: { [extension === ".pdf" ? "application/pdf" : "application/zip"]: [extension] } }] }); }
    catch (error) { if (error?.name === "AbortError") return; }
  }
  const ids = [...state.selectedInvoiceIds];
  const body = { mode, include_supporting: includeSupporting, ...(ids.length ? { ids } : Object.fromEntries(invoiceFilterParams())) };
  $("pdfExportDialog").close(); loading(true, "正在保持原画质生成 PDF", 4, ids.length ? `处理已选择的 ${ids.length} 张发票` : `处理当前筛选的 ${state.invoices.length} 张发票`, true);
  try {
    const response = await fetch("/api/export/pdf", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf }, body: JSON.stringify(body) });
    if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail || `PDF 导出失败 (${response.status})`); }
    setLoadingProgress(90, "正在写入导出文件"); const blob = await response.blob();
    if (fileHandle) { const writable = await fileHandle.createWritable(); await writable.write(blob); await writable.close(); }
    else await saveBlob(blob, suggestedName);
    const count = Number(response.headers.get("X-Export-Count") || 0), skipped = Number(response.headers.get("X-Export-Skipped") || 0), total = Number(response.headers.get("X-Export-Total") || 0);
    toast(`PDF 导出成功：${count} 张，合计 ${money(total)}${skipped ? `；有 ${skipped} 项进入故障追溯` : ""}`, skipped ? "error" : "success", 7500);
    await loadQualityIssues(true);
  } catch (error) { toast(error.message, "error", 7000); }
  finally { loading(false); }
}

async function downloadBackup() {
  const suggestedName = `燕翔车队经费完整备份_${nowDate()}.zip`;
  let fileHandle = null;
  if (window.showSaveFilePicker) {
    try { fileHandle = await window.showSaveFilePicker({ suggestedName, types: [{ description: "完整备份 ZIP", accept: { "application/zip": [".zip"] } }] }); }
    catch (error) { if (error?.name === "AbortError") return; }
  }
  if (!fileHandle && window.pywebview?.api?.choose_backup_path) {
    try {
      const targetPath = await window.pywebview.api.choose_backup_path(suggestedName);
      if (!targetPath) return;
      loading(true, "正在打包数据库和全部附件", 3, "备份过程不会修改现有数据", true);
      const result = await api("/api/admin/backup/save", { method: "POST", body: { target_path: targetPath } });
      toast(`完整备份已保存：${(Number(result.size || 0) / 1024 / 1024).toFixed(1)} MB`, "success", 6500);
    } catch (error) { toast(error.message, "error", 7000); }
    finally { loading(false); }
    return;
  }
  loading(true, "正在打包数据库和全部附件", 3, "备份过程不会修改现有数据", true);
  try {
    const response = await fetch("/api/admin/backup", { credentials: "same-origin" });
    if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail || `备份生成失败 (${response.status})`); }
    setLoadingProgress(88, "正在写入完整备份"); const blob = await response.blob();
    if (fileHandle) { const writable = await fileHandle.createWritable(); await writable.write(blob); await writable.close(); }
    else await saveBlob(blob, suggestedName);
    toast(`完整备份已保存：${(blob.size / 1024 / 1024).toFixed(1)} MB`, "success", 6500);
  } catch (error) { toast(error.message, "error", 7000); }
  finally { loading(false); }
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
  const defaults = state.settings.invoice_defaults || {};
  const preferredPayer = activeMembers().some((item) => item.id === defaults.payer_member_id) ? defaults.payer_member_id : (state.user.member_id && state.members.some((item) => item.id === state.user.member_id && item.active) ? state.user.member_id : state.members.find((item) => item.active)?.id);
  $("invoicePayer").value = preferredPayer || ""; $("fileState").textContent = "尚未选择附件"; $("runOcrBtn").disabled = true;
  $("ocrMessage").textContent = "电子 PDF 将优先直接读取文字"; $("ocrProgressBar").style.width = "0";
  $("classificationHint").textContent = ""; $("classificationHint").classList.add("hidden");
  $("invoiceMetadata").textContent = ""; $("invoiceMetadata").classList.add("hidden");
  $("invoiceCategory").value = defaults.category_id || ""; $("invoiceSource").value = defaults.funding_source_id || "";
  const defaultBurden = ["team_aa", "specified_split", "self_paid"].includes(defaults.burden_type) ? defaults.burden_type : "team_aa";
  document.querySelector(`input[name="burdenType"][value="${defaultBurden}"]`).checked = true; $("splitMode").value = "equal";
  const defaultSplits = defaultBurden === "specified_split" ? (defaults.split_member_ids || []) : activeMembers().map((item) => item.id);
  renderSplitMembers(defaultSplits); setCurrentInvoicePreview(null); updateReimbursementStatus();
  $("invoiceDialogTitle").textContent = "新增发票记录"; setInvoiceReadonly(false);
  state.supportingAttachments = []; state.lastOcrResult = null; renderSupportingAttachments(); markUncertainFields([]);
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
  if (item.attachment_id) setCurrentInvoicePreview({ name: item.attachment_name || "发票附件", mime: item.attachment_mime || "", attachmentId: item.attachment_id, url: `/api/attachments/${encodeURIComponent(item.attachment_id)}/content?inline=1` });
  $("invoiceMetadata").innerHTML = `<span><b>开票日期</b>${escapeHtml(dateText(item.invoice_date))}</span><span><b>上传日期</b>${escapeHtml(dateText(item.uploaded_at || item.created_at))}</span><span><b>提交人</b>${escapeHtml(item.created_by_name || "系统任务")}${item.created_by_username ? `（${escapeHtml(item.created_by_username)}）` : ""}</span>`;
  $("invoiceMetadata").classList.remove("hidden");
  state.supportingAttachments = item.supporting_attachments || []; renderSupportingAttachments();
  const lowIssue = state.qualityIssues.find((issue) => issue.invoice_id === item.id && issue.issue_type === "low_confidence");
  markUncertainFields(lowIssue?.details?.uncertain_fields || []);
  $("invoiceDialogTitle").textContent = readonly ? "查看发票记录" : "编辑发票记录"; updateReimbursementStatus(); setInvoiceReadonly(readonly); $("invoiceDialog").showModal();
}

const UNCERTAIN_FIELD_IDS = { invoice_no: "invoiceNo", vendor: "invoiceVendor", invoice_date: "invoiceDate", total_amount: "invoiceAmount", tax_amount: "invoiceTax" };
function markUncertainFields(fields = []) {
  Object.values(UNCERTAIN_FIELD_IDS).forEach((id) => $(id)?.classList.remove("ocr-uncertain"));
  fields.forEach((field) => $(UNCERTAIN_FIELD_IDS[field])?.classList.add("ocr-uncertain"));
}

function renderSupportingAttachments() {
  const items = state.supportingAttachments || [];
  $("supportingAttachmentList").innerHTML = items.map((item) => `<div class="supporting-file-item"><div><b>${escapeHtml(item.label || item.original_name)}</b><small>${escapeHtml({ shopping: "购物证明", signature: "签字证明", other: "其他证明" }[item.attachment_kind] || "证明附件")} · ${escapeHtml(item.original_name)}</small></div><button class="row-action" type="button" data-support-open="${escapeHtml(item.attachment_id)}">打开</button>${canWrite() ? `<button class="row-action delete" type="button" data-support-delete="${escapeHtml(item.relation_id)}">×</button>` : ""}</div>`).join("") || `<div class="empty-state">${$("invoiceId").value ? "尚未添加证明附件" : "保存发票后可以添加证明附件"}</div>`;
}

async function uploadSupportingAttachments(files) {
  const invoiceId = $("invoiceId").value;
  if (!invoiceId) { $("supportingAttachmentFiles").value = ""; return toast("请先保存发票记录，再添加证明附件", "error"); }
  const selected = [...(files || [])]; if (!selected.length) return;
  loading(true, "正在添加证明附件", 2, `共 ${selected.length} 个文件`, false);
  let success = 0;
  for (let index = 0; index < selected.length; index++) {
    const form = new FormData(); form.append("file", selected[index]); form.append("attachment_kind", $("supportingAttachmentKind").value); form.append("label", selected[index].name);
    try { await api(`/api/invoices/${encodeURIComponent(invoiceId)}/supporting-attachments`, { method: "POST", body: form }); success += 1; }
    catch (error) { toast(`${selected[index].name}：${error.message}`, "error", 6000); }
    setLoadingProgress(Math.round((index + 1) / selected.length * 100), "正在添加证明附件", `已完成 ${index + 1}/${selected.length}`);
  }
  const refreshed = await api(`/api/invoices/${encodeURIComponent(invoiceId)}`); state.supportingAttachments = refreshed.supporting_attachments || []; renderSupportingAttachments();
  $("supportingAttachmentFiles").value = ""; loading(false); toast(`证明附件已添加：${success}/${selected.length}`, success === selected.length ? "success" : "error");
}

async function deleteSupportingAttachment(relationId) {
  const invoiceId = $("invoiceId").value; if (!invoiceId) return;
  try { await api(`/api/invoices/${encodeURIComponent(invoiceId)}/supporting-attachments/${encodeURIComponent(relationId)}`, { method: "DELETE" }); state.supportingAttachments = state.supportingAttachments.filter((item) => item.relation_id !== relationId); renderSupportingAttachments(); toast("证明附件已移除"); }
  catch (error) { toast(error.message, "error"); }
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
    uncertain_fields: state.lastOcrResult?.uncertain_fields || [], field_confidences: state.lastOcrResult?.field_confidences || {},
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
    setCurrentInvoicePreview({ name: data.attachment.original_name, mime: data.attachment.mime_type || file.type, attachmentId: data.attachment.id, url: `/api/attachments/${encodeURIComponent(data.attachment.id)}/content?inline=1` });
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
  state.lastOcrResult = result;
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
  markUncertainFields(result.uncertain_fields || []);
  if ((result.uncertain_fields || []).length) toast(`有 ${result.uncertain_fields.length} 个识别字段不确定，已闪亮标注`, "error", 6500);
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
  const item = state.invoices.find((value) => value.id === id);
  if (!await confirmAction(`即将删除“${item?.vendor || "该发票"}”。系统会先自动建立回溯版本，之后可由管理员恢复。`, { title: "删除发票记录", confirmText: "删除", tone: "danger" })) return;
  try { await api(`/api/invoices/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("发票记录已删除"); await loadInvoices(); }
  catch (error) { toast(error.message, "error"); }
}

function updateBatchActionVisibility() {
  const statusMode = $("batchActionType").value === "status";
  $("batchCategoryActionWrap").classList.toggle("hidden", statusMode);
  $("batchStatusActionWrap").classList.toggle("hidden", !statusMode);
  const partial = statusMode && $("batchActionStatus").value === "partial";
  $("batchRatioWrap").classList.toggle("hidden", !partial);
  $("batchRatioValue").textContent = `${$("batchActionRatio").value}%`;
}

function openBatchActionDialog() {
  const selected = selectedInvoices(); if (!selected.length) return toast("请先勾选发票", "error");
  const categoryOptions = state.categories.filter((item) => item.active && !item.deleted_at).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  $("batchActionCategory").innerHTML = `<option value="">未分类</option>${categoryOptions}`;
  $("batchActionType").value = "category"; $("batchActionStatus").value = "pending"; $("batchActionRatio").value = "50"; $("batchActionDate").value = nowDate();
  $("batchActionSummary").textContent = `将修改 ${selected.length} 张发票，合计 ${money(selected.reduce((sum, item) => sum + Number(item.total_amount || 0), 0))}。修改前会自动建立回溯版本。`;
  updateBatchActionVisibility(); $("batchActionDialog").showModal();
}

async function submitBatchAction(event) {
  event.preventDefault(); const ids = [...state.selectedInvoiceIds]; if (!ids.length) return;
  const action = $("batchActionType").value;
  const body = action === "category"
    ? { ids, action, category_id: $("batchActionCategory").value }
    : { ids, action, status: $("batchActionStatus").value, reimbursement_ratio: Number($("batchActionRatio").value), reimbursement_date: $("batchActionDate").value };
  loading(true, "正在批量修改发票", 5, `共 ${ids.length} 张发票`, false);
  try {
    setLoadingProgress(35, "正在建立回溯版本");
    const result = await api("/api/invoices/batch-action", { method: "POST", body });
    setLoadingProgress(92, "正在刷新发票台账");
    $("batchActionDialog").close(); clearInvoiceSelection(false); await loadInvoices();
    toast(`批量修改完成：成功 ${result.changed_count} 张${result.skipped_count ? `，跳过 ${result.skipped_count} 张` : ""}`, "success", 5000);
  } catch (error) { toast(error.message, "error", 6000); }
  finally { loading(false); }
}

async function deleteSelectedInvoices() {
  const selected = selectedInvoices(); if (!selected.length) return;
  const total = selected.reduce((sum, item) => sum + Number(item.total_amount || 0), 0);
  const accepted = await confirmAction(`即将删除 ${selected.length} 张发票，合计 ${money(total)}。系统会先建立回溯版本，管理员之后仍可恢复。`, { title: "批量删除发票", confirmText: `删除 ${selected.length} 张`, tone: "danger" });
  if (!accepted) return;
  loading(true, "正在批量删除发票", 8, `共 ${selected.length} 张发票`, false);
  try {
    const result = await api("/api/invoices/batch-action", { method: "POST", body: { ids: selected.map((item) => item.id), action: "delete" } });
    setLoadingProgress(92, "正在刷新发票台账"); clearInvoiceSelection(false); await loadInvoices();
    toast(`已删除 ${result.changed_count} 张发票，合计 ${money(result.total_amount)}`, "success", 5000);
  } catch (error) { toast(error.message, "error", 6000); }
  finally { loading(false); }
}

function openBatchImport() {
  $("batchForm").reset(); $("batchResult").textContent = ""; clearBatchPreviews();
  state.batchReviewIds = []; $("batchReviewList").innerHTML = ""; $("batchReviewList").classList.add("hidden");
  $("batchFilePreview").innerHTML = `<div class="empty-state">选择文件后将在这里显示缩略图，点击可放大查看原文件。</div>`;
  renderReferenceOptions();
  const defaults = state.settings.invoice_defaults || {};
  $("batchCategory").value = defaults.category_id || "";
  $("batchPayer").value = activeMembers().some((item) => item.id === defaults.payer_member_id) ? defaults.payer_member_id : (state.user.member_id || activeMembers()[0]?.id || "");
  $("batchSource").value = defaults.funding_source_id || ""; $("batchBurden").value = defaults.burden_type || "team_aa"; $("batchNote").value = defaults.batch_note || "";
  renderSimpleMemberPicker("batchSplitMembers", defaults.split_member_ids?.length ? defaults.split_member_ids : activeMembers().map((item) => item.id));
  updateBatchSplitVisibility(); $("batchDialog").showModal();
}

async function submitBatch(event) {
  event.preventDefault(); const files = [...$("batchFile").files]; if (!files.length) return;
  const zipFiles = files.filter((file) => file.name.toLowerCase().endsWith(".zip"));
  if (zipFiles.length && (zipFiles.length !== 1 || files.length !== 1)) return toast("ZIP 压缩包不能与其他文件同时导入，请分成两次操作", "error", 5000);
  const form = new FormData();
  if (zipFiles.length) form.append("file", zipFiles[0]); else files.forEach((file) => form.append("files", file));
  form.append("category_id", $("batchCategory").value); form.append("payer_member_id", $("batchPayer").value);
  form.append("funding_source_id", $("batchSource").value); form.append("burden_type", $("batchBurden").value); form.append("note", $("batchNote").value);
  const splitIds = $("batchBurden").value === "specified_split" ? simpleMemberIds("batchSplitMembers") : activeMembers().map((item) => item.id);
  if ($("batchBurden").value === "specified_split" && !splitIds.length) return toast("指定成员均摊时，请至少勾选一名成员", "error");
  form.append("split_member_ids", JSON.stringify(splitIds));
  loading(true, "正在上传发票文件", 1, `已选择 ${files.length} 个文件`, false);
  let completed = false;
  try {
    const endpoint = zipFiles.length ? "/api/import/zip" : "/api/import/files";
    const result = await apiUpload(endpoint, form, (progress) => setLoadingProgress(Math.max(1, Math.round(progress * .34)), "正在上传发票文件", `上传进度 ${progress}%`));
    state.batchReviewIds = (result.drafts || []).map((item) => item.id).filter(Boolean);
    setLoadingProgress(40, zipFiles.length ? "压缩包已解压，正在建立识别队列" : "文件已保存，正在建立识别队列", `已创建 ${result.count || 0} 张发票草稿`);
    $("batchResult").textContent = `已导入 ${result.count} 个文件；${(result.skipped || []).length} 个文件被跳过。正在离线识别金额与分类……`;
    await loadInvoices();
    await pollBatchJobs(result.jobs || [], { imported: Number(result.count || 0), skipped: (result.skipped || []).length });
    completed = true;
  } catch (error) { $("batchResult").textContent = error.message; toast(error.message, "error", 5000); }
  finally { if (!completed) loading(false); }
}

async function pollBatchJobs(jobs, summary = {}) {
  const jobIds = jobs.map((item) => typeof item === "string" ? item : item.job_id).filter(Boolean);
  if (!jobIds.length) {
    setLoadingProgress(100, "批量导入完成", "没有需要识别的文件");
    $("batchResult").textContent = `批量导入完成：${summary.imported || 0} 张发票，合计 ${money(0)}。`;
    toast($("batchResult").textContent, "success", 6500); loading(false); return;
  }
  const finished = new Map();
  for (let round = 0; round < 900 && finished.size < jobIds.length; round++) {
    await new Promise((resolve) => setTimeout(resolve, round < 8 ? 700 : 1500));
    const pending = jobIds.filter((id) => !finished.has(id));
    const results = await Promise.all(pending.map((id) => api(`/api/ocr/jobs/${encodeURIComponent(id)}`).catch((error) => ({ status: "failed", error: error.message }))));
    results.forEach((job, index) => { if (["done", "failed"].includes(job.status)) finished.set(pending[index], job); });
    const doneCount = [...finished.values()].filter((job) => job.status === "done").length;
    const progress = 40 + Math.round(finished.size / jobIds.length * 59);
    setLoadingProgress(progress, "正在离线识别发票", `已完成 ${finished.size}/${jobIds.length} · 成功 ${doneCount} 张`);
    $("batchResult").textContent = `OCR 处理中：已完成 ${finished.size}/${jobIds.length}`;
  }
  const completedJobs = [...finished.values()].filter((job) => job.status === "done");
  const failedCount = jobIds.length - completedJobs.length;
  const total = completedJobs.reduce((sum, job) => sum + Number(job.result?.total_amount || 0), 0);
  setLoadingProgress(100, "批量导入完成", `成功 ${completedJobs.length} 张 · 合计 ${money(total)}`);
  const resultText = `批量导入完成：导入 ${summary.imported || jobIds.length} 张，识别成功 ${completedJobs.length} 张${failedCount ? `，失败 ${failedCount} 张` : ""}${summary.skipped ? `，跳过 ${summary.skipped} 个文件` : ""}，合计 ${money(total)}。`;
  $("batchResult").textContent = `${resultText} 请检查低置信度记录。`;
  toast(resultText, failedCount ? "error" : "success", 8000);
  if (state.currentView === "invoices") await loadInvoices();
  await loadBatchReview(); await loadQualityIssues(true);
  loading(false);
}

async function loadBatchReview() {
  const ids = state.batchReviewIds || [];
  if (!ids.length) { $("batchReviewList").classList.add("hidden"); return; }
  const items = (await Promise.all(ids.map((id) => api(`/api/invoices/${encodeURIComponent(id)}`).catch(() => null)))).filter(Boolean);
  $("batchReviewList").innerHTML = items.map((item) => `<article class="batch-review-card ${invoiceQualityClass(item.id)}"><div><b>${escapeHtml(item.vendor || "待补充商家")} · ${money(item.total_amount)}</b><small>${escapeHtml(item.invoice_no || "无发票号码")} · ${escapeHtml(dateText(item.invoice_date))}</small></div><div><b>${escapeHtml(item.category_name || "未分类")}</b><small>费用分类</small></div><div><b>${escapeHtml(item.funding_source_name || "未选择")}</b><small>资金来源</small></div><button class="btn secondary" type="button" data-batch-review="${escapeHtml(item.id)}">逐张校对</button></article>`).join("");
  $("batchReviewList").classList.remove("hidden");
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
  if (!await confirmAction("删除后将重新计算成员结算结果。", { title: "删除还款记录", confirmText: "删除", tone: "danger" })) return;
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
  $("memberCards").innerHTML = state.members.filter((item) => !item.deleted_at).map((item) => {
    const avatar = item.avatar_url ? `<span class="member-avatar has-photo"><img src="${escapeHtml(item.avatar_url)}" alt="${escapeHtml(item.name)}头像"></span>` : `<span class="member-avatar">${escapeHtml(item.name.slice(0, 1))}</span>`;
    const canAvatar = canWrite() && (isAdmin() || state.user.member_id === item.id);
    const controls = `${canAvatar ? `<button class="row-action" data-member-avatar="${escapeHtml(item.id)}" title="更换头像">图</button>` : ""}${isAdmin() && canWrite() ? `<button class="row-action" data-member-edit="${escapeHtml(item.id)}">✎</button>${item.active ? `<button class="row-action delete" data-member-archive="${escapeHtml(item.id)}">×</button>` : ""}` : ""}`;
    return `<article class="member-card ${item.active ? "" : "inactive"}" style="--member-color:${escapeHtml(item.avatar_color)}"><header>${avatar}<div><h4>${escapeHtml(item.name)}</h4><small>${escapeHtml(item.department || "未分组")}</small></div></header><p>${item.student_id ? `学号 ${escapeHtml(item.student_id)}<br>` : ""}${item.phone ? `电话 ${escapeHtml(item.phone)}<br>` : ""}${item.active ? "有效成员" : "已停用，历史数据保留"}</p>${controls ? `<footer>${controls}</footer>` : ""}</article>`;
  }).join("");
}

function renderUsers() {
  $("userTable").innerHTML = state.users.map((item) => `<tr><td><span class="cell-main">${escapeHtml(item.username)}</span></td><td>${escapeHtml(item.display_name)}</td><td>${escapeHtml(item.member_name || "-")}</td><td>${escapeHtml(roleLabel(item.role))}</td><td>${item.active ? `<span class="status-tag reimbursed">启用</span>` : `<span class="status-tag pending">停用</span>`}</td><td>${canWrite() ? `<button class="row-action" data-user-edit="${escapeHtml(item.id)}">✎</button>` : "只读"}</td></tr>`).join("") || emptyRow(6, "暂无账号");
}

function openMember(item = null) {
  $("memberForm").reset(); $("memberId").value = item?.id || ""; $("memberName").value = item?.name || ""; $("memberDepartment").value = item?.department || "";
  $("memberStudentId").value = item?.student_id || ""; $("memberPhone").value = item?.phone || ""; $("memberEmail").value = item?.email || ""; $("memberColor").value = item?.avatar_color || "#27d3ff";
  $("memberAvatarFile").value = "";
  $("memberDialogTitle").textContent = item ? "编辑成员" : "新增成员"; $("memberDialog").showModal();
}

async function saveMember(event) {
  event.preventDefault(); const id = $("memberId").value;
  const payload = { name: $("memberName").value, department: $("memberDepartment").value, student_id: $("memberStudentId").value, phone: $("memberPhone").value, email: $("memberEmail").value, avatar_color: $("memberColor").value, active: true };
  try { const saved = await api(id ? `/api/members/${encodeURIComponent(id)}` : "/api/members", { method: id ? "PUT" : "POST", body: payload }); const avatarFile = $("memberAvatarFile").files[0]; if (avatarFile) await uploadMemberAvatar(saved.id || id, avatarFile); $("memberDialog").close(); toast("成员信息已保存"); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
}

async function uploadMemberAvatar(memberId, file) {
  const form = new FormData(); form.append("file", file);
  return api(`/api/members/${encodeURIComponent(memberId)}/avatar`, { method: "POST", body: form });
}

function chooseMemberAvatar(memberId) {
  const input = document.createElement("input"); input.type = "file"; input.accept = "image/*,.gif";
  input.addEventListener("change", async () => { if (!input.files[0]) return; try { await uploadMemberAvatar(memberId, input.files[0]); toast("头像已更新"); await refreshCurrentView(true); } catch (error) { toast(error.message, "error"); } });
  input.click();
}

async function archiveMember(id) {
  const item = state.members.find((value) => value.id === id);
  if (!await confirmAction(`成员“${item?.name}”及其关联账号将被停用，历史账目仍会保留。`, { title: "停用成员", confirmText: "确认停用", tone: "warning" })) return;
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

async function loadCreators() {
  const result = await api("/api/creators"); state.creators = result.items || []; renderCreators();
}

function renderCreators() {
  $("creatorSeasonHeading").innerHTML = `<b>全赛季共享</b><span>${state.creators.length} 位创作者，新赛季无需重复添加</span>`;
  $("creatorCards").innerHTML = state.creators.map((item) => `<article class="creator-card${item.active ? "" : " inactive"}">
    <div class="creator-meta"><span>全赛季通用</span><span>${escapeHtml(item.department || "未填写组别")}</span><span>${escapeHtml(item.role_title || "创作者")}</span>${item.active ? "" : "<span>已隐藏</span>"}</div>
    <h4>${escapeHtml(item.name)}</h4><p>${escapeHtml(item.note || "参与软件建设与维护")}</p>
    ${isAdmin() ? `<footer><button class="row-action" data-creator-edit="${escapeHtml(item.id)}" title="编辑创作者">✎</button></footer>` : ""}
  </article>`).join("") || `<div class="empty-state">尚未添加全赛季创作者名单</div>`;
  $("referenceNotice").textContent = "引用内容仅用于燕翔车队内部发票报销与非商业技术学习；未复制外部网站媒体，图片和音乐只使用用户自有、授权或许可证明确素材。";
  $("openSourceReferences").innerHTML = (state.openSourceReferences || []).map((item) => `<article class="reference-credit-item"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.name)}</a><small>${escapeHtml(item.license)} · ${escapeHtml(item.use)}</small></article>`).join("");
}

async function loadSeasons() {
  const result = await api("/api/seasons"); state.seasons = result.items || []; return state.seasons;
}

function renderDepartmentOptions() {
  $("departmentOptions").innerHTML = state.departments.map((item) => `<option value="${escapeHtml(item.name)}"></option>`).join("");
  $("departmentManagerList").innerHTML = state.departments.map((item) => `<span class="department-chip">${escapeHtml(item.name)}</span>`).join("") || `<div class="empty-state">暂无长期组别</div>`;
}

function renderSeasonManager() {
  $("seasonManagerList").innerHTML = state.seasons.map((item) => `<article class="season-manager-item${item.is_current ? " current" : ""}${item.is_open ? "" : " archived"}">
    <div><h4>${escapeHtml(item.name)} ${item.is_current ? "· 当前查看" : ""}</h4><p>${item.member_count || 0} 名成员 · ${item.invoice_count || 0} 张发票 · ${item.creator_count || 0} 位创作者 · ${item.is_open ? "进行中" : "已归档，只读"}</p></div>
    <div class="season-actions">
      ${item.is_current ? "" : `<button type="button" class="btn secondary" data-season-switch="${escapeHtml(item.id)}">查看 / 切换</button>`}
      <button type="button" class="btn secondary" data-season-rename="${escapeHtml(item.id)}">重命名</button>
      ${item.is_current ? "" : `<button type="button" class="btn ${item.is_open ? "danger subtle" : "secondary"}" data-season-toggle="${escapeHtml(item.id)}">${item.is_open ? "归档" : "重新启用"}</button>`}
    </div>
  </article>`).join("") || `<div class="empty-state">暂无赛季</div>`;
  renderDepartmentOptions();
}

async function openSeasonManager() {
  if (!isAdmin()) return toast(`当前赛季：${state.season?.name || "未设置"}`);
  try { await loadSeasons(); renderSeasonManager(); $("seasonManagerDialog").showModal(); }
  catch (error) { toast(error.message, "error"); }
}

async function createSeason(event) {
  event.preventDefault();
  try {
    await api("/api/admin/seasons", { method: "POST", body: { name: $("seasonNameInput").value } });
    $("seasonCreateForm").reset(); await loadSeasons(); renderSeasonManager(); toast("新赛季已创建，成员与成员账号为空");
  } catch (error) { toast(error.message, "error"); }
}

async function switchSeason(item) {
  if (!item) return;
  const message = item.is_open
    ? `切换到“${item.name}”后，只显示该赛季的账目、成员和成员账号。`
    : `“${item.name}”已归档，切换后只能查看，不能修改账目、成员或结算记录。`;
  if (!await confirmAction(message, { title: "切换赛季", confirmText: "确认切换", tone: "info" })) return;
  try {
    await api(`/api/admin/seasons/${encodeURIComponent(item.id)}/switch`, { method: "POST" });
    $("seasonManagerDialog").close(); clearInvoiceSelection(); await refreshCurrentView(true); await navigate("dashboard");
    toast(`已切换到${item.name}${item.is_open ? "" : "（只读）"}`, "success", 5000);
  } catch (error) { toast(error.message, "error"); }
}

async function renameSeason(item) {
  if (!item) return;
  const result = await showDecision({ title: "重命名赛季", message: "修改只影响赛季显示名称，不会改变历史账目。", confirmText: "保存名称", tone: "info", eyebrow: "赛季设置", inputLabel: "赛季名称", inputValue: item.name });
  if (typeof result !== "string" || !result.trim()) return;
  try { await api(`/api/admin/seasons/${encodeURIComponent(item.id)}`, { method: "PUT", body: { name: result.trim(), active: item.is_open } }); await loadSeasons(); renderSeasonManager(); if (item.is_current) await refreshCurrentView(true); toast("赛季名称已更新"); }
  catch (error) { toast(error.message, "error"); }
}

async function toggleSeason(item) {
  if (!item || item.is_current) return;
  const nextOpen = !item.is_open;
  if (!nextOpen && !await confirmAction(`归档“${item.name}”后仍可切换查看，但所有赛季业务数据为只读。`, { title: "归档历史赛季", confirmText: "确认归档", tone: "warning" })) return;
  try { await api(`/api/admin/seasons/${encodeURIComponent(item.id)}`, { method: "PUT", body: { name: item.name, active: nextOpen } }); await loadSeasons(); renderSeasonManager(); toast(nextOpen ? "赛季已重新启用" : "赛季已归档"); }
  catch (error) { toast(error.message, "error"); }
}

async function createDepartment(event) {
  event.preventDefault();
  try {
    const item = await api("/api/admin/departments", { method: "POST", body: { name: $("departmentNameInput").value } });
    if (!state.departments.some((entry) => entry.id === item.id)) state.departments.push(item);
    state.departments.sort((left, right) => String(left.name).localeCompare(String(right.name), "zh-CN"));
    $("departmentCreateForm").reset(); renderDepartmentOptions(); toast("长期组别已保存");
  } catch (error) { toast(error.message, "error"); }
}

async function openCreator(item = null) {
  if (!isAdmin()) return;
  $("creatorForm").reset(); $("creatorId").value = item?.id || "";
  $("creatorName").value = item?.name || ""; $("creatorDepartment").value = item?.department || "";
  $("creatorRole").value = item?.role_title || ""; $("creatorNote").value = item?.note || "";
  $("creatorActive").checked = item ? Boolean(item.active) : true;
  $("creatorDialogTitle").textContent = item ? "编辑创作者" : "添加创作者";
  $("deleteCreatorBtn").classList.toggle("hidden", !item); $("creatorDialog").showModal();
}

async function saveCreator(event) {
  event.preventDefault(); const id = $("creatorId").value;
  const payload = { name: $("creatorName").value, department: $("creatorDepartment").value, role_title: $("creatorRole").value, note: $("creatorNote").value, active: $("creatorActive").checked };
  try {
    await api(id ? `/api/admin/creators/${encodeURIComponent(id)}` : "/api/admin/creators", { method: id ? "PUT" : "POST", body: payload });
    $("creatorDialog").close(); await refreshCurrentView(true); if (state.currentView === "creators") renderCreators(); toast("创作者名单已保存");
  } catch (error) { toast(error.message, "error"); }
}

async function deleteCreator() {
  const id = $("creatorId").value; if (!id) return;
  if (!await confirmAction(`确认删除创作者“${$("creatorName").value}”的署名记录？`, { title: "删除创作者", confirmText: "确认删除", tone: "danger" })) return;
  try { await api(`/api/admin/creators/${encodeURIComponent(id)}`, { method: "DELETE" }); $("creatorDialog").close(); await refreshCurrentView(true); renderCreators(); toast("创作者记录已删除"); }
  catch (error) { toast(error.message, "error"); }
}

async function loadHistory() {
  const [snapshots, logs] = await Promise.all([api("/api/admin/snapshots"), api("/api/audit-logs?limit=300")]);
  $("snapshotList").innerHTML = snapshots.items.map((item) => `<article class="version-item"><h4>${escapeHtml(item.label)}</h4><p>${escapeHtml(item.reason || "自动保护点")}<br><time>${escapeHtml(item.created_at.replace("T", " ").slice(0, 19))} · ${escapeHtml(item.created_by_name || "系统")}</time></p><button class="btn secondary" data-snapshot-restore="${escapeHtml(item.id)}">回溯</button></article>`).join("") || `<div class="empty-state">暂无历史版本</div>`;
  $("auditList").innerHTML = logs.items.map((item) => `<article class="audit-item"><h4>${escapeHtml(actionLabel(item.action))} · ${escapeHtml(item.entity_type)}</h4><p>${escapeHtml(item.user_name || "系统任务")} · ${escapeHtml(item.entity_id || "全局")}<br><time>${escapeHtml(item.created_at.replace("T", " ").slice(0, 19))}</time></p></article>`).join("") || `<div class="empty-state">暂无操作日志</div>`;
}

async function restoreSnapshot(id) {
  if (!await confirmAction("当前状态会先自动建立保护点，然后恢复所选版本；登录账号不会被回退。", { title: "回溯历史版本", confirmText: "开始回溯", tone: "warning" })) return;
  loading(true, "正在恢复历史版本");
  try { const result = await api(`/api/admin/snapshots/${encodeURIComponent(id)}/restore`, { method: "POST" }); toast(result.message); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
  finally { loading(false); }
}

async function createSnapshotManually() {
  const label = await requestText("创建手动版本", `手动版本 ${new Date().toLocaleString("zh-CN")}`); if (!label) return;
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

function renderLoadingCarEditor() {
  const cars = state.appearanceLoadingCars.length ? state.appearanceLoadingCars : DEFAULT_LOADING_CARS;
  $("loadingCarEditor").innerHTML = cars.map((car, index) => {
    const custom = Boolean(car.attachment_id);
    return `<article class="loading-car-card"><img src="${escapeHtml(appearanceMediaUrl(car))}" alt="${escapeHtml(car.title || "等待动画赛车")}"><span>${escapeHtml(car.title || `赛车 ${index + 1}`)}${custom ? "" : " · 内置"}</span>${custom ? `<button type="button" data-loading-car-remove="${index}" title="移除">×</button>` : ""}</article>`;
  }).join("");
}

function openAppearance() {
  $("appearanceForm").reset(); applyDisplayMode(savedDisplayMode(), false); $("teamNameSetting").value = state.settings.team_name || "燕翔车队 Racing Team";
  $("backgroundOverlay").value = Math.round(Number(state.settings.background_overlay || .82) * 100); $("overlayValue").textContent = `${$("backgroundOverlay").value}%`;
  $("loginPanelOpacity").value = Math.round(Number(state.settings.login_panel_opacity || .78) * 100); $("loginOpacityValue").textContent = `${$("loginPanelOpacity").value}%`;
  $("loginRandomEnabled").checked = Boolean(state.settings.login_random_enabled); $("globalBackgroundRandom").checked = Boolean(state.settings.global_background_random);
  $("accentColor").value = state.settings.accent_color || "#27d3ff";
  $("loginSlideshowEnabled").checked = Boolean(state.settings.login_slideshow_enabled);
  $("loginTransition").value = state.settings.login_transition || "fade";
  state.appearanceSlides = (state.settings.login_slides || []).map((slide) => ({ ...slide }));
  state.appearanceLoadingCars = (state.settings.loading_cars || DEFAULT_LOADING_CARS).map((car) => ({ ...car }));
  state.appearanceBackground = state.settings.background_media_id ? {
    attachment_id: state.settings.background_media_id,
    kind: state.settings.background_media_kind || "image",
    title: "当前系统背景",
    url: state.settings.background_media_url,
    private_url: state.settings.background_media_url,
  } : null;
  renderBackgroundPreview(); renderLoginSlideEditor(); renderLoadingCarEditor(); $("appearanceDialog").showModal();
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

async function addLoadingCarFiles(files) {
  const list = [...(files || [])].slice(0, 12); if (!list.length) return;
  const remaining = Math.max(0, 12 - state.appearanceLoadingCars.filter((item) => item.attachment_id).length);
  if (!remaining) { $("loadingCarFiles").value = ""; return toast("等待动画最多保存 12 辆赛车", "error"); }
  loading(true, `正在导入 ${Math.min(list.length, remaining)} 张赛车图片`);
  try {
    const media = await Promise.all(list.slice(0, remaining).map((file) => uploadAppearanceMedia(file)));
    if (state.appearanceLoadingCars.every((item) => !item.attachment_id)) state.appearanceLoadingCars = [];
    state.appearanceLoadingCars.push(...media.map((item) => ({ ...item, title: item.title || "自定义赛车" })));
    renderLoadingCarEditor(); toast(`已添加 ${media.length} 辆赛车，保存设置后生效`);
  } catch (error) { toast(error.message, "error", 5000); }
  finally { loading(false); $("loadingCarFiles").value = ""; }
}

function resetLoadingCars() {
  state.appearanceLoadingCars = DEFAULT_LOADING_CARS.map((item) => ({ ...item })); renderLoadingCarEditor();
  toast("已恢复两辆内置赛车，保存设置后生效");
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
      login_panel_opacity: Number($("loginPanelOpacity").value) / 100,
      login_random_enabled: $("loginRandomEnabled").checked,
      global_background_random: $("globalBackgroundRandom").checked,
      accent_color: $("accentColor").value,
      login_slideshow_enabled: $("loginSlideshowEnabled").checked,
      login_transition: $("loginTransition").value,
      login_slides: state.appearanceSlides.map((slide) => ({ id: slide.id, attachment_id: slide.attachment_id, title: slide.title, duration: Number(slide.duration || 8) })),
      loading_cars: state.appearanceLoadingCars.filter((car) => car.attachment_id).map((car) => ({ id: car.id, attachment_id: car.attachment_id, title: car.title })),
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
  const rule = state.classificationRules.find((item) => item.id === id);
  if (!rule || !await confirmAction(`规则“${rule.name}”将被删除，保存后不再参与发票分类。`, { title: "删除识别规则", confirmText: "删除规则", tone: "danger" })) return;
  state.classificationRules = state.classificationRules.filter((item) => item.id !== id);
  try { await persistClassificationRules("智能分类规则已删除"); resetClassificationEditor(); } catch (error) { toast(error.message, "error"); }
}

async function resetClassificationRules() {
  if (!await confirmAction("全部自定义分类规则将被系统默认规则替换。", { title: "恢复默认识别规则", confirmText: "恢复默认", tone: "warning" })) return;
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
  if (!file) return;
  if (!await confirmAction("恢复完整备份会替换当前账号、经费数据和设置。系统会先自动保存恢复前备份。", { title: "恢复完整备份", confirmText: "恢复备份", tone: "danger" })) { $("restoreBackupInput").value = ""; return; }
  const form = new FormData(); form.append("file", file); loading(true, "正在验证并恢复完整备份");
  try { const result = await api("/api/admin/restore-backup", { method: "POST", body: form }); toast(result.message); setTimeout(() => location.reload(), 1000); }
  catch (error) { toast(error.message, "error", 6000); }
  finally { loading(false); $("restoreBackupInput").value = ""; }
}

async function deleteDemo() {
  if (!await confirmAction("仅删除初始演示发票与演示还款，不影响之后录入的正式数据。", { title: "清除演示数据", confirmText: "确认清除", tone: "warning" })) return;
  try { const result = await api("/api/admin/demo-data", { method: "DELETE" }); toast(`已清除 ${result.deleted_count} 条演示记录`); await refreshCurrentView(true); }
  catch (error) { toast(error.message, "error"); }
}

function autoUpdateEnabled() {
  try { return localStorage.getItem(AUTO_UPDATE_STORAGE_KEY) !== "0"; } catch (_) { return true; }
}

function updateInstallMode() {
  try { return localStorage.getItem(UPDATE_INSTALL_MODE_KEY) === "manual" ? "manual" : "auto"; } catch (_) { return "auto"; }
}

function applyUpdateInstallMode(mode, notify = false) {
  const selected = mode === "manual" ? "manual" : "auto";
  try { localStorage.setItem(UPDATE_INSTALL_MODE_KEY, selected); } catch (_) { /* 当前会话仍可使用 */ }
  $("updateInstallMode").value = selected;
  $("updateModeHint").textContent = selected === "manual"
    ? "下载轻量补丁 ZIP，关闭软件后解压到原软件文件夹并覆盖；data、uploads、models 和 backups 不会包含在补丁中。"
    : "安装前自动备份全部赛季、人员、组别、账号、发票与附件，再校验补丁、重启并更新。";
  if (state.updateRelease) renderUpdateResult(state.updateRelease);
  if (notify) toast(selected === "manual" ? "已切换为手动补丁更新" : "已切换为自动备份并更新");
  return selected;
}

async function loadChangelog() {
  try {
    const response = await fetch(`/static/changelog.json?v=${encodeURIComponent(state.version)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("更新日志读取失败");
    const payload = await response.json();
    $("changelogList").innerHTML = (payload.entries || []).map((entry) => `<article class="changelog-entry"><header><h4>V${escapeHtml(entry.version)}</h4><time>${escapeHtml(entry.date || "")}</time></header><b>${escapeHtml(entry.title || "版本更新")}</b><ul>${(entry.changes || []).map((change) => `<li>${escapeHtml(change)}</li>`).join("")}</ul></article>`).join("") || `<div class="empty-state">暂无更新日志</div>`;
  } catch (error) { $("changelogList").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; }
}

function renderUpdateResult(result) {
  state.updateRelease = result;
  const status = $("updateStatus"), releaseLink = $("updateReleaseLink"), installButton = $("installUpdateBtn");
  const installMode = updateInstallMode();
  status.className = "update-status"; releaseLink.classList.add("hidden"); installButton.classList.add("hidden");
  $("updateCurrentVersion").textContent = `V${result.current_version || state.version}`;
  if (result.release_url) { releaseLink.href = result.release_url; releaseLink.classList.remove("hidden"); }
  if (result.available) {
    status.classList.add("available");
    if (installMode === "manual") {
      status.textContent = `发现最新版 V${result.latest_version}。可下载轻量补丁，关闭软件后解压覆盖原文件；业务数据不会打入补丁。`;
      if (result.package?.browser_url || result.release_url) {
        installButton.textContent = `下载 V${result.latest_version} 补丁包`; installButton.classList.remove("hidden");
      }
    } else {
      status.textContent = `发现最新版 V${result.latest_version}。软件会先建立完整备份，再替换程序并自动重启；人员、组别、账号、发票和附件均保留。`;
      if (result.install_supported) {
        installButton.textContent = `自动备份并更新到 V${result.latest_version}`; installButton.classList.remove("hidden");
      } else {
        status.textContent += " 当前为网页版/开发预览，请切换到手动补丁模式下载，或在 Windows 软件中执行自动更新。";
      }
    }
  } else {
    status.classList.add("good"); status.textContent = result.message || `当前 V${state.version} 已是最新版本。`;
  }
}

async function checkForUpdates(interactive = true) {
  const status = $("updateStatus");
  if (interactive) { status.className = "update-status"; status.textContent = "正在连接 GitHub 检查最新版本……"; }
  try {
    const result = await api("/api/update/check"); renderUpdateResult(result);
    if (interactive) toast(result.available ? `发现新版本 V${result.latest_version}` : "当前已是最新版本", "success", 4500);
    return result;
  } catch (error) {
    status.className = "update-status error"; status.textContent = `${error.message}。当前软件和本地数据不受影响，可稍后重试。`;
    if (interactive) toast(error.message, "error", 5500);
    return null;
  }
}

async function openUpdateDialog() {
  $("autoUpdateCheck").checked = autoUpdateEnabled();
  applyUpdateInstallMode(updateInstallMode());
  $("updateCurrentVersion").textContent = `V${state.version}`;
  $("updateStatus").className = "update-status"; $("updateStatus").textContent = "正在读取本地更新日志……";
  $("updateDialog").showModal(); await loadChangelog(); await checkForUpdates(false);
}

async function installLatestUpdate() {
  if (!state.updateRelease?.available) { const result = await checkForUpdates(true); if (!result?.available) return; }
  if (updateInstallMode() === "manual") {
    const url = state.updateRelease?.package?.browser_url || state.updateRelease?.release_url;
    if (!url) { toast("未找到补丁下载地址", "error"); return; }
    const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noopener"; document.body.appendChild(link); link.click(); link.remove();
    toast("补丁下载已打开：关闭软件后解压到原软件文件夹并覆盖", "success", 8000);
    return;
  }
  loading(true, "正在下载最新版", 1, "安装前会先自动完整备份", false);
  try {
    const started = await api("/api/admin/update/download", { method: "POST" }); state.updateJobId = started.id;
    let job = started;
    for (let round = 0; round < 3600; round++) {
      await new Promise((resolve) => setTimeout(resolve, round < 8 ? 600 : 1200));
      job = await api(`/api/admin/update/jobs/${encodeURIComponent(started.id)}`);
      setLoadingProgress(Number(job.progress || 1), job.message || "正在下载最新版", `目标版本 V${job.latest_version || state.updateRelease.latest_version} · 安装前自动完整备份`);
      if (job.status === "failed") throw new Error(job.error || "更新包下载失败");
      if (job.status === "ready") break;
    }
    if (job.status !== "ready") throw new Error("更新下载等待超时，请重新检查更新");
    loading(false); await new Promise((resolve) => setTimeout(resolve, 380));
    const accepted = await confirmAction(`V${job.latest_version || state.updateRelease.latest_version} 已下载并通过完整性校验。下一步会先自动保存完整备份，再重启更新；全部赛季、人员、组别、账号、发票与附件保持不变。`, { title: "自动备份并更新", confirmText: "备份并立即更新", tone: "info", eyebrow: "无损软件更新" });
    if (!accepted) { toast("更新包已下载，可稍后再次安装"); return; }
    const result = await api(`/api/admin/update/jobs/${encodeURIComponent(started.id)}/install`, { method: "POST", body: { backup_before_install: true } });
    toast(result.message || "安装程序已启动", "success", 6000);
  } catch (error) { loading(false); toast(error.message, "error", 7000); $("updateStatus").className = "update-status error"; $("updateStatus").textContent = error.message; }
}

function scheduleAutomaticUpdateCheck() {
  if (!autoUpdateEnabled()) return;
  let last = 0; try { last = Number(localStorage.getItem(LAST_UPDATE_CHECK_KEY) || 0); } catch (_) { last = 0; }
  if (Date.now() - last < 24 * 60 * 60 * 1000) return;
  try { localStorage.setItem(LAST_UPDATE_CHECK_KEY, String(Date.now())); } catch (_) { /* 仍允许本次检查 */ }
  setTimeout(async () => {
    const result = await checkForUpdates(false);
    if (result?.available) toast(`发现最新版 V${result.latest_version}，请到“系统设置 → 版本与更新”一键安装`, "success", 8000);
  }, 1800);
}

async function openRecovery() {
  try {
    const status = await api("/api/auth/recovery-status");
    if (!status.enabled) return toast("管理员未启用原始凭据找回", "error");
    $("recoveryForm").reset(); $("recoveryHint").textContent = `请输入安装本版本时保存的原始管理员账号与密码（提示：${status.username_hint || "原始管理员账号"}）。连续失败 5 次将锁定 15 分钟。`;
    $("recoveryDialog").showModal();
  } catch (error) { toast(error.message, "error"); }
}

async function submitRecovery(event) {
  event.preventDefault();
  try {
    const result = await api("/api/auth/recover-admin", { method: "POST", body: { original_username: $("recoveryOriginalUsername").value, original_password: $("recoveryOriginalPassword").value, new_username: $("recoveryNewUsername").value, new_password: $("recoveryNewPassword").value } });
    $("recoveryDialog").close(); $("loginUsername").value = $("recoveryNewUsername").value; $("loginPassword").value = ""; toast(result.message || "账号已重置", "success", 7000);
  } catch (error) { toast(error.message, "error", 7000); }
}

async function openRecoverySettings() {
  $("recoverySettingsForm").reset();
  try { const status = await api("/api/auth/recovery-status"); $("recoverySettingsEnabled").checked = Boolean(status.enabled); $("recoverySettingsDialog").showModal(); }
  catch (error) { toast(error.message, "error"); }
}

async function saveRecoverySettings(event) {
  event.preventDefault();
  try { await api("/api/admin/recovery", { method: "PUT", body: { enabled: $("recoverySettingsEnabled").checked, current_password: $("recoverySettingsCurrentPassword").value, original_username: $("recoverySettingsOriginalUsername").value, original_password: $("recoverySettingsOriginalPassword").value } }); $("recoverySettingsDialog").close(); toast("管理员找回设置已更新"); }
  catch (error) { toast(error.message, "error", 6000); }
}

function openFeedback() {
  $("feedbackForm").reset();
  const member = state.members.find((item) => item.id === state.user?.member_id);
  $("feedbackName").value = member?.name || ""; $("feedbackSeason").value = state.season?.name || ""; $("feedbackDepartment").value = member?.department || "";
  $("feedbackDialog").showModal();
}

function captureDraftForm(form) {
  return [...form.elements].filter((element) => element.name !== "" || element.id).filter((element) => !["file", "submit", "button", "hidden"].includes(element.type)).map((element) => ({ key: element.type === "radio" ? `@radio:${element.name}:${element.value}` : (element.id || element.name), value: ["checkbox", "radio"].includes(element.type) ? Boolean(element.checked) : element.value }));
}

function applyDraftSnapshot(snapshot) {
  const form = state.activeDraftForm; if (!form) return;
  snapshot.forEach((item) => { let element; if (item.key.startsWith("@radio:")) { const [, , name, value] = item.key.split(":"); element = form.querySelector(`input[type="radio"][name="${CSS.escape(name)}"][value="${CSS.escape(value)}"]`); } else element = form.querySelector(`#${CSS.escape(item.key)}`) || form.elements.namedItem(item.key); if (!element || !element.type) return; if (["checkbox", "radio"].includes(element.type)) element.checked = Boolean(item.value); else element.value = item.value; element.dispatchEvent(new Event("change", { bubbles: true })); });
}

function updateDraftButtons() {
  const active = Boolean(state.activeDraftForm);
  $("globalActionBar").classList.toggle("hidden", !active);
  $("globalUndoBtn").disabled = !active || state.draftHistoryIndex <= 0;
  $("globalRedoBtn").disabled = !active || state.draftHistoryIndex >= state.draftHistory.length - 1;
}

function activateDraftForm(form) {
  if (!form || ["loginForm", "decisionForm", "shortcutsForm", "recoveryForm"].includes(form.id)) return;
  state.activeDraftForm = form; state.draftHistory = [captureDraftForm(form)]; state.draftHistoryIndex = 0;
  form.closest("dialog")?.appendChild($("globalActionBar")); updateDraftButtons();
}

function recordDraftChange(form) {
  if (!form?.closest("dialog[open]")) return;
  if (state.activeDraftForm !== form) activateDraftForm(form);
  clearTimeout(state.draftHistoryTimer); state.draftHistoryTimer = setTimeout(() => {
    const snapshot = captureDraftForm(form), encoded = JSON.stringify(snapshot), current = JSON.stringify(state.draftHistory[state.draftHistoryIndex] || []);
    if (encoded === current) return;
    state.draftHistory = state.draftHistory.slice(0, state.draftHistoryIndex + 1); state.draftHistory.push(snapshot);
    if (state.draftHistory.length > 60) state.draftHistory.shift(); else state.draftHistoryIndex += 1;
    updateDraftButtons();
  }, 180);
}

function moveDraftHistory(direction) {
  const target = state.draftHistoryIndex + direction;
  if (!state.activeDraftForm || target < 0 || target >= state.draftHistory.length) return;
  state.draftHistoryIndex = target; applyDraftSnapshot(state.draftHistory[target]); updateDraftButtons();
}

function releaseDraftForm(dialog) {
  if (!state.activeDraftForm || dialog?.contains(state.activeDraftForm)) {
    state.activeDraftForm = null; state.draftHistory = []; state.draftHistoryIndex = -1;
    document.querySelector(".content-shell")?.appendChild($("globalActionBar")); updateDraftButtons();
  }
}

function handleDraftHistoryShortcut(event) {
  if (!state.activeDraftForm || !event.ctrlKey || event.altKey || event.metaKey) return;
  if (event.key.toLowerCase() === "z") { event.preventDefault(); moveDraftHistory(event.shiftKey ? 1 : -1); }
  else if (event.key.toLowerCase() === "y") { event.preventDefault(); moveDraftHistory(1); }
}

async function submitFeedback(event) {
  event.preventDefault(); const form = new FormData();
  form.append("reporter_name", $("feedbackName").value); form.append("season", $("feedbackSeason").value); form.append("department", $("feedbackDepartment").value); form.append("contact", $("feedbackContact").value); form.append("description", $("feedbackDescription").value); form.append("consent_public_attachment", $("feedbackPublicConsent").checked ? "true" : "false");
  [...$("feedbackFiles").files].forEach((file) => form.append("files", file));
  loading(true, "正在打包问题反馈", 8, "个人信息不会写入公开 GitHub 问题", true);
  try { const result = await api("/api/feedback", { method: "POST", body: form }); $("feedbackDialog").close(); toast(result.status === "sent" ? "问题反馈已提交" : (result.notice || "反馈已保留到本机待发送队列"), result.status === "sent" ? "success" : "error", 8000); }
  catch (error) { toast(error.message, "error", 8000); }
  finally { loading(false); }
}

function setupEvents() {
  $$('[data-theme-select]').forEach((select) => select.addEventListener("change", () => {
    const mode = applyDisplayMode(select.value);
    persistPersonalPreferences({ theme: mode }).catch(() => {});
    toast(`已切换为${DISPLAY_MODE_LABELS[mode]}`);
  }));
  $("decisionForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const expectsInput = !$("decisionInputWrap").classList.contains("hidden");
    finishDecision(expectsInput ? $("decisionInput").value.trim() : true);
  });
  $("decisionCancelBtn").addEventListener("click", () => finishDecision(false));
  $("decisionDialog").addEventListener("cancel", (event) => { event.preventDefault(); finishDecision(false); });
  $("decisionDialog").addEventListener("close", () => { if (state.decisionResolve) finishDecision(false); });
  $("openShortcutsBtn").addEventListener("click", openShortcutSettings);
  $("shortcutsForm").addEventListener("submit", saveShortcutSettings);
  $("resetShortcutsBtn").addEventListener("click", resetShortcutSettings);
  $("shortcutList").addEventListener("keydown", captureShortcut);
  $("shortcutList").addEventListener("click", (event) => {
    const clear = event.target.closest("[data-shortcut-clear]"); if (!clear) return;
    state.shortcutDraft[clear.dataset.shortcutClear] = ""; renderShortcutEditor();
  });
  document.addEventListener("keydown", handleGlobalShortcut);
  $("toggleLoginPassword").addEventListener("click", () => {
    const visible = $("loginPassword").type === "text"; $("loginPassword").type = visible ? "password" : "text";
    $("toggleLoginPassword").textContent = visible ? "显示" : "隐藏"; $("toggleLoginPassword").setAttribute("aria-pressed", String(!visible)); $("toggleLoginPassword").setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
  });
  $("forgotPasswordBtn").addEventListener("click", openRecovery); $("recoveryForm").addEventListener("submit", submitRecovery);
  $("openRecoverySettingsBtn").addEventListener("click", openRecoverySettings); $("recoverySettingsForm").addEventListener("submit", saveRecoverySettings);
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
  $("invoiceTable").addEventListener("click", (event) => { const attachment = event.target.closest("[data-attachment-open]"); if (attachment) return openLocalAttachment(attachment.dataset.attachmentOpen); const button = event.target.closest("[data-invoice-action]"); if (!button) return; if (button.dataset.invoiceAction === "delete") deleteInvoiceRecord(button.dataset.id); else openInvoice(button.dataset.id, button.dataset.invoiceAction === "view").catch((error) => toast(error.message, "error")); });
  $("invoiceTable").addEventListener("change", (event) => { const input = event.target.closest(".invoice-select"); if (!input) return; if (input.checked) state.selectedInvoiceIds.add(input.dataset.id); else state.selectedInvoiceIds.delete(input.dataset.id); renderInvoiceSelection(); });
  $("selectAllInvoices").addEventListener("change", (event) => { state.invoices.forEach((item) => event.target.checked ? state.selectedInvoiceIds.add(item.id) : state.selectedInvoiceIds.delete(item.id)); $$("#invoiceTable .invoice-select").forEach((input) => { input.checked = event.target.checked; }); renderInvoiceSelection(); });
  $("exportCsvBtn").addEventListener("click", () => downloadCsv()); $("batchExportSelectedBtn").addEventListener("click", () => downloadCsv([...state.selectedInvoiceIds]));
  $("exportPdfBtn").addEventListener("click", openPdfExportDialog); $("pdfExportForm").addEventListener("submit", downloadPdfExport);
  $("batchClearSelectionBtn").addEventListener("click", () => clearInvoiceSelection()); $("batchEditSelectedBtn").addEventListener("click", openBatchActionDialog); $("batchDeleteSelectedBtn").addEventListener("click", deleteSelectedInvoices);
  $("batchActionForm").addEventListener("submit", submitBatchAction); $("batchActionType").addEventListener("change", updateBatchActionVisibility); $("batchActionStatus").addEventListener("change", updateBatchActionVisibility); $("batchActionRatio").addEventListener("input", updateBatchActionVisibility);
  $("invoiceForm").addEventListener("submit", saveInvoiceForm); $("invoiceFile").addEventListener("change", (event) => uploadInvoiceFile(event.target.files[0])); $("runOcrBtn").addEventListener("click", runOcr);
  $("invoiceAttachmentPreview").addEventListener("click", (event) => { if (event.target.closest("[data-open-current-preview]")) openAttachmentViewer(state.currentInvoicePreview); });
  $("attachmentOpenOriginal").addEventListener("click", () => openLocalAttachment());
  $("supportingAttachmentFiles").addEventListener("change", (event) => uploadSupportingAttachments(event.target.files));
  $("supportingAttachmentList").addEventListener("click", (event) => { const open = event.target.closest("[data-support-open]"), remove = event.target.closest("[data-support-delete]"); if (open) openLocalAttachment(open.dataset.supportOpen); if (remove) deleteSupportingAttachment(remove.dataset.supportDelete); });
  $("attachmentZoomOut").addEventListener("click", () => applyAttachmentZoom(state.attachmentViewerZoom - .15)); $("attachmentZoomIn").addEventListener("click", () => applyAttachmentZoom(state.attachmentViewerZoom + .15)); $("attachmentZoomReset").addEventListener("click", () => applyAttachmentZoom(1));
  $("invoiceAmount").addEventListener("input", updateReimbursementStatus); $("reimbursedAmount").addEventListener("input", updateReimbursementStatus);
  $("invoicePayer").addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights()));
  $$('input[name="burdenType"]').forEach((input) => input.addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights())));
  $("splitMode").addEventListener("change", () => renderSplitMembers(selectedSplitIds(), selectedWeights()));
  $("splitMemberPicker").addEventListener("change", (event) => { if (event.target.type === "checkbox") { const weight = event.target.closest(".split-member").querySelector(".split-weight"); weight.disabled = $("splitMode").value !== "weighted" || !event.target.checked; } });
  $("batchImportBtn").addEventListener("click", openBatchImport); $("batchForm").addEventListener("submit", submitBatch);
  $("batchFile").addEventListener("change", (event) => renderBatchPreviews(event.target.files)); $("batchBurden").addEventListener("change", updateBatchSplitVisibility);
  $("batchFilePreview").addEventListener("click", (event) => { const button = event.target.closest("[data-batch-preview]"); if (button) openAttachmentViewer(state.batchPreviewFiles[Number(button.dataset.batchPreview)]); });
  $("batchReviewList").addEventListener("click", (event) => { const button = event.target.closest("[data-batch-review]"); if (button) openInvoice(button.dataset.batchReview).catch((error) => toast(error.message, "error")); });
  $("recordSettlementBtn").addEventListener("click", () => openSettlement()); $("settlementForm").addEventListener("submit", saveSettlement);
  $("transferRecommendations").addEventListener("click", (event) => { const button = event.target.closest("[data-transfer-from]"); if (button) openSettlement(button.dataset.transferFrom, button.dataset.transferTo, button.dataset.transferAmount); });
  $("settlementHistory").addEventListener("click", (event) => { const button = event.target.closest("[data-settlement-delete]"); if (button) deleteSettlement(button.dataset.settlementDelete); });
  $("reportRunBtn").addEventListener("click", loadReports);
  $("addMemberBtn").addEventListener("click", () => openMember()); $("memberForm").addEventListener("submit", saveMember);
  $("memberCards").addEventListener("click", (event) => { const edit = event.target.closest("[data-member-edit]"), archive = event.target.closest("[data-member-archive]"), avatar = event.target.closest("[data-member-avatar]"); if (edit) openMember(state.members.find((item) => item.id === edit.dataset.memberEdit)); if (archive) archiveMember(archive.dataset.memberArchive); if (avatar) chooseMemberAvatar(avatar.dataset.memberAvatar); });
  $("addUserBtn").addEventListener("click", () => openUser()); $("userForm").addEventListener("submit", saveUser);
  $("userTable").addEventListener("click", (event) => { const button = event.target.closest("[data-user-edit]"); if (button) openUser(state.users.find((item) => item.id === button.dataset.userEdit)); });
  $("seasonBadgeBtn").addEventListener("click", openSeasonManager); $("openSeasonManagerBtn").addEventListener("click", openSeasonManager);
  $("seasonCreateForm").addEventListener("submit", createSeason); $("departmentCreateForm").addEventListener("submit", createDepartment);
  $("seasonManagerList").addEventListener("click", (event) => {
    const switchButton = event.target.closest("[data-season-switch]"), renameButton = event.target.closest("[data-season-rename]"), toggleButton = event.target.closest("[data-season-toggle]");
    if (switchButton) switchSeason(state.seasons.find((item) => item.id === switchButton.dataset.seasonSwitch));
    if (renameButton) renameSeason(state.seasons.find((item) => item.id === renameButton.dataset.seasonRename));
    if (toggleButton) toggleSeason(state.seasons.find((item) => item.id === toggleButton.dataset.seasonToggle));
  });
  $("addCreatorBtn").addEventListener("click", () => openCreator().catch((error) => toast(error.message, "error")));
  $("creatorForm").addEventListener("submit", saveCreator); $("deleteCreatorBtn").addEventListener("click", deleteCreator);
  $("creatorCards").addEventListener("click", (event) => { const button = event.target.closest("[data-creator-edit]"); if (button) openCreator(state.creators.find((item) => item.id === button.dataset.creatorEdit)).catch((error) => toast(error.message, "error")); });
  $("createSnapshotBtn").addEventListener("click", createSnapshotManually); $("snapshotList").addEventListener("click", (event) => { const button = event.target.closest("[data-snapshot-restore]"); if (button) restoreSnapshot(button.dataset.snapshotRestore); });
  $("openReferencesBtn").addEventListener("click", openReferences); $("categoryForm").addEventListener("submit", saveCategory); $("sourceForm").addEventListener("submit", saveSource);
  $("openInvoiceDefaultsBtn").addEventListener("click", openInvoiceDefaults); $("invoiceDefaultsForm").addEventListener("submit", saveInvoiceDefaults); $("defaultInvoiceBurden").addEventListener("change", updateDefaultSplitVisibility);
  $("resetCategoryBtn").addEventListener("click", resetCategoryEditor); $("resetSourceBtn").addEventListener("click", resetSourceEditor);
  $("categoryManagerList").addEventListener("click", (event) => { const button = event.target.closest("[data-category-edit]"); if (button) editCategory(state.categories.find((item) => item.id === button.dataset.categoryEdit)); });
  $("sourceManagerList").addEventListener("click", (event) => { const button = event.target.closest("[data-source-edit]"); if (button) editSource(state.fundingSources.find((item) => item.id === button.dataset.sourceEdit)); });
  $("openAppearanceBtn").addEventListener("click", openAppearance); $("appearanceForm").addEventListener("submit", saveAppearance); $("backgroundOverlay").addEventListener("input", () => $("overlayValue").textContent = `${$("backgroundOverlay").value}%`);
  $("loginPanelOpacity").addEventListener("input", () => { $("loginOpacityValue").textContent = `${$("loginPanelOpacity").value}%`; document.documentElement.style.setProperty("--login-panel-opacity", String(Number($("loginPanelOpacity").value) / 100)); });
  $("appearanceDialog").addEventListener("click", (event) => { const preset = event.target.closest("[data-overlay-preset]"); if (!preset) return; $("backgroundOverlay").value = preset.dataset.overlayPreset; $("overlayValue").textContent = `${preset.dataset.overlayPreset}%`; document.documentElement.style.setProperty("--background-overlay", String(Number(preset.dataset.overlayPreset) / 100)); });
  $("backgroundFile").addEventListener("change", (event) => chooseBackgroundFile(event.target.files[0])); $("loginMediaFiles").addEventListener("change", (event) => addLoginMediaFiles(event.target.files));
  $("loadingCarFiles").addEventListener("change", (event) => addLoadingCarFiles(event.target.files)); $("resetLoadingCarsBtn").addEventListener("click", resetLoadingCars);
  $("loadingCarEditor").addEventListener("click", (event) => { const remove = event.target.closest("[data-loading-car-remove]"); if (!remove) return; state.appearanceLoadingCars.splice(Number(remove.dataset.loadingCarRemove), 1); if (!state.appearanceLoadingCars.length) state.appearanceLoadingCars = DEFAULT_LOADING_CARS.map((item) => ({ ...item })); renderLoadingCarEditor(); });
  $("clearBackgroundBtn").addEventListener("click", () => { state.appearanceBackground = null; renderBackgroundPreview(); }); $("scanWallpapersBtn").addEventListener("click", () => scanWallpapers(true));
  $("wallpaperGrid").addEventListener("click", (event) => { const background = event.target.closest("[data-wallpaper-background]"), login = event.target.closest("[data-wallpaper-login]"); if (background) importWallpaper(background.dataset.wallpaperBackground, "background"); if (login) importWallpaper(login.dataset.wallpaperLogin, "login"); });
  $("loginSlideEditor").addEventListener("input", (event) => { const titleIndex = event.target.dataset.slideTitle, durationIndex = event.target.dataset.slideDuration; if (titleIndex !== undefined && state.appearanceSlides[Number(titleIndex)]) state.appearanceSlides[Number(titleIndex)].title = event.target.value; if (durationIndex !== undefined && state.appearanceSlides[Number(durationIndex)]) state.appearanceSlides[Number(durationIndex)].duration = Math.max(2, Math.min(600, Number(event.target.value || 8))); });
  $("loginSlideEditor").addEventListener("click", (event) => { const remove = event.target.closest("[data-slide-remove]"), move = event.target.closest("[data-slide-move]"); if (remove) state.appearanceSlides.splice(Number(remove.dataset.slideRemove), 1); if (move) { const index = Number(move.dataset.index), target = move.dataset.slideMove === "up" ? index - 1 : index + 1; if (target >= 0 && target < state.appearanceSlides.length) [state.appearanceSlides[index], state.appearanceSlides[target]] = [state.appearanceSlides[target], state.appearanceSlides[index]]; } if (remove || move) renderLoginSlideEditor(); });
  $("openClassificationBtn").addEventListener("click", openClassification); $("classificationRuleForm").addEventListener("submit", saveClassificationRule); $("newRuleBtn").addEventListener("click", resetClassificationEditor); $("resetRulesBtn").addEventListener("click", resetClassificationRules);
  $("classificationRuleList").addEventListener("click", (event) => { const edit = event.target.closest("[data-rule-edit]"), remove = event.target.closest("[data-rule-delete]"); if (edit) editClassificationRule(edit.dataset.ruleEdit); if (remove) deleteClassificationRule(remove.dataset.ruleDelete); });
  $("openSyncBtn").addEventListener("click", openSync); $("syncForm").addEventListener("submit", saveSync); $("syncNowBtn").addEventListener("click", syncNow);
  $("downloadBackupBtn").addEventListener("click", downloadBackup); $("restoreBackupInput").addEventListener("change", (event) => restoreBackup(event.target.files[0])); $("deleteDemoBtn").addEventListener("click", deleteDemo);
  $("openUpdateBtn").addEventListener("click", openUpdateDialog); $("checkUpdateBtn").addEventListener("click", () => checkForUpdates(true)); $("installUpdateBtn").addEventListener("click", installLatestUpdate);
  $("autoUpdateCheck").addEventListener("change", (event) => { try { localStorage.setItem(AUTO_UPDATE_STORAGE_KEY, event.target.checked ? "1" : "0"); } catch (_) { /* 当前会话仍可使用 */ } toast(event.target.checked ? "已启用每日自动检查更新" : "已关闭启动后自动检查"); });
  $("updateInstallMode").addEventListener("change", (event) => applyUpdateInstallMode(event.target.value, true));
  $("openPersonalSettingsBtn").addEventListener("click", openPersonalSettings); $("personalAudioFiles").addEventListener("change", (event) => uploadPersonalAudio(event.target.files));
  $("personalAudioList").addEventListener("click", (event) => { const play = event.target.closest("[data-audio-play]"), remove = event.target.closest("[data-audio-delete]"); if (play) playPersonalAudio(Number(play.dataset.audioPlay)); if (remove) deletePersonalAudio(remove.dataset.audioDelete); });
  $("personalAudioMode").addEventListener("change", () => persistPersonalPreferences()); $("personalAudioEnabled").addEventListener("change", () => { $("personalAudioPlayer").muted = !$("personalAudioEnabled").checked; persistPersonalPreferences(); });
  $("personalAudioVolume").addEventListener("input", () => { $("personalAudioVolumeText").textContent = `${$("personalAudioVolume").value}%`; $("personalAudioPlayer").volume = Number($("personalAudioVolume").value) / 100; }); $("personalAudioVolume").addEventListener("change", () => persistPersonalPreferences()); $("personalAudioPlayer").addEventListener("ended", advancePersonalAudio);
  $("exportPersonalSettingsBtn").addEventListener("click", exportPersonalSettings); $("importPersonalSettingsInput").addEventListener("change", (event) => importPersonalSettings(event.target.files[0]));
  $("openFeedbackBtn").addEventListener("click", openFeedback); $("feedbackForm").addEventListener("submit", submitFeedback);
  $("qualityFaultToggle").addEventListener("click", () => $("qualityFaultPanel").classList.toggle("hidden")); $("qualityFaultClose").addEventListener("click", () => $("qualityFaultPanel").classList.add("hidden"));
  $("qualityFaultList").addEventListener("click", (event) => { const open = event.target.closest("[data-quality-open]"), resolve = event.target.closest("[data-quality-resolve]"); if (open) openQualityIssue(open.dataset.qualityOpen); if (resolve) resolveQualityIssue(resolve.dataset.qualityResolve); });
  $("globalSaveBtn").addEventListener("click", () => state.activeDraftForm?.requestSubmit()); $("globalUndoBtn").addEventListener("click", () => moveDraftHistory(-1)); $("globalRedoBtn").addEventListener("click", () => moveDraftHistory(1));
  document.addEventListener("keydown", handleDraftHistoryShortcut, true); document.addEventListener("input", (event) => recordDraftChange(event.target.closest("form")), true); document.addEventListener("change", (event) => recordDraftChange(event.target.closest("form")), true);
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
  $$("dialog").forEach((dialog) => { dialog.addEventListener("close", () => releaseDraftForm(dialog)); dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    if (dialog.id === "decisionDialog") finishDecision(false); else dialog.close();
  }); });
  const draftObserver = new MutationObserver((records) => records.forEach((record) => { const dialog = record.target; if (dialog.open) requestAnimationFrame(() => activateDraftForm(dialog.querySelector("form"))); }));
  $$("dialog").forEach((dialog) => draftObserver.observe(dialog, { attributes: true, attributeFilter: ["open"] }));
  window.addEventListener("resize", () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(() => { if (state.currentView === "dashboard") renderDashboard(); }, 160); });
}

async function initialize() {
  state.shortcuts = loadShortcutSettings();
  applyDisplayMode(savedDisplayMode(), false);
  setupEvents();
  await loadPublicAppearance();
  try { const current = await api("/api/auth/me"); state.csrf = current.csrf_token; state.user = current.user; await loadBootstrap(); }
  catch (_) { showLogin(); }
}

initialize();
