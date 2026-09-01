const $ = (id) => document.getElementById(id),
  state = {
    meta: null,
    csrf: "",
    components: [],
    movements: [],
    bomReady: false,
    deleteId: "",
  };
const movementLabels = {
  in: "采购入库",
  out: "领用出库",
  adjust: "库存调整",
  pending: "待审批",
  applied: "已生效",
  rejected: "已拒绝",
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
function money(cents) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
  }).format((Number(cents) || 0) / 100);
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
async function loadMeta() {
  state.meta = await api("/api/inventory/meta");
  state.csrf = state.meta.csrf_token;
  $("versionText").textContent = `V${state.meta.version}`;
  $("addComponentBtn").classList.toggle("hidden", !state.meta.can_manage);
  $("managerBtn").classList.toggle("hidden", state.meta.user.role !== "admin");
  $("mouserConfigBtn").classList.toggle(
    "hidden",
    state.meta.user.role !== "admin",
  );
  $("movementType").querySelector('[value="adjust"]').disabled =
    !state.meta.can_manage;
  $("managerList").innerHTML = state.meta.users
    .filter((u) => u.role !== "viewer")
    .map(
      (u) =>
        `<label><input type="checkbox" value="${esc(u.id)}" ${state.meta.manager_ids.includes(u.id) ? "checked" : ""}>${esc(u.display_name)} · ${esc(u.username)}</label>`,
    )
    .join("");
}
async function loadData() {
  const query = new URLSearchParams({
    search: $("componentSearch").value,
    category: $("categoryFilter").value,
    low_stock: String($("lowStockOnly").checked),
  });
  const [components, movements] = await Promise.all([
    api(`/api/inventory/components?${query}`),
    api("/api/inventory/movements"),
  ]);
  state.components = components.items;
  state.movements = movements.items;
  $("metricCount").textContent = components.count;
  $("metricQuantity").textContent = Number(
    components.quantity || 0,
  ).toLocaleString("zh-CN");
  $("metricValue").textContent = money(components.value_cents);
  $("metricLow").textContent = components.low_stock_count;
  renderComponents();
  renderMovements();
  renderComponentOptions();
}
function renderComponents() {
  $("componentRows").innerHTML =
    state.components
      .map(
        (c) =>
          `<tr><td class="inventory-name"><b>${esc(c.name)}</b><small>${esc(c.manufacturer || "未填写制造商")}</small></td><td>${esc(c.manufacturer_part_no || "—")}<br><small>${esc(c.mouser_part_no || "")}</small></td><td>${esc(c.category || "未分类")}<br><small>${esc(c.package || "未填写封装")}</small></td><td>${esc(c.location || "未设置")}</td><td class="${c.low_stock ? "low-stock" : "good-stock"}">${Number(c.quantity).toLocaleString("zh-CN")} ${esc(c.unit)}${c.low_stock ? " · 低库存" : ""}</td><td>${money(c.unit_cost_cents)}</td><td><button class="row-action" data-component="${esc(c.id)}">${state.meta.can_manage ? "编辑" : "查看"}</button></td></tr>`,
      )
      .join("") || '<tr><td colspan="7" class="empty">暂无元件档案</td></tr>';
}
function renderMovements() {
  $("movementRows").innerHTML =
    state.movements
      .map(
        (m) =>
          `<tr><td>${esc(String(m.created_at).replace("T", " ").slice(0, 16))}</td><td>${esc(m.component_name)}</td><td>${esc(movementLabels[m.movement_type])}</td><td>${Number(m.quantity).toLocaleString("zh-CN")} ${esc(m.unit)}</td><td>${esc(m.project_name || "—")}<br><small>${esc(m.batch_no || "")}</small></td><td>${esc(m.requester_name || "系统")}</td><td><span class="status-chip status-${esc(m.status)}">${esc(movementLabels[m.status])}</span></td><td>${m.status === "pending" && state.meta.can_manage ? `<button class="row-action" data-decision="approve" data-movement="${esc(m.id)}">通过</button> <button class="row-action" data-decision="reject" data-movement="${esc(m.id)}">拒绝</button>` : "—"}</td></tr>`,
      )
      .join("") || '<tr><td colspan="8" class="empty">暂无出入库流水</td></tr>';
}
function renderComponentOptions() {
  $("movementComponent").innerHTML = state.components
    .map(
      (c) =>
        `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.manufacturer_part_no || "无型号")} · 库存 ${Number(c.quantity)}</option>`,
    )
    .join("");
}
function openComponent(item = null) {
  $("componentForm").reset();
  $("componentId").value = item?.id || "";
  $("componentDialogTitle").textContent = item
    ? "查看或编辑元件"
    : "新建元件档案";
  $("componentName").value = item?.name || "";
  $("componentCategory").value = item?.category || "";
  $("componentManufacturer").value = item?.manufacturer || "";
  $("componentMpn").value = item?.manufacturer_part_no || "";
  $("componentMouser").value = item?.mouser_part_no || "";
  $("componentPackage").value = item?.package || "";
  $("componentLocation").value = item?.location || "";
  $("componentUnit").value = item?.unit || "个";
  $("componentMinimum").value = item?.minimum_quantity || 0;
  $("componentCost").value = (Number(item?.unit_cost_cents) || 0) / 100;
  $("componentParameters").value = item?.parameters || "";
  $("componentDatasheet").value = item?.datasheet_url || "";
  $("componentImage").value = item?.image_url || "";
  $("componentNote").value = item?.note || "";
  $("deleteComponentBtn").classList.toggle(
    "hidden",
    !item || state.meta.user.role !== "admin",
  );
  [...$("componentForm").elements].forEach((el) => {
    if (el.type !== "button") el.disabled = !state.meta.can_manage;
  });
  $("componentDialog").showModal();
}
async function saveComponent(event) {
  event.preventDefault();
  const id = $("componentId").value,
    payload = {
      name: $("componentName").value,
      category: $("componentCategory").value,
      manufacturer: $("componentManufacturer").value,
      manufacturer_part_no: $("componentMpn").value,
      mouser_part_no: $("componentMouser").value,
      package: $("componentPackage").value,
      location: $("componentLocation").value,
      unit: $("componentUnit").value,
      minimum_quantity: Number($("componentMinimum").value),
      unit_cost: Number($("componentCost").value),
      parameters: $("componentParameters").value,
      datasheet_url: $("componentDatasheet").value,
      image_url: $("componentImage").value,
      note: $("componentNote").value,
    };
  try {
    await api(
      id
        ? `/api/inventory/components/${encodeURIComponent(id)}`
        : "/api/inventory/components",
      { method: id ? "PUT" : "POST", body: payload },
    );
    $("componentDialog").close();
    toast("元件档案已保存");
    await loadData();
  } catch (error) {
    toast(error.message);
  }
}
async function saveMovement(event) {
  event.preventDefault();
  const payload = {
    component_id: $("movementComponent").value,
    movement_type: $("movementType").value,
    quantity: Number($("movementQuantity").value),
    unit_cost: Number($("movementCost").value),
    batch_no: $("movementBatch").value,
    project_name: $("movementProject").value,
    note: $("movementNote").value,
  };
  try {
    const result = await api("/api/inventory/movements", {
      method: "POST",
      body: payload,
    });
    $("movementDialog").close();
    toast(
      result.status === "applied"
        ? "出入库已生效"
        : "申请已提交，等待元件库负责人审批",
    );
    await loadData();
  } catch (error) {
    toast(error.message);
  }
}
async function bomImport(apply = false) {
  const file = $("bomFile").files[0];
  if (!file) return toast("请选择 BOM 文件");
  const form = new FormData();
  form.append("file", file);
  form.append("mode", $("bomMode").value);
  form.append("production_count", $("productionCount").value || "1");
  form.append("apply", String(apply));
  try {
    const result = await api("/api/inventory/bom-import", {
      method: "POST",
      body: form,
    });
    if (apply) {
      $("bomDialog").close();
      toast(`已完成 ${result.count} 条 BOM 入库`);
      await loadData();
      return;
    }
    state.bomReady = result.can_apply;
    $("bomApplyBtn").disabled =
      !result.can_apply ||
      $("bomMode").value !== "stock_in" ||
      !state.meta.can_manage;
    $("bomPreview").className = "preview-list field wide";
    $("bomPreview").innerHTML =
      (result.errors || [])
        .map((v) => `<div class="preview-row low-stock">${esc(v)}</div>`)
        .join("") +
      (result.items || [])
        .map(
          (v) =>
            `<div class="preview-row"><span><b>${esc(v.name)}</b><br><small>${esc(v.manufacturer_part_no || v.mouser_part_no || "无型号")} · 需求 ${Number(v.quantity)} · 库存 ${Number(v.available)}</small></span><b class="${v.shortage > 0 ? "low-stock" : "good-stock"}">${v.shortage > 0 ? `缺 ${Number(v.shortage)}` : "库存足够"}</b></div>`,
        )
        .join("");
    toast(`BOM 预检完成：${result.count} 项`);
  } catch (error) {
    $("bomApplyBtn").disabled = true;
    toast(error.message);
  }
}
async function searchMouser() {
  const q = $("mouserQuery").value.trim();
  if (!q) return toast("请输入型号或关键词");
  $("mouserStatus").textContent = "正在查询贸泽官方资料……";
  try {
    const result = await api(
      `/api/inventory/mouser-search?q=${encodeURIComponent(q)}`,
    );
    $("mouserStatus").textContent =
      `找到 ${result.count} 条结果 · 数据来源：${result.source}`;
    $("mouserResults").innerHTML =
      result.items
        .map(
          (item) =>
            `<article class="mouser-card"><b>${esc(item.manufacturer_part_no || item.mouser_part_no)}</b><small>${esc(item.manufacturer)} · ${esc(item.mouser_part_no)}</small><p>${esc(item.description)}</p><small>${esc(item.availability)} · MOQ ${esc(item.minimum_order || "—")} · ${esc(item.lead_time || "交期未知")}</small><div class="actions">${item.datasheet_url ? `<a class="btn" href="${esc(item.datasheet_url)}" target="_blank" rel="noopener noreferrer">数据手册</a>` : ""}${item.product_url ? `<a class="btn" href="${esc(item.product_url)}" target="_blank" rel="noopener noreferrer">贸泽页面</a>` : ""}</div></article>`,
        )
        .join("") || '<div class="empty">没有找到匹配结果</div>';
  } catch (error) {
    $("mouserStatus").textContent = error.message;
    toast(error.message);
  }
}
document.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-close]");
  if (close) $(close.dataset.close).close();
  const component = event.target.closest("[data-component]");
  if (component)
    openComponent(
      state.components.find((c) => c.id === component.dataset.component),
    );
  const decision = event.target.closest("[data-decision]");
  if (decision) {
    try {
      await api(
        `/api/inventory/movements/${encodeURIComponent(decision.dataset.movement)}/decision`,
        { method: "POST", body: { decision: decision.dataset.decision } },
      );
      toast(
        decision.dataset.decision === "approve" ? "申请已通过" : "申请已拒绝",
      );
      await loadData();
    } catch (error) {
      toast(error.message);
    }
  }
});
$("componentForm").addEventListener("submit", saveComponent);
$("movementForm").addEventListener("submit", saveMovement);
$("addComponentBtn").addEventListener("click", () => openComponent());
$("movementBtn").addEventListener("click", () => {
  $("movementForm").reset();
  $("movementDialog").showModal();
});
$("bomBtn").addEventListener("click", () => $("bomDialog").showModal());
$("bomPreviewBtn").addEventListener("click", () => bomImport(false));
$("bomApplyBtn").addEventListener("click", () => bomImport(true));
$("bomMode").addEventListener(
  "change",
  () => ($("bomApplyBtn").disabled = true),
);
$("mouserBtn").addEventListener("click", () => $("mouserDialog").showModal());
$("mouserSearchBtn").addEventListener("click", searchMouser);
$("mouserQuery").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchMouser();
  }
});
$("mouserConfigBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/inventory/mouser-config");
    $("mouserEnabled").value = String(data.enabled);
    $("mouserApiKey").value = "";
    $("mouserConfigDialog").showModal();
  } catch (error) {
    toast(error.message);
  }
});
$("mouserConfigForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/admin/inventory/mouser-config", {
      method: "PUT",
      body: {
        enabled: $("mouserEnabled").value === "true",
        api_key: $("mouserApiKey").value,
      },
    });
    $("mouserConfigDialog").close();
    toast("贸泽 API 配置已保存");
    await loadMeta();
  } catch (error) {
    toast(error.message);
  }
});
$("managerBtn").addEventListener("click", () => $("managerDialog").showModal());
$("saveManagersBtn").addEventListener("click", async () => {
  const user_ids = [...$("managerList").querySelectorAll("input:checked")].map(
    (el) => el.value,
  );
  try {
    await api("/api/admin/inventory/managers", {
      method: "PUT",
      body: { user_ids },
    });
    $("managerDialog").close();
    toast("负责人权限已保存");
    await loadMeta();
  } catch (error) {
    toast(error.message);
  }
});
$("deleteComponentBtn").addEventListener("click", () => {
  state.deleteId = $("componentId").value;
  $("confirmDialog").showModal();
});
$("confirmDeleteBtn").addEventListener("click", async () => {
  try {
    await api(
      `/api/inventory/components/${encodeURIComponent(state.deleteId)}`,
      { method: "DELETE" },
    );
    $("confirmDialog").close();
    $("componentDialog").close();
    toast("元件档案已删除");
    await loadData();
  } catch (error) {
    toast(error.message);
  }
});
$("refreshBtn").addEventListener("click", loadData);
$("componentSearch").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadData, 280);
});
$("categoryFilter").addEventListener("input", () => {
  clearTimeout(state.categoryTimer);
  state.categoryTimer = setTimeout(loadData, 280);
});
$("lowStockOnly").addEventListener("change", loadData);
(async () => {
  try {
    await loadMeta();
    await loadData();
  } catch (error) {
    toast(error.message);
  }
})();
