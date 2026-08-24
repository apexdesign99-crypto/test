/* AI LAND DESIGN — 画面ロジック。
   フォームの入力を /api/analyze に送り、返ってきた結果を描画する。
   算定は一切行わず、表示と入力の整形だけを担当する。 */

const form = document.getElementById("site-form");
const statusEl = document.getElementById("form-status");
const submitBtn = document.getElementById("submit-btn");
const roadsEl = document.getElementById("roads");
const resultBody = document.getElementById("result-body");
const placeholder = document.getElementById("placeholder");

let meta = null;
let samples = [];
let lastPayload = null;

/* ---------- 表示用フォーマッタ ---------- */

const fmtNum = (value, digits = 2) =>
  Number(value).toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });

function fmtJpy(value) {
  if (value === null || value === undefined) return "—";
  const oku = Math.floor(value / 100000000);
  const man = Math.floor((value % 100000000) / 10000);
  if (oku > 0) return `${oku}億${man.toLocaleString("ja-JP")}万円`;
  if (man > 0) return `${man.toLocaleString("ja-JP")}万円`;
  return `${value.toLocaleString("ja-JP")}円`;
}

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- 初期化 ---------- */

async function init() {
  const [metaRes, sampleRes] = await Promise.all([
    fetch("/api/meta").then((r) => r.json()),
    fetch("/api/samples").then((r) => r.json()),
  ]);
  meta = metaRes;
  samples = sampleRes.samples || [];

  fillSelect("use-district", meta.use_districts.map((u) => u.value), "第一種住居地域");
  fillSelect("fire-zone", meta.fire_zones, meta.fire_zones[0]);
  fillSelect("structure", meta.structures, meta.structures[0]);
  fillSelect("grade", meta.grades, "標準");

  const sampleSelect = document.getElementById("sample-select");
  samples.forEach((s) => {
    const option = document.createElement("option");
    option.value = s.id;
    option.textContent = s.label;
    sampleSelect.appendChild(option);
  });
  sampleSelect.addEventListener("change", (e) => applySample(e.target.value));

  addRoad({ width_m: 6, direction: "南", frontage_m: 14, is_setback_road: false, is_legal_road: true });
  bindEvents();
  updateAreaHint();
}

function fillSelect(id, values, selected) {
  const el = document.getElementById(id);
  el.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selected) option.selected = true;
    el.appendChild(option);
  });
}

function bindEvents() {
  document.getElementById("add-road").addEventListener("click", () => addRoad());

  document.querySelectorAll(".seg").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b === btn));
      const isRect = btn.dataset.shape === "rect";
      document.getElementById("shape-rect").classList.toggle("hidden", !isRect);
      document.getElementById("shape-polygon").classList.toggle("hidden", isRect);
      updateAreaHint();
    });
  });

  document.getElementById("use-district").addEventListener("change", (e) => {
    const info = meta.use_districts.find((u) => u.value === e.target.value);
    if (!info) return;
    form.bcr.value = Math.round(info.default_bcr * 100);
    form.far.value = Math.round(info.default_far * 100);
    form.height_limit_m.value = info.default_height_limit_m ?? "";
    form.wall_setback_m.value = info.is_low_rise ? 1 : 0;
    if (!info.allows_dwelling) {
      setStatus(`${info.value}では住宅を建築できません（法48条）。`, true);
    } else {
      setStatus("");
    }
  });

  ["width_m", "depth_m", "polygon"].forEach((name) => {
    form[name].addEventListener("input", updateAreaHint);
  });

  document.getElementById("fetch-market").addEventListener("click", fetchMarketPrice);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    analyze();
  });

  // 入力値が HTML の検証に引っかかると submit が発火しないため、理由を表示する。
  form.addEventListener(
    "invalid",
    (event) => {
      const field = event.target;
      const label = field.closest(".field")?.querySelector("span")?.textContent || field.name;
      setStatus(`${label.trim()}: ${field.validationMessage}`, true);
    },
    true
  );
}

/* ---------- 入力の読み取り ---------- */

function isRectMode() {
  return document.querySelector(".seg.active").dataset.shape === "rect";
}

function parsePolygon(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(/[,\s]+/).map(Number))
    .filter((pair) => pair.length >= 2 && pair.every((n) => Number.isFinite(n)))
    .map((pair) => [pair[0], pair[1]]);
}

function polygonArea(points) {
  let total = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    total += x1 * y2 - x2 * y1;
  }
  return Math.abs(total / 2);
}

function updateAreaHint() {
  let area = 0;
  if (isRectMode()) {
    area = (Number(form.width_m.value) || 0) * (Number(form.depth_m.value) || 0);
  } else {
    const points = parsePolygon(form.polygon.value);
    if (points.length >= 3) area = polygonArea(points);
  }
  document.getElementById("area-hint").textContent = area
    ? `敷地面積 ${fmtNum(area)} m²（${fmtNum(area / 3.305785)} 坪）`
    : "敷地面積を算出できません（3点以上の座標が必要です）";
}

function addRoad(values = {}) {
  const index = roadsEl.children.length;
  const row = document.createElement("div");
  row.className = "road-row";
  row.innerHTML = `
    <div class="road-head">
      <span>道路 ${index + 1}</span>
      <button type="button" class="link" data-remove>削除</button>
    </div>
    <div class="grid-3">
      <label class="field"><span>幅員 (m)</span><input type="number" data-road="width_m" value="${values.width_m ?? 4}" step="any" min="0.5"></label>
      <label class="field"><span>方位</span><select data-road="direction">${meta.directions
        .map((d) => `<option ${d === (values.direction ?? "南") ? "selected" : ""}>${d}</option>`)
        .join("")}</select></label>
      <label class="field"><span>接道長 (m)</span><input type="number" data-road="frontage_m" value="${values.frontage_m ?? 10}" step="any" min="0"></label>
    </div>
    <div class="checks">
      <label><input type="checkbox" data-road="is_setback_road" ${values.is_setback_road ? "checked" : ""}> 42条2項道路</label>
      <label><input type="checkbox" data-road="is_legal_road" ${values.is_legal_road === false ? "" : "checked"}> 建築基準法上の道路</label>
    </div>`;
  row.querySelector("[data-remove]").addEventListener("click", () => {
    row.remove();
    renumberRoads();
  });
  roadsEl.appendChild(row);
}

function renumberRoads() {
  [...roadsEl.children].forEach((row, i) => {
    row.querySelector(".road-head span").textContent = `道路 ${i + 1}`;
  });
}

function readRoads() {
  return [...roadsEl.children].map((row) => {
    const road = {};
    row.querySelectorAll("[data-road]").forEach((input) => {
      const key = input.dataset.road;
      road[key] = input.type === "checkbox" ? input.checked : Number(input.value);
      if (key === "direction") road[key] = input.value;
    });
    return road;
  });
}

function numberOrNull(value) {
  return value === "" || value === null ? null : Number(value);
}

function collectPayload() {
  const payload = {
    site_id: document.getElementById("sample-select").value || "custom",
    address: form.address.value,
    zoning: {
      use_district: form.use_district.value,
      building_coverage_ratio: Number(form.bcr.value) / 100,
      floor_area_ratio: Number(form.far.value) / 100,
      fire_zone: form.fire_zone.value,
      height_limit_m: numberOrNull(form.height_limit_m.value),
      wall_setback_m: Number(form.wall_setback_m.value) || 0,
      shadow_regulation: form.shadow_regulation.checked,
      is_corner_lot: form.is_corner_lot.checked,
      scenic_district: form.scenic_district.checked,
    },
    roads: readRoads(),
    hazard: {
      flood_depth_m: Number(form.flood_depth_m.value) || 0,
      landslide_risk: form.landslide_risk.checked,
      liquefaction_risk: form.liquefaction_risk.checked,
      quake_intensity_rank: Number(form.quake_intensity_rank.value) || 3,
    },
    land_price_jpy: numberOrNull(form.land_price_jpy.value),
    station_distance_m: numberOrNull(form.station_distance_m.value),
    options: {
      household_size: Number(form.household_size.value) || 4,
      structure: form.structure.value,
      grade: form.grade.value,
      floor_height_m: Number(form.floor_height_m.value) || 2.9,
      target_floor_area_m2: numberOrNull(form.target_floor_area_m2.value),
      market_unit_price_per_tsubo: numberOrNull(form.market_unit_price_per_tsubo.value),
    },
  };
  if (isRectMode()) {
    payload.width_m = Number(form.width_m.value);
    payload.depth_m = Number(form.depth_m.value);
  } else {
    payload.polygon = parsePolygon(form.polygon.value);
  }
  return payload;
}

function applySample(id) {
  const sample = samples.find((s) => s.id === id);
  if (!sample) return;
  const data = sample.request;
  form.address.value = data.address || "";
  form.polygon.value = (data.polygon || []).map((p) => `${p[0]}, ${p[1]}`).join("\n");
  document.querySelector('.seg[data-shape="polygon"]').click();
  form.use_district.value = data.zoning.use_district;
  form.bcr.value = Math.round(data.zoning.building_coverage_ratio * 100);
  form.far.value = Math.round(data.zoning.floor_area_ratio * 100);
  form.fire_zone.value = data.zoning.fire_zone;
  form.height_limit_m.value = data.zoning.height_limit_m ?? "";
  form.wall_setback_m.value = data.zoning.wall_setback_m ?? 0;
  form.is_corner_lot.checked = !!data.zoning.is_corner_lot;
  form.shadow_regulation.checked = !!data.zoning.shadow_regulation;
  form.scenic_district.checked = !!data.zoning.scenic_district;
  form.land_price_jpy.value = data.land_price_jpy ?? "";
  form.station_distance_m.value = data.station_distance_m ?? "";
  form.flood_depth_m.value = data.hazard?.flood_depth_m ?? 0;
  form.landslide_risk.checked = !!data.hazard?.landslide_risk;
  form.liquefaction_risk.checked = !!data.hazard?.liquefaction_risk;
  form.quake_intensity_rank.value = data.hazard?.quake_intensity_rank ?? 3;
  roadsEl.innerHTML = "";
  (data.roads || []).forEach((road) => addRoad(road));
  updateAreaHint();
  setStatus("サンプルを読み込みました。");
}

/** 所在地から市区町村名を取り出す（「東京都世田谷区代田1-1-1」→「世田谷区」）。 */
function municipalityOf(address) {
  const match = address.match(/^(?:.{2,3}[都道府県])?(.*?[市区町村])/);
  return match ? match[1] : address.trim().slice(0, 4);
}

/** 不動産 API 層から周辺相場（坪単価の中央値）を取得して入力欄に反映する。 */
async function fetchMarketPrice() {
  const query = municipalityOf(form.address.value.trim());
  if (!query) {
    setStatus("所在地を入力してください。", true);
    return;
  }
  setStatus("相場を取得中…");
  try {
    const response = await fetch(`/api/listings?address=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "取得に失敗しました");
    if (!data.median_unit_price_per_tsubo) {
      setStatus(`${query} の売地データが見つかりません。`, true);
      return;
    }
    form.market_unit_price_per_tsubo.value = data.median_unit_price_per_tsubo;
    setStatus(
      `${query} の相場: 坪 ${data.median_unit_price_per_tsubo.toLocaleString("ja-JP")} 円（${data.count} 件）`
    );
  } catch (error) {
    setStatus(error.message, true);
  }
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

/* ---------- 実行と描画 ---------- */

async function analyze() {
  const payload = collectPayload();
  lastPayload = payload;
  submitBtn.disabled = true;
  setStatus("計算中…");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((d) => `${d.loc?.slice(-1)}: ${d.msg}`).join(" / ")
        : data.detail;
      throw new Error(detail || "計算に失敗しました");
    }
    render(data);
    setStatus("完了");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    submitBtn.disabled = false;
  }
}

function render(data) {
  placeholder.classList.add("hidden");
  resultBody.classList.remove("hidden");
  renderDiagnosis(data);
  renderEnvelope(data);
  renderPlan(data);
  renderCost(data);
  renderExports(data);
}

function findingsList(findings) {
  if (!findings || !findings.length) return "";
  return `<ul class="findings">${findings
    .map((f) => `<li class="${f.level}">${escapeHtml(f.message)}</li>`)
    .join("")}</ul>`;
}

function renderDiagnosis(data) {
  const d = data.diagnosis;
  document.getElementById("diagnosis-card").innerHTML = `
    <h2 class="card-title">1. AI 土地診断</h2>
    <div class="score-head">
      <div class="rank rank-${d.rank}">${d.rank}</div>
      <div class="score-value">${fmtNum(d.total_score, 1)}<small>/ 100 点</small></div>
      <div class="hint">${escapeHtml(data.site.address || data.site.site_id)} ／ ${fmtNum(
        data.site.area_m2
      )} m²（${fmtNum(data.site.area_tsubo)} 坪）</div>
    </div>
    <div class="bars">
      ${d.items
        .map(
          (item) => `
        <div class="bar-row">
          <span>${escapeHtml(item.name)}</span>
          <div class="bar"><div style="width:${item.score}%"></div></div>
          <span class="num">${fmtNum(item.score, 1)}</span>
        </div>
        <div class="bar-comment">${escapeHtml(item.comment)}</div>`
        )
        .join("")}
    </div>
    ${findingsList(d.findings)}`;
}

function renderEnvelope(data) {
  const e = data.envelope;
  const banner = e.buildable
    ? '<div class="banner ok">建築可能</div>'
    : '<div class="banner ng">建築不可 — 下記の指摘を解消しない限り建築できません</div>';
  document.getElementById("envelope-card").innerHTML = `
    <h2 class="card-title">2. 建築可能判定</h2>
    ${banner}
    <div class="metrics">
      ${metric("有効敷地面積", `${fmtNum(e.effective_site_area_m2)}<small> m²</small>`)}
      ${metric("建築面積の上限", `${fmtNum(e.max_building_area_m2)}<small> m²</small>`, `建蔽率 ${(
        e.applied_coverage_ratio * 100
      ).toFixed(0)}%`)}
      ${metric("延べ面積の上限", `${fmtNum(e.max_floor_area_m2)}<small> m²</small>`, `容積率 ${(
        e.applied_far * 100
      ).toFixed(0)}%`)}
      ${metric("高さの上限", `${fmtNum(e.max_height_m)}<small> m</small>`)}
      ${metric("想定階数", `${e.max_storeys}<small> 階</small>`)}
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>高さ制限</th><th class="num">上限</th><th>根拠</th></tr></thead>
        <tbody>
          ${e.height_limits
            .map(
              (l) => `<tr><td>${escapeHtml(l.name)}</td><td class="num">${
                l.limit_m >= 999 ? "適用外" : `${fmtNum(l.limit_m)} m`
              }</td><td>${escapeHtml(l.detail)}</td></tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
    ${findingsList(e.findings)}`;
}

function metric(label, value, sub = "") {
  return `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div>${
    sub ? `<div class="label">${escapeHtml(sub)}</div>` : ""
  }</div>`;
}

function renderPlan(data) {
  const card = document.getElementById("plan-card");
  if (!data.building) {
    card.innerHTML = `<h2 class="card-title">3. AI 間取り / 3D 外観</h2>
      <p class="hint">建築可能判定で不可となったため、間取り以降は算出していません。</p>`;
    return;
  }
  const b = data.building;
  const plans = data.drawings.plans || [];
  const tabs = [
    ...plans.map((p) => ({ id: `plan-${p.storey}`, label: `${p.storey}階 平面図`, svg: p.svg })),
    { id: "exterior", label: "3D 外観", svg: data.drawings.exterior },
  ];

  card.innerHTML = `
    <h2 class="card-title">3. AI 間取り / 3D 外観</h2>
    <div class="metrics">
      ${metric("間取り", b.ldk_type)}
      ${metric("構造・階数", `${escapeHtml(b.structure)} ${b.storeys}<small> 階建</small>`)}
      ${metric("延べ面積", `${fmtNum(b.total_floor_area_m2)}<small> m²</small>`)}
      ${metric("建築面積", `${fmtNum(b.footprint_area_m2)}<small> m²</small>`)}
      ${metric("最高高さ", `${fmtNum(b.height_m)}<small> m</small>`, `屋根 ${escapeHtml(b.roof)}`)}
    </div>
    <div class="tabs">${tabs
      .map((t, i) => `<button type="button" class="tab ${i === 0 ? "active" : ""}" data-tab="${t.id}">${t.label}</button>`)
      .join("")}</div>
    <div class="drawing" id="drawing"></div>
    <div class="table-scroll" style="margin-top:14px">
      <table>
        <thead><tr><th>階</th><th>室名</th><th class="num">面積</th><th class="num">帖数</th><th class="num">寸法</th></tr></thead>
        <tbody>
          ${b.floors
            .flatMap((floor) =>
              floor.rooms.map(
                (room) =>
                  `<tr><td>${floor.storey}F</td><td>${escapeHtml(room.name)}</td><td class="num">${fmtNum(
                    room.area_m2
                  )} m²</td><td class="num">${fmtNum(room.jo, 1)} 帖</td><td class="num">${fmtNum(
                    room.w
                  )} × ${fmtNum(room.h)} m</td></tr>`
              )
            )
            .join("")}
        </tbody>
      </table>
    </div>`;

  const drawing = document.getElementById("drawing");
  const show = (id) => {
    const tab = tabs.find((t) => t.id === id);
    drawing.innerHTML = tab?.svg || '<p class="hint">図面がありません</p>';
  };
  card.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      card.querySelectorAll(".tab").forEach((b2) => b2.classList.toggle("active", b2 === btn));
      show(btn.dataset.tab);
    });
  });
  show(tabs[0].id);
}

function renderCost(data) {
  const card = document.getElementById("cost-card");
  if (!data.cost) {
    card.innerHTML = "";
    return;
  }
  const c = data.cost;
  const rows = (items) =>
    items
      .map(
        (i) =>
          `<tr><td>${escapeHtml(i.name)}</td><td class="num">${i.amount_jpy.toLocaleString(
            "ja-JP"
          )} 円</td><td>${escapeHtml(i.note || "")}</td></tr>`
      )
      .join("");

  card.innerHTML = `
    <h2 class="card-title">4. 建築費 / 総事業費</h2>
    <div class="metrics">
      ${metric("土地取得費", fmtJpy(c.land_price_jpy))}
      ${metric("建築費（税込）", fmtJpy(c.construction_total_jpy), `坪単価 ${(
        data.summary?.construction_unit_price_per_tsubo || 0
      ).toLocaleString("ja-JP")} 円`)}
      ${metric("諸費用", fmtJpy(c.other_total_jpy))}
      ${metric("総事業費", fmtJpy(c.project_total_jpy))}
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>建築費の内訳</th><th class="num">金額</th><th>備考</th></tr></thead>
        <tbody>
          ${rows(c.construction_items)}
          <tr><td>消費税</td><td class="num">${c.construction_tax_jpy.toLocaleString("ja-JP")} 円</td><td></td></tr>
          <tr class="total"><td>建築費 合計</td><td class="num">${c.construction_total_jpy.toLocaleString(
            "ja-JP"
          )} 円</td><td></td></tr>
        </tbody>
      </table>
    </div>
    <div class="table-scroll" style="margin-top:14px">
      <table>
        <thead><tr><th>諸費用・総事業費</th><th class="num">金額</th><th>備考</th></tr></thead>
        <tbody>
          <tr><td>土地取得費</td><td class="num">${c.land_price_jpy.toLocaleString("ja-JP")} 円</td><td></td></tr>
          <tr><td>建築費（税込）</td><td class="num">${c.construction_total_jpy.toLocaleString("ja-JP")} 円</td><td></td></tr>
          ${rows(c.other_items)}
          <tr class="total"><td>総事業費</td><td class="num">${c.project_total_jpy.toLocaleString(
            "ja-JP"
          )} 円</td><td></td></tr>
        </tbody>
      </table>
    </div>
    ${findingsList(data.compliance)}`;
}

function renderExports(data) {
  const buildable = !!data.building;
  const plans = data.drawings?.plans || [];
  const buttons = [
    { fmt: "ifc", label: "BIM モデル (.ifc)", needsBuilding: true },
    { fmt: "obj", label: "3D マッシング (.obj)", needsBuilding: true },
    { fmt: "exterior-svg", label: "外観図 (.svg)", needsBuilding: true },
    ...plans.map((p) => ({
      fmt: "plan-svg",
      storey: p.storey,
      label: `${p.storey}階 平面図 (.svg)`,
      needsBuilding: true,
    })),
    { fmt: "permit-md", label: "確認申請 準備資料 (.md)", needsBuilding: true },
    { fmt: "report-md", label: "事業性レポート (.md)", needsBuilding: false },
    { fmt: "report-json", label: "計算結果 (.json)", needsBuilding: false },
  ];

  document.getElementById("export-card").innerHTML = `
    <h2 class="card-title">5. 成果物のダウンロード</h2>
    <div class="exports">
      ${buttons
        .map(
          (b) =>
            `<button type="button" class="export-btn" data-fmt="${b.fmt}" data-storey="${
              b.storey || 1
            }" ${b.needsBuilding && !buildable ? "disabled" : ""}>${b.label}</button>`
        )
        .join("")}
    </div>
    <details style="margin-top:16px">
      <summary class="hint" style="cursor:pointer">レポート全文（Markdown）</summary>
      <pre class="markdown">${escapeHtml(data.markdown || "")}</pre>
    </details>
    ${
      data.permit_markdown
        ? `<details style="margin-top:8px"><summary class="hint" style="cursor:pointer">確認申請 準備資料</summary><pre class="markdown">${escapeHtml(
            data.permit_markdown
          )}</pre></details>`
        : ""
    }`;

  document.querySelectorAll(".export-btn").forEach((btn) => {
    btn.addEventListener("click", () => download(btn.dataset.fmt, Number(btn.dataset.storey)));
  });
}

async function download(fmt, storey) {
  if (!lastPayload) return;
  setStatus("生成中…");
  try {
    const response = await fetch(`/api/export/${fmt}?storey=${storey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastPayload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "ダウンロードに失敗しました");
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = match ? match[1] : `output.${fmt}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus("ダウンロードしました");
  } catch (error) {
    setStatus(error.message, true);
  }
}

init();
