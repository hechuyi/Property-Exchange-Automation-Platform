import {
  resolveCatalogBusinessScopeSelection,
  resolveCatalogFamilyScopePlan,
} from "../state/businessScopeSelector.js";
import { runActiveJobGuardedAction } from "../state/activeJobActionGuard.js";
import { normalizeCatalogResource } from "../contracts/catalog.js";
import { businessTypeLabel, exchangeDisplayLabel, recordFamilyLabel } from "../constants/index.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function findBusinessLabel(businessOptions = [], businessId = "") {
  const targetBusinessId = asText(businessId);
  if (!targetBusinessId) return "";
  const matched = asArray(businessOptions).find((business) => asText(business?.business_id) === targetBusinessId);
  return asText(matched?.business_label);
}

function getCatalogSource({ getCatalog }) {
  if (typeof getCatalog === "function") {
    const catalog = getCatalog();
    if (catalog && typeof catalog === "object") {
      const normalizedCatalog = normalizeCatalogResource(catalog);
      if (normalizedCatalog.visible_families.length > 0) return catalog;
    }
  }
  return null;
}

function sourceLabelFromCatalog(catalog = {}, sourceId = "") {
  const normalizedSourceId = asText(sourceId);
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const source = normalizedCatalog.sources.find((item) => asText(item.source_id) === normalizedSourceId);
  const knownExchangeLabel = exchangeDisplayLabel(normalizedSourceId);
  return asText(source?.source_label) || knownExchangeLabel || normalizedSourceId;
}

export function createActionModals({
  $,
  API,
  escapeHtml,
  text,
  display,
  chooseLocalPath,
  getSettings,
  getCatalog,
  getOverview,
  runHistorical,
  runOneClick,
  runManualImport,
}) {
  async function loadCatalogAndBasic() {
    const currentSettings = asObject(typeof getSettings === "function" ? getSettings() : {});
    const cachedBasic = asObject(currentSettings.basic);
    const cachedCatalog = getCatalogSource({ getCatalog });
    let basic = cachedBasic;
    if (typeof API.getSettingsBasic === "function") {
      const loadedBasic = await API.getSettingsBasic();
      if (loadedBasic && typeof loadedBasic === "object" && !Array.isArray(loadedBasic)) {
        basic = loadedBasic;
      }
    }
    const catalog = cachedCatalog
      || (typeof API.getCatalog === "function" ? await API.getCatalog() : {});
    return {
      basic,
      catalog: asObject(catalog),
    };
  }

  function showHistoricalModal() {
    const modal = document.createElement("div");
    modal.id = "modal-historical";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:1000";
    modal.innerHTML = `
      <div style="background:var(--surface);border-radius:var(--radius);padding:var(--space-8);width:380px;box-shadow:var(--shadow)">
        <h2 style="font-family:var(--font-serif);font-size:20px;margin-bottom:var(--space-6)">历史区间任务</h2>
        <div id="historical-defaults" class="alert" style="margin-bottom:var(--space-4)">正在读取业务目录...</div>
        <div class="form-group" style="margin-bottom:var(--space-4)">
          <label>开始日期</label>
          <input type="date" id="hist-start" style="width:100%">
        </div>
        <div class="form-group" style="margin-bottom:var(--space-6)">
          <label>结束日期</label>
          <input type="date" id="hist-end" style="width:100%">
        </div>
        <div style="display:flex;gap:var(--space-3);justify-content:flex-end">
          <button id="hist-cancel" class="btn">取消</button>
          <button id="hist-confirm" class="btn btn-primary">确定</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const defaultsEl = $("#historical-defaults", modal);
    const confirmBtn = $("#hist-confirm", modal);
    confirmBtn.disabled = true;
    let submitting = false;
    let historicalFamilyScopes = [];

    const setBlocked = (message) => {
      confirmBtn.disabled = true;
      defaultsEl.className = "alert alert-warning";
      defaultsEl.innerHTML = escapeHtml(message);
    };
    const setReady = (familyScopes, basic) => {
      confirmBtn.disabled = false;
      defaultsEl.className = "alert";
      defaultsEl.innerHTML = `将执行 <strong>${escapeHtml(text(familyScopes.length))}</strong> 个业务范围，并发数 <strong>${escapeHtml(text(basic.default_concurrency || 1))}</strong>，包含公共资源网成交。`;
    };
    const setLoading = (loading) => {
      submitting = Boolean(loading);
      confirmBtn.disabled = Boolean(loading);
      $("#hist-cancel", modal).disabled = Boolean(loading);
      $("#hist-start", modal).disabled = Boolean(loading);
      $("#hist-end", modal).disabled = Boolean(loading);
    };

    const closeModal = () => {
      if (document.body.contains(modal)) document.body.removeChild(modal);
    };

    $("#hist-cancel", modal).addEventListener("click", closeModal);
    $("#hist-confirm", modal).addEventListener("click", async () => {
      if (submitting) return;
      const selectedFamilyScopes = historicalFamilyScopes.map((scope) => ({ ...scope }));
      if (!selectedFamilyScopes.length) {
        return;
      }
      const startDate = $("#hist-start", modal).value;
      const endDate = $("#hist-end", modal).value;
      const payload = {
        family_scopes: selectedFamilyScopes,
        include_public_resource: true,
      };
      if (startDate) payload.start_date = startDate;
      if (endDate) payload.end_date = endDate;
      setLoading(true);
      defaultsEl.className = "alert";
      defaultsEl.textContent = "正在启动历史区间任务...";
      try {
        await runActiveJobGuardedAction({
          fetchOverview: async () => (
            typeof API.getOverview === "function"
              ? await API.getOverview()
              : getOverview()
          ),
          actionLabel: "历史区间任务",
          execute: async () => runHistorical(payload),
        });
        closeModal();
      } catch (error) {
        setLoading(false);
        defaultsEl.className = "alert alert-danger";
        defaultsEl.textContent = `启动失败：${error?.message || "请稍后重试。"}`;
      }
    });

    (async () => {
      try {
        const loaded = await loadCatalogAndBasic();
        const plan = resolveCatalogFamilyScopePlan({
          catalog: loaded.catalog || {},
          surface: "one_click",
        });
        historicalFamilyScopes = asArray(plan.family_scopes);
        if (!historicalFamilyScopes.length) {
          setBlocked("当前业务目录中没有可下载的历史区间业务范围。");
          return;
        }
        setReady(historicalFamilyScopes, loaded.basic || {});
      } catch (error) {
        setBlocked(`读取业务目录失败：${error.message}`);
      }
    })();
  }

  async function showOneClickModal() {
    const modal = document.createElement("div");
    modal.id = "modal-oneclick";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:1000";
    modal.innerHTML = `
      <div style="background:var(--surface);border-radius:var(--radius);padding:var(--space-8);width:min(640px,calc(100vw - 32px));box-shadow:var(--shadow)">
        <h2 style="font-family:var(--font-serif);font-size:20px;margin-bottom:var(--space-4)">一键执行任务</h2>
        <p style="font-size:13px;color:var(--text-muted);margin:0 0 var(--space-4)">执行已声明的一键范围。可选填日期范围和抓取上限作为覆盖。</p>
        <div id="oneclick-defaults" class="alert" style="margin-bottom:var(--space-4)">正在读取业务目录...</div>
        <div class="form-row">
          <div class="form-group">
            <label>开始日期</label>
            <input type="date" id="oneclick-start-date" style="width:100%">
          </div>
          <div class="form-group">
            <label>结束日期</label>
            <input type="date" id="oneclick-end-date" style="width:100%">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>最大页数（可选）</label>
            <input type="number" id="oneclick-max-pages" min="1" step="1" placeholder="为空则使用系统默认">
          </div>
          <div class="form-group">
            <label>并发数（可选）</label>
            <input type="number" id="oneclick-concurrency" min="1" step="1" placeholder="为空则使用系统默认">
          </div>
        </div>
        <div id="oneclick-family-status" style="margin-bottom:var(--space-4)">
          <!-- filled dynamically with default scope status -->
        </div>
        <div id="oneclick-status" style="min-height:24px"></div>
        <div style="display:flex;gap:var(--space-3);justify-content:flex-end;flex-wrap:wrap">
          <button id="oneclick-cancel" class="btn">取消</button>
          <button id="oneclick-confirm" class="btn btn-primary">启动任务</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const defaultsEl = $("#oneclick-defaults", modal);
    const statusEl = $("#oneclick-status", modal);
    const familyStatusEl = $("#oneclick-family-status", modal);
    const confirmBtn = $("#oneclick-confirm", modal);
    const cancelBtn = $("#oneclick-cancel", modal);
    const startDateEl = $("#oneclick-start-date", modal);
    const endDateEl = $("#oneclick-end-date", modal);
    const maxPagesEl = $("#oneclick-max-pages", modal);
    const concurrencyEl = $("#oneclick-concurrency", modal);
    confirmBtn.disabled = true;
    let runtime = {
      state: "missing_default_scope",
      scope: null,
    };
    let oneClickCatalog = {};
    let oneClickFamilyScopes = [];

    const closeModal = () => {
      if (document.body.contains(modal)) document.body.removeChild(modal);
    };
    const setStatus = (message = "", kind = "info") => {
      if (!message) {
        statusEl.innerHTML = "";
        return;
      }
      const className = kind === "error" ? "alert alert-danger" : "alert";
      statusEl.innerHTML = `<div class="${className}" style="margin-bottom:var(--space-4)">${escapeHtml(message)}</div>`;
    };
    const setLoading = (loading) => {
      [confirmBtn, cancelBtn, startDateEl, endDateEl, maxPagesEl, concurrencyEl].forEach((el) => {
        if (el) el.disabled = loading;
      });
    };
    const setBlocked = (message) => {
      defaultsEl.className = "alert alert-warning";
      defaultsEl.innerHTML = escapeHtml(message);
      confirmBtn.disabled = true;
    };

    function scopeDisplayName(scope = {}) {
      const family = recordFamilyLabel(scope.record_family, scope.family_label || scope.record_family);
      const business = businessTypeLabel(scope.business_id, scope.business_label || scope.business_id);
      const exchangeLabel = sourceLabelFromCatalog(oneClickCatalog || runtime.catalog, scope.exchange);
      return `${family} / ${business} / ${exchangeLabel}`;
    }

    function renderOneClickScopePlan(scopes = [], status = "等待中") {
      if (!familyStatusEl) return;
      familyStatusEl.innerHTML = asArray(scopes).map((scope) => (
        `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:13px"><span style="font-weight:600">${escapeHtml(scopeDisplayName(scope))}</span><span style="color:var(--text-muted)">${escapeHtml(status)}</span></div>`
      )).join("");
    }

    cancelBtn.addEventListener("click", closeModal);

    try {
      const loaded = await loadCatalogAndBasic();
      oneClickCatalog = loaded.catalog || {};
      runtime = {
        state: "ready",
        scope: null,
        catalog: oneClickCatalog,
      };
      const plan = resolveCatalogFamilyScopePlan({
        catalog: oneClickCatalog,
        surface: "one_click",
      });
      oneClickFamilyScopes = asArray(plan.family_scopes);
      if (!oneClickFamilyScopes.length) {
        setBlocked("当前业务目录中没有可一键执行的业务范围。");
        return;
      }
      defaultsEl.className = "alert";
      defaultsEl.innerHTML = `将执行 <strong>${escapeHtml(text(oneClickFamilyScopes.length))}</strong> 个业务范围，包含公共资源网成交。`;
      renderOneClickScopePlan(oneClickFamilyScopes);
      confirmBtn.disabled = false;
    } catch (e) {
      defaultsEl.className = "alert alert-danger";
      defaultsEl.textContent = `读取业务目录失败：${e.message}`;
      confirmBtn.disabled = true;
    }

    confirmBtn.addEventListener("click", async () => {
      const userOverrides = {};
      const startDate = startDateEl.value.trim();
      const endDate = endDateEl.value.trim();
      const maxPages = maxPagesEl.value.trim();
      const concurrency = concurrencyEl.value.trim();
      if (startDate) userOverrides.start_date = startDate;
      if (endDate) userOverrides.end_date = endDate;
      if (maxPages) userOverrides.max_pages = Number(maxPages);
      if (concurrency) userOverrides.concurrency = Number(concurrency);

      setLoading(true);
      setStatus("");

      const selectedFamilyScopes = oneClickFamilyScopes.map((scope) => ({ ...scope }));
      if (!selectedFamilyScopes.length) {
        setLoading(false);
        setBlocked("当前业务目录中没有可一键执行的业务范围。");
        return;
      }
      renderOneClickScopePlan(selectedFamilyScopes, "执行中...");

      const aggregatePayload = {
        family_scopes: selectedFamilyScopes,
        include_public_resource: true,
        ...userOverrides,
      };

      try {
        await runActiveJobGuardedAction({
          fetchOverview: async () => (
            typeof API.getOverview === "function"
              ? await API.getOverview()
              : getOverview()
          ),
          actionLabel: "一键执行",
          execute: async () => runOneClick(aggregatePayload),
        });
        renderOneClickScopePlan(selectedFamilyScopes, "已启动");
      } catch (err) {
        const message = err?.message || "启动失败";
        renderOneClickScopePlan(selectedFamilyScopes, `失败：${message}`);
        setLoading(false);
        setStatus(`启动失败：${message}`, "error");
        return;
      }

      setLoading(false);
      setStatus("已启动一键执行任务。");
    });
  }

  function showManualImportModal() {
    const modal = document.createElement("div");
    const suggestedPath = text(getOverview().defaults?.manual_import_input_dir || "").trim();
    modal.id = "modal-manual-import";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:1000";
    modal.innerHTML = `
      <div style="background:var(--surface);border-radius:var(--radius);padding:var(--space-8);width:min(560px,calc(100vw - 32px));box-shadow:var(--shadow)">
        <h2 style="font-family:var(--font-serif);font-size:20px;margin-bottom:var(--space-4)">手动导入目录</h2>
        <p style="font-size:13px;color:var(--text-muted);margin:0 0 var(--space-4)">
          可直接粘贴本地目录路径，或点击“选择目录”调用系统文件选择器。
        </p>
        <div class="form-group" style="margin-bottom:var(--space-4)">
          <label>待导入目录</label>
          <input type="text" id="manual-import-dir" style="width:100%" value="${escapeHtml(suggestedPath)}" placeholder="/path/to/manual-html">
        </div>
        <div class="form-row" style="margin-bottom:var(--space-3)">
          <div class="form-group" style="flex:1">
            <label>业务类别（可选）</label>
            <select id="manual-import-family" style="width:100%">
              <option value="">正在读取业务目录...</option>
            </select>
          </div>
          <div class="form-group" style="flex:1">
            <label>业务 hint（可选）</label>
            <select id="manual-import-business" style="width:100%">
              <option value="">不指定业务 hint</option>
            </select>
          </div>
        </div>
        <div class="form-group" style="margin-bottom:var(--space-4)">
          <label>交易所 hint（选择业务后生效）</label>
          <select id="manual-import-exchange" style="width:100%">
            <option value="">不指定交易所</option>
          </select>
        </div>
        <div class="alert" style="margin-bottom:var(--space-4)">
          默认按“无业务 hint”提交，导入结果会保留 unknown truth；只有你显式选择业务与交易所时，manual-import 才会携带 scope。
        </div>
        <div id="manual-import-status" style="min-height:24px"></div>
        <div style="display:flex;gap:var(--space-3);justify-content:flex-end;flex-wrap:wrap">
          <button id="manual-import-cancel" class="btn">取消</button>
          <button id="manual-import-browse" class="btn">选择目录</button>
          <button id="manual-import-confirm" class="btn btn-primary">开始导入</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const closeModal = () => {
      if (document.body.contains(modal)) document.body.removeChild(modal);
    };
    const statusEl = $("#manual-import-status", modal);
    const inputEl = $("#manual-import-dir", modal);
    const familyEl = $("#manual-import-family", modal);
    const businessEl = $("#manual-import-business", modal);
    const exchangeEl = $("#manual-import-exchange", modal);
    const browseBtn = $("#manual-import-browse", modal);
    const confirmBtn = $("#manual-import-confirm", modal);
    let manualImportCatalog = {};
    let manualImportBasic = {};
    let manualImportCatalogLoadState = "loading";
    let manualImportCatalogLoadError = "";
    let manualImportEditor = resolveCatalogBusinessScopeSelection({
      allowImplicitExchangeSelection: false,
    });

    const setStatus = (message = "", kind = "info") => {
      if (!message) {
        statusEl.innerHTML = "";
        return;
      }
      const className = kind === "error" ? "alert alert-danger" : "alert";
      statusEl.innerHTML = `<div class="${className}" style="margin-bottom:var(--space-4)">${escapeHtml(message)}</div>`;
    };
    const setLoading = (loading) => {
      browseBtn.disabled = loading;
      confirmBtn.disabled = loading;
      inputEl.disabled = loading;
      if (familyEl) familyEl.disabled = loading;
      if (businessEl) businessEl.disabled = loading;
      if (exchangeEl) exchangeEl.disabled = loading;
    };
    const setCatalogLoadFailed = (message) => {
      manualImportCatalogLoadState = "failed";
      manualImportCatalogLoadError = message;
      confirmBtn.disabled = true;
      if (familyEl) familyEl.disabled = true;
      if (businessEl) businessEl.disabled = true;
      if (exchangeEl) exchangeEl.disabled = true;
      setStatus(message, "error");
    };
    const manualImportExchangeOptions = (editor) => {
      const recordFamily = asText(editor.selected_family_id);
      const businessId = asText(editor.selected_business_id);
      if (!recordFamily || !businessId) return [];
      const normalizedCatalog = normalizeCatalogResource(manualImportCatalog);
      const familyMatrix = asObject(normalizedCatalog.surface_source_matrix[recordFamily]);
      const businessMatrix = asObject(familyMatrix[businessId]);
      const hasRecordsContract = Object.prototype.hasOwnProperty.call(businessMatrix, "records");
      const catalogLabels = new Map(
        asArray(normalizedCatalog.sources)
          .map((source) => [asText(source.source_id), asText(source.source_label)])
          .filter(([value]) => value),
      );
      const sourceIds = hasRecordsContract
        ? asArray(businessMatrix.records).map((value) => asText(value)).filter((value) => value && value !== "all")
        : [];
      return sourceIds.map((sourceId) => [
        sourceId,
        catalogLabels.get(sourceId) || display(sourceId),
      ]);
    };
    const renderManualImportScopeEditor = (editor) => {
      const familyOptions = Array.isArray(editor.family_options) ? editor.family_options : [];
      const businessOptions = Array.isArray(editor.business_options) ? editor.business_options : [];
      if (familyEl) {
        familyEl.innerHTML = familyOptions.length
          ? familyOptions.map((family) => {
            const familyId = asText(family.family_id);
            const rawLabel = asText(family.family_label, familyId);
            const displayLabel = asText(family.family_display_label) || recordFamilyLabel(familyId, rawLabel);
            return `<option value="${escapeHtml(familyId)}">${escapeHtml(displayLabel)}</option>`;
          }).join("")
          : `<option value="">当前无可用业务类别</option>`;
        familyEl.value = editor.selected_family_id || familyOptions[0]?.family_id || "";
        familyEl.disabled = browseBtn.disabled || familyOptions.length === 0;
      }
      if (businessEl) {
        businessEl.innerHTML = [
          `<option value="">不指定业务 hint</option>`,
          ...businessOptions.map((business) => {
            const businessId = asText(business.business_id);
            const rawLabel = asText(business.business_label, businessId);
            const displayLabel = asText(business.business_display_label) || businessTypeLabel(businessId, rawLabel);
            return `<option value="${escapeHtml(businessId)}">${escapeHtml(displayLabel)}</option>`;
          }),
        ].join("");
        businessEl.value = editor.selected_business_id || "";
        businessEl.disabled = browseBtn.disabled || businessOptions.length === 0;
      }
      if (exchangeEl) {
        const hasExplicitBusinessSelection = Boolean(asText(editor.selected_business_id));
        const scopedExchangeOptions = hasExplicitBusinessSelection
          ? manualImportExchangeOptions(editor)
          : [];
        const exchangeOptions = [
          ["", "不指定交易所"],
          ...scopedExchangeOptions,
        ];
        exchangeEl.innerHTML = exchangeOptions
          .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
          .join("");
        const selectedExchange = hasExplicitBusinessSelection
          && exchangeOptions.some(([value]) => value === asText(editor.selected_exchange))
          ? asText(editor.selected_exchange)
          : "";
        exchangeEl.value = selectedExchange;
        exchangeEl.disabled = browseBtn.disabled || !hasExplicitBusinessSelection || scopedExchangeOptions.length === 0;
      }
    };
    const syncManualImportScopeEditor = ({
      selectedFamilyId = familyEl?.value,
      selectedBusinessId = businessEl?.value,
      selectedExchange = exchangeEl?.value,
    } = {}) => {
      manualImportEditor = resolveCatalogBusinessScopeSelection({
        catalog: manualImportCatalog,
        basicSettings: manualImportBasic,
        selectedFamilyId,
        selectedBusinessId,
        selectedExchange,
        allowImplicitExchangeSelection: false,
      });
      renderManualImportScopeEditor(manualImportEditor);
    };

    $("#manual-import-cancel", modal).addEventListener("click", closeModal);
    familyEl?.addEventListener("change", () => {
      syncManualImportScopeEditor({
        selectedFamilyId: familyEl.value,
        selectedBusinessId: "",
        selectedExchange: "",
      });
    });
    businessEl?.addEventListener("change", () => {
      syncManualImportScopeEditor({
        selectedFamilyId: familyEl?.value,
        selectedBusinessId: businessEl.value,
        selectedExchange: businessEl.value ? exchangeEl?.value : "",
      });
    });
    browseBtn.addEventListener("click", async () => {
      try {
        setStatus("");
        const selectedPath = await chooseLocalPath({
          selection_kind: "directory",
          prompt: "选择待导入网页目录",
          current_path: inputEl.value.trim(),
        });
        if (selectedPath) inputEl.value = selectedPath;
      } catch (e) {
        setStatus(`目录选择失败: ${e.message}`, "error");
      }
    });
    confirmBtn.addEventListener("click", async () => {
      if (manualImportCatalogLoadState !== "ready") {
        const message = manualImportCatalogLoadState === "failed"
          ? manualImportCatalogLoadError
          : "正在读取业务目录，请稍后再试。";
        confirmBtn.disabled = true;
        setStatus(message, "error");
        return;
      }
      const dir = inputEl.value.trim();
      if (!dir) {
        setStatus("请输入待导入目录路径。", "error");
        inputEl.focus();
        return;
      }
      const payload = { input_dir: dir };
      const businessId = asText(businessEl?.value);
      if (businessId) {
        const exchange = asText(exchangeEl?.value);
        if (!exchange || exchange === "all") {
          setStatus("显式业务 hint 需要同时选择交易所。", "error");
          exchangeEl?.focus?.();
          return;
        }
        const recordFamily = asText(familyEl?.value || manualImportEditor.selected_family_id);
        if (!recordFamily) {
          setStatus("显式业务 hint 缺少业务类别。", "error");
          familyEl?.focus?.();
          return;
        }
        payload.record_family = recordFamily;
        payload.business_id = businessId;
        const businessLabel = findBusinessLabel(manualImportEditor.business_options || [], businessId);
        if (businessLabel) payload.business_label = businessLabel;
        payload.exchange = exchange;
      }
      try {
        setLoading(true);
        setStatus("正在提交手动导入任务...");
        await runManualImport(payload);
        closeModal();
      } catch (e) {
        setLoading(false);
        setStatus(`导入失败: ${e.message}`, "error");
      }
    });
    inputEl.focus();
    inputEl.select();
    confirmBtn.disabled = true;
    syncManualImportScopeEditor({
      selectedExchange: "",
    });
    (async () => {
      try {
        const loaded = await loadCatalogAndBasic();
        manualImportCatalog = loaded.catalog || {};
        manualImportBasic = loaded.basic || {};
        syncManualImportScopeEditor({
          selectedFamilyId: familyEl?.value,
          selectedBusinessId: businessEl?.value,
          selectedExchange: exchangeEl?.value,
        });
        manualImportCatalogLoadState = "ready";
        manualImportCatalogLoadError = "";
        confirmBtn.disabled = false;
      } catch (error) {
        setCatalogLoadFailed(`读取业务目录失败：${error.message}`);
      }
    })();
  }

  return {
    showHistoricalModal,
    showOneClickModal,
    showManualImportModal,
  };
}
