const state = {
  config: { google_maps_browser_key: "", geocoding_provider: "nominatim" },
  settings: null,
  currentUploadId: null,
  page1: null,
  page2: null,
  pendingMapping: null, // {upload_token, headers, sample_rows, autodetected_mapping, field_defs}
  pendingDelete: null,  // {id, label} awaiting confirmation in the delete dialog
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function badge(text, level) {
  return el("span", { class: `badge ${level}` }, [text]);
}

function checkBadge(check, unitLabel) {
  if (!check || !check.applicable) return badge("No data", "na");
  const val = check.distance_m != null ? `${Math.round(check.distance_m)}m` : "";
  return badge(check.passed ? `Pass (${val})` : `Fail (${val})`, check.passed ? "ok" : "crit");
}

function providerLabel() {
  return state.config.geocoding_provider === "google" ? "Google Geocoding API (billed)" : "OpenStreetMap Nominatim (free)";
}

function statCard(label, ok, total, caption) {
  const pct = total > 0 ? Math.round((ok / total) * 100) : null;
  const level = pct == null ? "na" : pct >= 90 ? "ok" : pct >= 70 ? "warn" : "crit";
  return el("div", { class: `stat-card ${level}` }, [
    el("div", { class: "stat-label" }, [label]),
    el("div", { class: "stat-value" }, [pct == null ? "—" : `${pct}%`]),
    el("div", { class: "stat-caption" }, [caption]),
  ]);
}

// ------------------------------------------------------------ tabs

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#panel-${tab.dataset.tab}`).classList.add("active");
  });
});

// ------------------------------------------------------------ upload flow

$("#uploadBtn").addEventListener("click", () => $("#fileInput").click());
$("#fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const maxBytes = state.config.max_upload_bytes || 3000000;
  if (file.size > maxBytes) {
    toast(`CSV is too large. Maximum size is ${(maxBytes / 1000000).toFixed(1)} MB. Split it into smaller files.`);
    return;
  }
  const fd = new FormData();
  fd.append("file", file);
  try {
    const preview = await Api.postForm("/api/uploads/preview", fd);
    state.pendingMapping = preview;
    openMappingModal(preview);
  } catch (err) {
    toast("Couldn't read that CSV: " + err.message);
  }
});

function openMappingModal(preview) {
  $("#mappingSub").textContent = `${preview.filename} — ${preview.row_count} row${preview.row_count === 1 ? "" : "s"}. Match your CSV's columns below (fields marked * are required).`;
  const grid = $("#mappingFields");
  grid.innerHTML = "";
  const REQUIRED = ["fdp_lat", "fdp_lon", "cust_lat", "cust_lon"];
  for (const [key, def] of Object.entries(preview.field_defs)) {
    const select = el("select", { id: `map_${key}` });
    select.appendChild(el("option", { value: "" }, ["— not in this CSV —"]));
    for (const h of preview.headers) {
      const opt = el("option", { value: h }, [h]);
      if (preview.autodetected_mapping[key] === h) opt.selected = true;
      select.appendChild(opt);
    }
    const label = el("span", { class: REQUIRED.includes(key) ? "req" : "" }, [def.label]);
    grid.appendChild(el("label", { class: "field" }, [label, select]));
  }
  $("#mappingModal").hidden = false;
}

$("#mappingCancel").addEventListener("click", () => {
  $("#mappingModal").hidden = true;
  state.pendingMapping = null;
});

$("#mappingConfirm").addEventListener("click", async () => {
  const preview = state.pendingMapping;
  if (!preview) return;
  const mapping = {};
  for (const key of Object.keys(preview.field_defs)) {
    const v = $(`#map_${key}`).value;
    if (v) mapping[key] = v;
  }
  if (!mapping.fdp_lat || !mapping.fdp_lon || !mapping.cust_lat || !mapping.cust_lon) {
    toast("FDP and customer latitude/longitude are all required.");
    return;
  }
  const fd = new FormData();
  fd.append("upload_token", preview.upload_token);
  fd.append("mapping_json", JSON.stringify(mapping));
  try {
    const result = await Api.postForm("/api/uploads/confirm", fd);
    $("#mappingModal").hidden = true;
    state.config.geocoding_provider = result.geocoding_provider;
    toast(`Loaded ${result.row_count} row${result.row_count === 1 ? "" : "s"}.`);
    await refreshUploadList();
    $("#uploadSelect").value = String(result.upload_id);
    await loadUpload(result.upload_id);
  } catch (err) {
    toast("Couldn't run analysis: " + err.message);
  }
});

function showGeocodeBanner(text, pct) {
  $("#geocodeBanner").hidden = false;
  $("#geocodeBannerText").textContent = text;
  $("#geocodeBannerFill").style.width = `${pct}%`;
}
function hideGeocodeBanner() { $("#geocodeBanner").hidden = true; }

let geocodeGeneration = 0;
let geocodeTimer = null;
function stopGeocoding() {
  geocodeGeneration++;
  clearTimeout(geocodeTimer);
  hideGeocodeBanner();
}

function pollGeocoding(uploadId) {
  stopGeocoding();
  const generation = geocodeGeneration;
  const current = () => generation === geocodeGeneration && state.currentUploadId === uploadId;
  let completedBatch = false;
  let failures = 0;
  const tick = async () => {
    if (!current()) return;
    let delay = 1500;
    try {
      const s = await Api.post(`/api/uploads/${uploadId}/geocode-batch`);
      if (!current()) return;
      failures = 0;
      completedBatch = completedBatch || s.processed > 0;
      if (s.processed > 0) await loadPage2(uploadId);
      if (!current()) return;
      if (s.geocode_pending <= 0) {
        await loadPage2(uploadId);
        if (!current()) return;
        hideGeocodeBanner();
        if (completedBatch) toast("Address checks finished. Review the table for matches or geocoding errors.");
        return;
      }
      const pct = s.with_address > 0 ? Math.round((s.geocode_done / s.with_address) * 100) : 0;
      showGeocodeBanner(
        `Checking addresses via ${providerLabel()}: ${s.geocode_done}/${s.with_address} done. Keep this upload open; reopening it resumes unfinished checks.`, pct
      );
      delay = s.retry_after_ms || 1500;
    } catch (e) {
      if (!current()) return;
      if (e.status === 404) { hideGeocodeBanner(); return; }
      failures++;
      delay = Math.min(30000, 2000 * (2 ** Math.min(failures, 4)));
      showGeocodeBanner("Address checking interrupted. Saved progress is safe; retrying shortly...", 0);
    }
    if (current()) geocodeTimer = setTimeout(tick, delay);
  };
  showGeocodeBanner("Checking saved address progress...", 0);
  geocodeTimer = setTimeout(tick, 0);
}

// ------------------------------------------------------------ uploads list

function updateDeleteBtn() {
  $("#deleteBtn").disabled = !$("#uploadSelect").value;
}

async function refreshUploadList() {
  const uploads = await Api.get("/api/uploads");
  const sel = $("#uploadSelect");
  sel.innerHTML = "";
  if (!uploads.length) {
    sel.appendChild(el("option", { value: "" }, ["No uploads yet"]));
    updateDeleteBtn();
    return uploads;
  }
  for (const u of uploads) {
    sel.appendChild(el("option", { value: u.id }, [`${u.filename} (${u.row_count} rows)`]));
  }
  updateDeleteBtn();
  return uploads;
}
$("#uploadSelect").addEventListener("change", (e) => {
  updateDeleteBtn();
  if (e.target.value) loadUpload(e.target.value);
});

// ------------------------------------------------------------ delete upload

$("#deleteBtn").addEventListener("click", () => {
  const sel = $("#uploadSelect");
  if (!sel.value) return;
  const opt = sel.options[sel.selectedIndex];
  const label = opt ? opt.textContent.trim() : "";
  $("#deleteSub").textContent = `“${label}” and all of its FDP/customer rows will be permanently removed from the database. This cannot be undone.`;
  state.pendingDelete = { id: sel.value, label };
  $("#deleteModal").hidden = false;
});

$("#deleteCancel").addEventListener("click", () => {
  $("#deleteModal").hidden = true;
  state.pendingDelete = null;
});

$("#deleteConfirm").addEventListener("click", async () => {
  const pending = state.pendingDelete;
  if (!pending) return;
  $("#deleteModal").hidden = true;
  state.pendingDelete = null;
  try {
    await Api.delete(`/api/uploads/${pending.id}`);
    toast(`Deleted "${pending.label}".`);
    const uploads = await refreshUploadList();
    if (uploads.length) {
      $("#uploadSelect").value = String(uploads[0].id);
      await loadUpload(uploads[0].id);
    } else {
      stopGeocoding();
      state.currentUploadId = null;
      $("#panel-page1").innerHTML = "";
      $("#panel-page1").appendChild(renderEmptyHero());
      $("#panel-page2").innerHTML = "";
      $("#panel-page2").appendChild(renderEmptyHero());
    }
  } catch (err) {
    toast("Couldn't delete: " + err.message);
  }
});

async function loadUpload(uploadId) {
  stopGeocoding();
  state.currentUploadId = uploadId;
  await Promise.all([loadPage1(uploadId), loadPage2(uploadId), loadQuality(uploadId)]);
  if (state.currentUploadId === uploadId) pollGeocoding(uploadId);
}

// ------------------------------------------------------------ data quality

async function loadQuality(uploadId) {
  const box = $("#qualityBanner");
  box.innerHTML = "";
  try {
    const q = await Api.get(`/api/uploads/${uploadId}/quality`);
    if (!q.flagged_rows) return; // clean CSV - nothing to show
    box.appendChild(renderQualityCard(q));
  } catch (err) { /* no upload loaded yet, or a transient error - stay quiet */ }
}

function renderQualityCard(q) {
  const chevron = chevronIcon();
  const head = el("div", { class: "quality-head" }, [
    el("div", { class: "quality-head-left" }, [
      chevron,
      `${q.flagged_rows} of ${q.total_rows} row${q.total_rows === 1 ? "" : "s"} in this CSV had a data issue`,
    ]),
    badge(`${q.clean_rows} clean`, "ok"),
  ]);
  const rows = q.issues.map((issue) => {
    const examples = issue.examples.map((e) => e.label).join(", ");
    const more = issue.count > issue.examples.length ? ` +${issue.count - issue.examples.length} more` : "";
    return el("div", { class: "quality-row" }, [
      el("span", { class: "q-label" }, [issue.label]),
      el("span", { class: "q-count" }, [`${issue.count} row${issue.count === 1 ? "" : "s"}`]),
      el("div", { class: "q-examples" }, [`e.g. ${examples}${more}`]),
    ]);
  });
  const body = el("div", { class: "quality-body" }, rows);
  head.addEventListener("click", () => {
    body.classList.toggle("hidden-body");
    chevron.classList.toggle("collapsed");
  });
  return el("div", { class: "quality-card" }, [head, body]);
}

// ------------------------------------------------------------ settings

async function loadSettings() {
  state.settings = await Api.get("/api/settings");
  $("#setOverhead").value = state.settings.overhead_threshold_m;
  $("#setLine").value = state.settings.line_threshold_m;
  $("#setAddress").value = state.settings.address_tolerance_m;
}
$("#settingsBtn").addEventListener("click", () => { $("#settingsModal").hidden = false; });
$("#settingsCancel").addEventListener("click", () => { $("#settingsModal").hidden = true; loadSettings(); });
$("#settingsSave").addEventListener("click", async () => {
  const body = {
    overhead_threshold_m: Number($("#setOverhead").value),
    line_threshold_m: Number($("#setLine").value),
    address_tolerance_m: Number($("#setAddress").value),
  };
  await Api.put("/api/settings", body);
  state.settings = body;
  $("#settingsModal").hidden = true;
  toast("Thresholds updated.");
  if (state.currentUploadId) await loadUpload(state.currentUploadId);
});

// ------------------------------------------------------------ Page 1

async function loadPage1(uploadId) {
  const panel = $("#panel-page1");
  try {
    const data = await Api.analysis(`/api/uploads/${uploadId}/page1`, 1, () => state.currentUploadId === uploadId);
    if (!data) return;
    if (state.currentUploadId !== uploadId) return;
    state.page1 = data;
    renderPage1(data);
  } catch (err) {
    if (state.currentUploadId !== uploadId) return;
    panel.innerHTML = "";
    panel.appendChild(el("p", { class: "sub", role: "alert" }, ["Could not load all results: " + err.message]));
  }
}

function renderPage1(data) {
  const panel = $("#panel-page1");
  panel.innerHTML = "";

  const allFdps = [...data.ok_fdps, ...data.rejected_fdps];
  const allCustomers = allFdps.flatMap((f) => f.customers);
  const overheadApplicable = allCustomers.filter((c) => c.overhead.applicable);
  const overheadPass = overheadApplicable.filter((c) => c.overhead.passed);
  const lineApplicable = allCustomers.filter((c) => c.line.applicable);
  const linePass = lineApplicable.filter((c) => c.line.passed);

  panel.appendChild(el("div", { class: "stat-grid" }, [
    statCard("FDPs on land, in Sri Lanka", data.ok_fdps.length, allFdps.length, `${data.ok_fdps.length} of ${allFdps.length} FDPs`),
    statCard("Overhead distance within limit", overheadPass.length, overheadApplicable.length, `${overheadPass.length} of ${overheadApplicable.length} customers checked`),
    statCard("Line distance within limit", linePass.length, lineApplicable.length,
      lineApplicable.length ? `${linePass.length} of ${lineApplicable.length} customers checked` : "No line-distance column uploaded"),
  ]));

  panel.appendChild(el("div", { class: "section-title" }, [
    "FDPs on land, in Sri Lanka", el("span", { class: "count" }, [String(data.ok_fdps.length)]),
  ]));
  for (const fdp of data.ok_fdps) panel.appendChild(renderFdpCard(fdp, false));

  panel.appendChild(el("div", { class: "section-title rejected" }, [
    "Rejected FDPs (outside Sri Lanka or on a known water body)", el("span", { class: "count" }, [String(data.rejected_fdps.length)]),
  ]));
  if (!data.rejected_fdps.length) panel.appendChild(el("p", { class: "sub" }, ["None."]));
  for (const fdp of data.rejected_fdps) panel.appendChild(renderFdpCard(fdp, true));
}

function chevronIcon() {
  const span = el("span", { class: "chevron" });
  span.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16"><path fill="currentColor" d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  return span;
}

function renderFdpCard(fdp, rejected) {
  const card = el("div", { class: "fdp-card" + (rejected ? " rejected" : "") });
  const chevron = chevronIcon();
  const head = el("div", { class: "fdp-head" }, [
    el("div", { class: "fdp-head-left" }, [
      chevron,
      el("div", {}, [
        el("span", { class: "fdp-id" }, [fdp.ext_id || `FDP #${fdp.id}`]),
        el("div", { class: "fdp-meta" }, [
          fdp.lat != null ? `${fdp.lat.toFixed(5)}, ${fdp.lon.toFixed(5)}` : "no coordinates",
          rejected ? ` — ${fdp.rejection_reason}` : "",
          ` · ${fdp.customers.length} customer${fdp.customers.length === 1 ? "" : "s"}`,
        ]),
      ]),
    ]),
    rejected ? badge("Rejected", "crit") : badge("OK", "ok"),
  ]);
  head.addEventListener("click", () => {
    body.classList.toggle("hidden-body");
    chevron.classList.toggle("collapsed");
  });

  const table = el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      el("th", {}, ["Customer"]), el("th", {}, ["Overhead ≤ limit"]), el("th", {}, ["Line distance ≤ limit"]), el("th", {}, ["Map"]),
    ])]),
  ]);
  const tbody = el("tbody");
  for (const c of fdp.customers) {
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [c.name ? `${c.name} (${c.ext_id || c.id})` : (c.ext_id || `#${c.id}`)]),
      el("td", {}, [checkBadge(c.overhead)]),
      el("td", {}, [checkBadge(c.line)]),
      el("td", {}, [el("button", { class: "id-link", onclick: () => openPairMap(c.id) }, ["View on map"])]),
    ]));
  }
  table.appendChild(tbody);
  const body = el("div", { class: "fdp-body" }, [table]);

  card.appendChild(head);
  card.appendChild(body);
  return card;
}

// ------------------------------------------------------------ Page 2

async function loadPage2(uploadId) {
  const panel = $("#panel-page2");
  try {
    const data = await Api.analysis(`/api/uploads/${uploadId}/page2`, 2, () => state.currentUploadId === uploadId);
    if (!data) return;
    if (state.currentUploadId !== uploadId) return;
    state.page2 = data;
    renderPage2(data);
  } catch (err) {
    if (state.currentUploadId !== uploadId) return;
    panel.innerHTML = "";
    panel.appendChild(el("p", { class: "sub", role: "alert" }, ["Could not load all results: " + err.message]));
  }
}

function renderPage2(data) {
  const panel = $("#panel-page2");
  panel.innerHTML = "";

  const allCustomers = [...data.ok_customers, ...data.rejected_customers];
  const matchApplicable = allCustomers.filter((c) => c.address_match.applicable);
  const matchPass = matchApplicable.filter((c) => c.address_match.passed);

  panel.appendChild(el("div", { class: "stat-grid" }, [
    statCard("Customers on land, in Sri Lanka", data.ok_customers.length, allCustomers.length, `${data.ok_customers.length} of ${allCustomers.length} customers`),
    statCard("Within road distance of address", matchPass.length, matchApplicable.length,
      matchApplicable.length ? `${matchPass.length} of ${matchApplicable.length} geocoded` : "No addresses geocoded yet"),
  ]));

  panel.appendChild(el("div", { class: "provider-note" }, [
    `Addresses are geocoded via ${providerLabel()}. Each address is first located, then matched to its road - a customer passes if they're within tolerance of that road, not a pinpoint rooftop. A "*" means the road couldn't be resolved, so that row falls back to the exact geocoded point instead.`,
  ]));

  panel.appendChild(el("div", { class: "section-title" }, [
    "Customers on land, in Sri Lanka", el("span", { class: "count" }, [String(data.ok_customers.length)]),
  ]));
  panel.appendChild(renderCustomerTable(data.ok_customers, false));

  panel.appendChild(el("div", { class: "section-title rejected" }, [
    "Rejected customers (outside Sri Lanka or on a known water body)", el("span", { class: "count" }, [String(data.rejected_customers.length)]),
  ]));
  if (!data.rejected_customers.length) panel.appendChild(el("p", { class: "sub" }, ["None."]));
  else panel.appendChild(renderCustomerTable(data.rejected_customers, true));
}

function addressMatchBadge(m) {
  if (!m.applicable) {
    const label = m.geocode_status === "pending" ? "Geocoding…" : m.geocode_status === "not_run" ? "No address" : m.geocode_status === "not_found" ? "Address not found" : m.geocode_status === "error" ? "Geocode error" : m.geocode_status === "missing_coordinates" ? "Missing coordinates" : "No data";
    return badge(label, "na");
  }
  const suffix = m.basis === "road" ? "" : " *";
  return badge(m.passed ? `Match (${m.percent}%)${suffix}` : `Mismatch (${m.percent}%)${suffix}`, m.passed ? "ok" : "crit");
}

function renderCustomerTable(customers, rejected) {
  const table = el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      el("th", {}, ["Customer"]), el("th", {}, ["Address"]),
      rejected ? el("th", {}, ["Reason"]) : null,
      el("th", {}, ["Within road distance"]), el("th", {}, ["Map"]),
    ])]),
  ]);
  const tbody = el("tbody");
  for (const c of customers) {
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [c.name ? `${c.name} (${c.ext_id || c.id})` : (c.ext_id || `#${c.id}`)]),
      el("td", {}, [c.address || "—"]),
      rejected ? el("td", {}, [c.rejection_reason || ""]) : null,
      el("td", {}, [addressMatchBadge(c.address_match)]),
      el("td", {}, [el("button", { class: "id-link", onclick: () => openCircleMap(c.id) }, ["View on map"])]),
    ]));
  }
  table.appendChild(tbody);
  return table;
}

function renderEmptyHero() {
  const icon = el("div", { class: "empty-icon" });
  icon.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M12 2C7.6 2 4 5.6 4 10c0 5.4 7 11.5 7.3 11.8a1 1 0 0 0 1.4 0C13 21.5 20 15.4 20 10c0-4.4-3.6-8-8-8zm0 11a3 3 0 1 1 0-6 3 3 0 0 1 0 6z"/></svg>';
  return el("div", { class: "empty-hero" }, [
    icon,
    el("h2", {}, ["Upload a CSV to get started"]),
    el("p", {}, ["Needs at minimum: FDP latitude/longitude and customer latitude/longitude. Optional: FDP ID, customer ID/name, address, line distance."]),
    el("button", { class: "btn", onclick: () => $("#fileInput").click() }, ["Upload CSV"]),
  ]);
}

// ------------------------------------------------------------ map modal

async function ensureMapsLoaded() {
  return GMaps.load(state.config.google_maps_browser_key);
}

function openMapModalShell(title, sub) {
  $("#mapModalTitle").textContent = title;
  $("#mapModalSub").textContent = sub || "";
  $("#mapCanvas").innerHTML = "";
  $("#mapLegend").innerHTML = "";
  $("#mapModal").hidden = false;
}
$("#mapModalClose").addEventListener("click", () => { $("#mapModal").hidden = true; });

async function openPairMap(customerId) {
  try {
    const detail = await Api.get(`/api/customers/${customerId}/map`);
    if (detail.customer.lat == null || detail.customer.lon == null) {
      openMapModalShell(`Customer ${detail.customer.ext_id || detail.customer.id}`, "This customer's row is missing (or has invalid) coordinates in the uploaded CSV, so it can't be plotted.");
      $("#mapCanvas").innerHTML = `<div class="map-info">Fix the customer's lat/lon in the CSV and re-upload to include it in the checks.</div>`;
      return;
    }
    const overheadThreshold = state.settings ? state.settings.overhead_threshold_m : 500;
    openMapModalShell(
      `Customer ${detail.customer.ext_id || detail.customer.id} → FDP ${detail.fdp ? (detail.fdp.ext_id || detail.fdp.id) : "?"}`,
      detail.fdp ? `The dashed line is the straight-line (overhead) distance measured for the check on this page. The red ring around the customer shows the ${overheadThreshold}m overhead threshold.` : "This customer has no matched FDP."
    );
    const mapsLib = await ensureMapsLoaded();
    GMaps.renderPair($("#mapCanvas"), mapsLib, { customer: detail.customer, fdp: detail.fdp, ring_radius_m: overheadThreshold });
    $("#mapLegend").appendChild(el("div", {}, [
      el("span", {}, [el("span", { class: "dot", style: "background:#ea4335" }), `Customer, ringed at the ${overheadThreshold}m overhead threshold`]),
      el("span", {}, [el("span", { class: "dot", style: "background:#1e8e3e" }), "FDP (green)"]),
    ]));
  } catch (err) {
    $("#mapCanvas").innerHTML = `<div class="map-error">${err.message}</div>`;
    $("#mapModal").hidden = false;
  }
}

async function openCircleMap(customerId) {
  try {
    const detail = await Api.get(`/api/customers/${customerId}/map`);
    if (detail.customer.lat == null || detail.customer.lon == null) {
      openMapModalShell(`Customer ${detail.customer.ext_id || detail.customer.id}`, "This customer's row is missing (or has invalid) coordinates in the uploaded CSV, so it can't be plotted or checked against its address.");
      $("#mapCanvas").innerHTML = `<div class="map-info">Fix the customer's lat/lon in the CSV and re-upload to include it in the checks.</div>`;
      return;
    }
    if (detail.geocode.status !== "ok") {
      const reason = detail.geocode.status === "pending" ? "This address hasn't been geocoded yet - check back shortly."
        : detail.geocode.status === "not_found" ? "The geocoder couldn't find a match for this address."
        : detail.geocode.status === "not_run" ? "This customer has no address to geocode."
        : "Geocoding failed for this address.";
      openMapModalShell(`Customer ${detail.customer.ext_id || detail.customer.id}`, reason);
      $("#mapCanvas").innerHTML = `<div class="map-info">${detail.geocode.display_name || ""}</div>`;
      return;
    }
    const radius = state.settings ? state.settings.address_tolerance_m : 500;
    const onRoad = detail.geocode.road_status === "ok" && detail.geocode.road_lat != null;
    const center = onRoad
      ? { lat: detail.geocode.road_lat, lon: detail.geocode.road_lon }
      : { lat: detail.geocode.lat, lon: detail.geocode.lon };
    const distance = onRoad ? detail.geocode.road_distance_m : detail.geocode.distance_m;
    const roadName = detail.geocode.road_name;
    const basisText = onRoad
      ? `${Math.round(distance)}m from ${roadName || "the address's road"} (tolerance = ${radius}m).`
      : `Couldn't resolve a specific road for this address, so this falls back to the exact geocoded point - ${Math.round(distance)}m away (tolerance = ${radius}m).`;
    openMapModalShell(
      `Customer ${detail.customer.ext_id || detail.customer.id}`,
      `Address: ${detail.customer.address || "—"} · Geocoded to: ${detail.geocode.display_name || ""} · ${basisText}`
    );
    const mapsLib = await ensureMapsLoaded();
    GMaps.renderCircle($("#mapCanvas"), mapsLib, {
      center,
      center_label: onRoad ? "R" : "A",
      center_title: onRoad ? `Road: ${roadName || ""}` : "Geocoded address",
      customer: detail.customer,
      radius_m: radius,
    });
    $("#mapLegend").appendChild(el("div", {}, [
      el("span", {}, [el("span", { class: "dot", style: "background:#1a73e8" }), onRoad ? `${roadName || "Address's road"} (circle = tolerance radius)` : "Geocoded address (circle = tolerance radius)"]),
      el("span", {}, [el("span", { class: "dot", style: "background:#ea4335" }), "Customer's uploaded coordinates (red, highlighted)"]),
    ]));
  } catch (err) {
    $("#mapCanvas").innerHTML = `<div class="map-error">${err.message}</div>`;
    $("#mapModal").hidden = false;
  }
}

// ------------------------------------------------------------ init

(async function init() {
  state.config = await Api.get("/api/config");
  await loadSettings();
  await refreshUploadList();
  $("#panel-page1").appendChild(renderEmptyHero());
  $("#panel-page2").appendChild(renderEmptyHero());
  const uploads = await Api.get("/api/uploads");
  if (uploads.length) {
    $("#uploadSelect").value = String(uploads[0].id);
    await loadUpload(uploads[0].id);
  }
})();
