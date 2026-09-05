const $ = (id) => document.getElementById(id),
  state = {
    meta: null,
    tasks: [],
    csrf: "",
    seasonOpen: true,
    importReady: false,
    deleteId: "",
  };
const labels = {
  todo: "未开始",
  doing: "进行中",
  review: "待验收",
  blocked: "阻塞",
  done: "已完成",
  low: "低",
  medium: "中",
  high: "高",
  urgent: "紧急",
};
function esc(v) {
  return String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 3600);
}
async function api(path, options = {}) {
  const config = {
    credentials: "same-origin",
    ...options,
    headers: { ...(options.headers || {}) },
  };
  if (config.body && !(config.body instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  if (state.csrf && String(config.method || "GET").toUpperCase() !== "GET")
    config.headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, config);
  if (response.status === 401) {
    location.href = `/?next=${encodeURIComponent(location.pathname)}`;
    throw new Error("请先登录");
  }
  if (!response.ok) {
    let msg = `操作失败（${response.status}）`;
    try {
      msg = (await response.json()).detail || msg;
    } catch (_) {}
    throw new Error(msg);
  }
  return response.json();
}
function selected(id) {
  return [...$(id).selectedOptions].map((o) => o.value);
}
function setSelected(id, values = []) {
  const wanted = new Set(values);
  [...$(id).options].forEach((o) => (o.selected = wanted.has(o.value)));
}
async function loadMeta() {
  state.meta = await api("/api/plans/meta");
  state.csrf = state.meta.csrf_token;
  $("versionText").textContent = `V${state.meta.version}`;
  $("seasonSelect").innerHTML = state.meta.seasons
    .map(
      (s) =>
        `<option value="${esc(s.id)}">${esc(s.name)}${s.active ? "" : "（归档）"}</option>`,
    )
    .join("");
  $("seasonSelect").value = state.meta.current_season_id;
  $("taskDepartments").innerHTML = state.meta.departments
    .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
    .join("");
  $("departmentFilter").innerHTML =
    '<option value="">全部组别</option>' +
    state.meta.departments
      .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
      .join("");
  $("taskAssignees").innerHTML = state.meta.users
    .map(
      (u) =>
        `<option value="${esc(u.id)}">${esc(u.display_name)} · ${esc(u.username)}</option>`,
    )
    .join("");
  $("assigneeFilter").innerHTML =
    '<option value="">全部负责人</option>' +
    state.meta.users
      .map(
        (u) =>
          `<option value="${esc(u.id)}">${esc(u.display_name)} · ${esc(u.username)}</option>`,
      )
      .join("");
}
async function loadTasks() {
  const query = new URLSearchParams({
    season_id: $("seasonSelect").value,
    status: $("statusFilter").value,
    priority: $("priorityFilter").value,
    department: $("departmentFilter").value,
    assignee_id: $("assigneeFilter").value,
    search: $("taskSearch").value,
  });
  const data = await api(`/api/plans/tasks?${query}`);
  state.tasks = data.items;
  state.seasonOpen = data.season_open;
  $("addTaskBtn").disabled = !state.seasonOpen;
  $("importBtn").disabled = !state.seasonOpen;
  render();
}
function render() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = state.tasks.filter(
      (t) =>
        t.status !== "done" &&
        t.due_date &&
        new Date(`${t.due_date}T00:00:00`) - today >= 0 &&
        new Date(`${t.due_date}T00:00:00`) - today <= 7 * 864e5,
    ).length,
    overdue = state.tasks.filter(
      (t) =>
        t.status !== "done" &&
        t.due_date &&
        new Date(`${t.due_date}T00:00:00`) < today,
    ).length;
  $("metricAll").textContent = state.tasks.length;
  $("metricDoing").textContent = state.tasks.filter((t) =>
    ["doing", "review"].includes(t.status),
  ).length;
  $("metricDue").textContent = due;
  $("metricOverdue").textContent = overdue;
  renderTable();
  renderGantt();
  renderBoard();
  renderTaskOptions();
  $("ganttPanel").classList.toggle("hidden", $("viewMode").value !== "gantt");
  $("boardPanel").classList.toggle("hidden", $("viewMode").value !== "board");
  $("tablePanel").classList.toggle("hidden", $("viewMode").value !== "table");
}
function userNames(ids = []) {
  const map = new Map(state.meta.users.map((u) => [u.id, u.display_name]));
  return ids.map((id) => map.get(id) || id).join("、") || "未指定";
}
function renderTable() {
  $("taskCount").textContent = `${state.tasks.length} 项`;
  $("taskRows").innerHTML =
    state.tasks
      .map(
        (t) =>
          `<tr><td><code>${esc(t.external_id || "—")}</code></td><td><b>${esc(t.title)}</b><br><small>${esc(t.priority && labels[t.priority])}优先级</small></td><td>${esc(userNames(t.assignee_user_ids))}</td><td>${esc((t.department || []).join("、") || "未指定")}</td><td><span class="status-chip status-${esc(t.status)}">${esc(labels[t.status])}</span></td><td>${Number(t.progress) || 0}%</td><td>${esc(t.due_date || "未设置")}</td><td><button class="row-action" data-edit="${esc(t.id)}">查看/编辑</button></td></tr>`,
      )
      .join("") || '<tr><td colspan="8" class="empty">暂无任务</td></tr>';
}
function renderGantt() {
  if (!state.tasks.length) {
    $("gantt").innerHTML =
      '<div class="empty">暂无任务，可新建或导入甘特图</div>';
    return;
  }
  const dates = state.tasks
    .flatMap((t) => [t.start_date, t.due_date])
    .filter(Boolean)
    .map((v) => new Date(`${v}T00:00:00`).getTime());
  let min = dates.length ? Math.min(...dates) : Date.now(),
    max = dates.length ? Math.max(...dates) : Date.now() + 7 * 864e5;
  if (max <= min) max = min + 864e5;
  const span = max - min;
  $("gantt").innerHTML =
    `<div class="gantt-head"><div class="gantt-label">任务 / 负责人</div><div class="gantt-axis"><span>${new Date(min).toLocaleDateString()}</span><span>${new Date(max).toLocaleDateString()}</span></div></div>` +
    state.tasks
      .map((t) => {
        const start = t.start_date
            ? new Date(`${t.start_date}T00:00:00`).getTime()
            : min,
          end = t.due_date
            ? new Date(`${t.due_date}T00:00:00`).getTime()
            : start + 864e5,
          left = Math.max(0, ((start - min) / span) * 100),
          width = Math.max(
            0.8,
            ((Math.max(end, start + 864e5) - start) / span) * 100,
          );
        return `<div class="gantt-row"><div class="gantt-label"><b>${esc(t.external_id ? `${t.external_id} · ${t.title}` : t.title)}</b><br><small>${esc(userNames(t.assignee_user_ids))}</small></div><div class="gantt-track"><button class="gantt-bar ${esc(t.status)}" data-edit="${esc(t.id)}" style="left:${left}%;width:${Math.min(width, 100 - left)}%" title="${esc(t.start_date)} → ${esc(t.due_date)} · ${Number(t.progress) || 0}%"><i style="width:${Number(t.progress) || 0}%"></i></button></div></div>`;
      })
      .join("");
}
function renderBoard() {
  const statuses = ["todo", "doing", "review", "blocked", "done"];
  $("taskBoard").innerHTML = statuses
    .map((status) => {
      const tasks = state.tasks.filter((task) => task.status === status);
      return `<section class="board-column"><header><b>${esc(labels[status])}</b><span>${tasks.length}</span></header><div>${tasks
        .map(
          (task) =>
            `<button class="board-card priority-edge-${esc(task.priority)}" data-edit="${esc(task.id)}"><small>${esc(task.external_id || labels[task.priority])}</small><b>${esc(task.title)}</b><span>${esc(userNames(task.assignee_user_ids))}</span><i><em style="width:${Number(task.progress) || 0}%"></em></i><small>${Number(task.progress) || 0}% · ${esc(task.due_date || "未设截止")}</small></button>`,
        )
        .join("") || '<p class="board-empty">暂无任务</p>'}</div></section>`;
    })
    .join("");
}
function renderTaskOptions() {
  const options = state.tasks
    .map((t) => `<option value="${esc(t.id)}">${esc(t.title)}</option>`)
    .join("");
  $("taskParent").innerHTML = '<option value="">无</option>' + options;
  $("taskDependencies").innerHTML = options;
}
function openTask(item = null) {
  $("taskForm").reset();
  $("taskId").value = item?.id || "";
  $("taskExternalId").value = item?.external_id || "";
  $("taskDialogTitle").textContent = item ? "查看或编辑任务" : "新建任务";
  $("taskTitle").value = item?.title || "";
  $("taskDescription").value = item?.description || "";
  $("taskStatus").value = item?.status || "todo";
  $("taskPriority").value = item?.priority || "medium";
  $("taskStart").value = item?.start_date || "";
  $("taskDue").value = item?.due_date || "";
  $("taskProgress").value = item?.progress || 0;
  $("taskParent").value = item?.parent_id || "";
  setSelected("taskDepartments", item?.department || []);
  setSelected("taskAssignees", item?.assignee_user_ids || []);
  setSelected("taskDependencies", item?.dependency_ids || []);
  const reminders = new Set((item?.reminder_days || [7, 3, 1, 0]).map(String));
  document
    .querySelectorAll('[name="reminder"]')
    .forEach((c) => (c.checked = reminders.has(c.value)));
  $("deleteTaskBtn").classList.toggle(
    "hidden",
    !item || state.meta.user.role !== "admin",
  );
  const disabled = Boolean(item && !state.seasonOpen);
  [...$("taskForm").elements].forEach((el) => {
    if (el.type !== "button" && el.id !== "deleteTaskBtn")
      el.disabled = disabled;
  });
  $("taskDialog").showModal();
}
async function saveTask(event) {
  event.preventDefault();
  const id = $("taskId").value,
    payload = {
      external_id: $("taskExternalId").value,
      title: $("taskTitle").value,
      description: $("taskDescription").value,
      status: $("taskStatus").value,
      priority: $("taskPriority").value,
      start_date: $("taskStart").value,
      due_date: $("taskDue").value,
      progress: Number($("taskProgress").value),
      parent_id: $("taskParent").value,
      departments: selected("taskDepartments"),
      assignee_user_ids: selected("taskAssignees"),
      dependency_ids: selected("taskDependencies").filter((v) => v !== id),
      reminder_days: [
        ...document.querySelectorAll('[name="reminder"]:checked'),
      ].map((c) => Number(c.value)),
    };
  try {
    await api(
      id ? `/api/plans/tasks/${encodeURIComponent(id)}` : "/api/plans/tasks",
      { method: id ? "PUT" : "POST", body: payload },
    );
    $("taskDialog").close();
    toast("任务已保存");
    await loadTasks();
  } catch (error) {
    toast(error.message);
  }
}
async function importFile(apply = false) {
  const file = $("ganttFile").files[0];
  if (!file) return toast("请选择甘特图文件");
  const form = new FormData();
  form.append("file", file);
  form.append("apply", String(apply));
  form.append("strategy", $("ganttImportStrategy").value);
  try {
    const result = await api("/api/plans/import", {
      method: "POST",
      body: form,
    });
    if (apply) {
      $("importDialog").close();
      toast(`导入完成：新增 ${result.created_count} 项，更新 ${result.updated_count} 项`);
      await loadTasks();
      return;
    }
    state.importReady = result.can_apply;
    $("applyImportBtn").disabled = !result.can_apply;
    $("importSummary").classList.remove("hidden");
    $("importSummary").innerHTML = `<b>共 ${Number(result.count) || 0} 项</b><span class="good-stock">新增 ${Number(result.create_count) || 0}</span><span class="status-doing">更新 ${Number(result.update_count) || 0}</span><span class="${result.errors?.length ? "low-stock" : "good-stock"}">${result.errors?.length || 0} 个错误</span>`;
    $("importPreview").className = "preview-list";
    $("importPreview").innerHTML =
      (result.errors || [])
        .map(
          (v) =>
            `<div class="preview-row"><span class="low-stock">${esc(v)}</span></div>`,
        )
        .join("") +
      (result.warnings || [])
        .map(
          (v) =>
            `<div class="preview-row"><span class="status-review">提示：${esc(v)}</span></div>`,
        )
        .join("") +
      (result.items || [])
        .map(
          (v) =>
            `<div class="preview-row"><span><b>${esc(v.external_id || `第 ${v.row_number} 行`)} · ${esc(v.title)}</b><br><small>${esc((v.departments || []).join("、") || "未设组别")} · ${esc((v.assignee_names || []).join("、") || "未设负责人")} · ${esc(v.start_date || "未设开始")} → ${esc(v.due_date || "未设截止")}</small></span><b class="${v.action === "update" ? "status-doing" : "good-stock"}">${v.action === "update" ? "更新" : "新增"} · ${Number(v.progress) || 0}%</b></div>`,
        )
        .join("");
    toast(`预检完成：${result.count} 项`);
  } catch (error) {
    state.importReady = false;
    $("applyImportBtn").disabled = true;
    toast(error.message);
  }
}
async function loadReminders() {
  try {
    const data = await api("/api/plans/reminders");
    if (!data.items.length) return;
    $("reminderDrawer").classList.remove("hidden");
    $("reminderList").innerHTML = data.items
      .map(
        (r) =>
          `<button class="reminder-item ${r.days_left < 0 ? "overdue" : ""}" data-reminder-task="${esc(r.task_id)}" data-reminder-key="${esc(r.reminder_key)}"><b>${esc(r.title)}</b><small>${r.days_left < 0 ? `已逾期 ${Math.abs(r.days_left)} 天` : r.days_left === 0 ? "今天截止" : `还有 ${r.days_left} 天截止`} · ${esc(r.due_date)}</small></button>`,
      )
      .join("");
    if (window.Notification && Notification.permission === "granted")
      data.items
        .filter((r) => !r.read)
        .slice(0, 5)
        .forEach(
          (r) =>
            new Notification("燕翔车队 Deadline 提醒", {
              body: `${r.title} · ${r.days_left < 0 ? "已逾期" : `${r.days_left} 天后截止`}`,
            }),
        );
  } catch (_) {}
}
document.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-close]");
  if (close) $(close.dataset.close).close();
  const edit = event.target.closest("[data-edit]");
  if (edit) openTask(state.tasks.find((t) => t.id === edit.dataset.edit));
  const reminder = event.target.closest("[data-reminder-task]");
  if (reminder) {
    await api(
      `/api/plans/reminders/${encodeURIComponent(reminder.dataset.reminderTask)}/${encodeURIComponent(reminder.dataset.reminderKey)}/read`,
      { method: "POST", body: {} },
    );
    reminder.remove();
  }
});
$("taskForm").addEventListener("submit", saveTask);
$("addTaskBtn").addEventListener("click", () => openTask());
$("refreshBtn").addEventListener("click", loadTasks);
$("seasonSelect").addEventListener("change", loadTasks);
$("statusFilter").addEventListener("change", loadTasks);
$("priorityFilter").addEventListener("change", loadTasks);
$("departmentFilter").addEventListener("change", loadTasks);
$("assigneeFilter").addEventListener("change", loadTasks);
$("taskSearch").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadTasks, 280);
});
$("viewMode").addEventListener("change", render);
$("importBtn").addEventListener("click", () => {
  state.importReady = false;
  $("applyImportBtn").disabled = true;
  $("importSummary").classList.add("hidden");
  $("importPreview").className = "preview-list empty";
  $("importPreview").textContent = "请选择文件并预检";
  $("importDialog").showModal();
});
$("previewImportBtn").addEventListener("click", () => importFile(false));
$("applyImportBtn").addEventListener("click", () => importFile(true));
$("deleteTaskBtn").addEventListener("click", () => {
  state.deleteId = $("taskId").value;
  $("confirmDialog").showModal();
});
$("confirmDeleteBtn").addEventListener("click", async () => {
  try {
    await api(`/api/plans/tasks/${encodeURIComponent(state.deleteId)}`, {
      method: "DELETE",
    });
    $("confirmDialog").close();
    $("taskDialog").close();
    toast("任务已删除");
    await loadTasks();
  } catch (error) {
    toast(error.message);
  }
});
$("enableNotificationBtn").addEventListener("click", async () => {
  if (!window.Notification) return toast("当前运行环境不支持 Windows 通知");
  const permission = await Notification.requestPermission();
  toast(permission === "granted" ? "Windows 提醒已启用" : "未获得系统通知权限");
});
$("closeReminders").addEventListener("click", () =>
  $("reminderDrawer").classList.add("hidden"),
);
(async () => {
  try {
    await loadMeta();
    await loadTasks();
    await loadReminders();
    setInterval(loadReminders, 5 * 60 * 1000);
  } catch (error) {
    toast(error.message);
  }
})();
