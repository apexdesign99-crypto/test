/* データソース設定画面。API キーの登録・接続テストを行う。
   キーはサーバにのみ保存し、画面はマスクした値しか受け取らない。 */

const form = document.getElementById("settings-form");
const statusEl = document.getElementById("form-status");
const statusBody = document.getElementById("status-body");
const testBody = document.getElementById("test-body");

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

async function load() {
  const data = await fetch("/api/settings").then((r) => r.json());
  render(data);
}

function render(data) {
  form.zoning_api.value = data.zoning_api || "XKT013";
  form.live.checked = !!data.live;
  form.zoning_geojson.value = data.zoning_geojson || "";
  form.geocode_table.value = data.geocode_table || "";
  form.geocode_cache.value = data.geocode_cache || "";

  // キーが登録済みならマスク表示に切り替える（入力欄は隠す）
  const savedBlock = document.getElementById("key-saved-block");
  const inputBlock = document.getElementById("key-input-block");
  if (data.reinfolib_api_key_set) {
    document.getElementById("key-masked").textContent = data.reinfolib_api_key_masked;
    savedBlock.style.display = "";
    inputBlock.style.display = "none";
    form.reinfolib_api_key.value = "";
  } else {
    savedBlock.style.display = "none";
    inputBlock.style.display = "";
  }

  const rows = [
    ["用途地域 API", data.zoning_api, data.origins?.zoning_api],
    ["API キー", data.reinfolib_api_key_set ? data.reinfolib_api_key_masked : "未登録", data.origins?.reinfolib_api_key],
    ["外部 API の利用", data.live ? "有効" : "無効", data.origins?.live],
    ["用途地域 GeoJSON", data.zoning_geojson || "未設定", data.origins?.zoning_geojson],
    ["住所辞書", data.geocode_table || "未設定", data.origins?.geocode_table],
    ["ジオコーディングのキャッシュ", data.geocode_cache || "未設定", data.origins?.geocode_cache],
  ];

  statusBody.innerHTML =
    `<div class="banner ${data.ready ? "ok" : "ng"}">${
      data.ready
        ? "住所からの自動取得が使えます"
        : `住所からの自動取得は使えません — ${escapeHtml(data.reason || "設定が不足しています")}`
    }</div>` +
    `<div class="table-scroll"><table><thead><tr><th>項目</th><th>値</th><th>設定元</th></tr></thead><tbody>${rows
      .map(
        ([label, value, origin]) =>
          `<tr><td>${label}</td><td>${escapeHtml(value)}</td><td>${escapeHtml(origin || "—")}</td></tr>`
      )
      .join("")}</tbody></table></div>` +
    (data.sources?.length
      ? `<p class="hint">使用するデータソース:<br>${data.sources.map(escapeHtml).join("<br>")}</p>`
      : "") +
    `<p class="hint">設定ファイル: <code>${escapeHtml(data.config_path)}</code>${
      data.config_exists ? "（保存済み・権限 0600）" : "（未作成）"
    }</p>`;
}

function payload() {
  return {
    // 空文字は「変更なし」としてサーバ側で既存のキーを保持する
    reinfolib_api_key: form.reinfolib_api_key.value.trim(),
    zoning_api: form.zoning_api.value.trim() || "XKT013",
    live: form.live.checked,
    zoning_geojson: form.zoning_geojson.value.trim(),
    geocode_table: form.geocode_table.value.trim(),
    geocode_cache: form.geocode_cache.value.trim(),
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("保存中…");
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "保存に失敗しました");
    render(data);
    setStatus("保存しました");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("key-change").addEventListener("click", () => {
  document.getElementById("key-saved-block").style.display = "none";
  document.getElementById("key-input-block").style.display = "";
  form.reinfolib_api_key.focus();
});

document.getElementById("key-delete").addEventListener("click", async () => {
  if (!window.confirm("保存済みの API キーを削除します。よろしいですか？")) return;
  const data = await fetch("/api/settings/api-key", { method: "DELETE" }).then((r) => r.json());
  render(data);
  setStatus("API キーを削除しました");
});

document.getElementById("show-key").addEventListener("change", (event) => {
  form.reinfolib_api_key.type = event.target.checked ? "text" : "password";
});

document.getElementById("test-btn").addEventListener("click", async () => {
  setStatus("接続テスト中…");
  testBody.innerHTML = '<p class="hint">接続しています…</p>';
  try {
    const data = await fetch("/api/settings/test", { method: "POST" }).then((r) => r.json());
    testBody.innerHTML = `<ul class="findings">${data.results
      .map(
        (r) =>
          `<li class="${r.skipped ? "info" : r.ok ? "info" : "block"}">` +
          `<strong>${r.skipped ? "－" : r.ok ? "OK" : "NG"}</strong> ${escapeHtml(r.name)}<br>` +
          `<span class="hint">${escapeHtml(r.detail)}</span></li>`
      )
      .join("")}</ul>`;
    setStatus(data.ok ? "接続テスト: 問題なし" : "接続テスト: 失敗またはスキップがあります", !data.ok);
  } catch (error) {
    setStatus(error.message, true);
    testBody.innerHTML = `<p class="hint error">${escapeHtml(error.message)}</p>`;
  }
});

load();
