"""HTML/CSS/JS for the admin panel - setup, login, and the key-management
panel itself. Same string-constant pattern as scripts/lib/dashboard_assets.py:
plain (non-f) strings for CSS/JS so callers can splice them into an f-string
without escaping braces. Palette matches report/KP_Healthcare_Plan.html's
:root tokens (deep teal-ink ground, burnt-ochre accent) for visual
continuity between the dashboard and the admin panel.
"""
import html

DISPLAY_NAMES = {
    "anthropic": "Claude (Anthropic)",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "grok": "Grok (xAI)",
    "groq": "Groq",
}

ADMIN_CSS = r"""
:root {
  color-scheme: light;
  --ink: #16211f;
  --ink-soft: #48534f;
  --muted: #7c8580;
  --paper: #f3f6f4;
  --panel: #ffffff;
  --line: rgba(22,33,31,0.13);
  --accent: #a85a17;
  --accent-ink: #6e3b0e;
  --accent-2: #2d6e64;
  --danger: #b3392b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --ink: #eaeeec;
    --ink-soft: #b7c0bb;
    --muted: #8a9490;
    --paper: #111815;
    --panel: #182420;
    --line: rgba(234,238,236,0.14);
    --accent: #dd9247;
    --accent-ink: #f2c692;
    --accent-2: #62b0a2;
    --danger: #e2685a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--paper);
  color: var(--ink);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
h1 {
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Noto Serif", serif;
  color: var(--ink);
}
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 2rem;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 8px 24px rgba(22,33,31,0.08);
}
.panel-card { max-width: 640px; }
label { display: block; font-size: 0.85rem; color: var(--ink-soft); margin-top: 0.75rem; }
input[type="password"], input[type="text"] {
  width: 100%;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.95rem;
  margin: 0.35rem 0 1rem;
}
button {
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  cursor: pointer;
}
button.primary { background: var(--accent); color: #fff; }
button.danger { background: var(--danger); color: #fff; }
button.secondary { background: var(--panel); color: var(--ink); border: 1px solid var(--line); }
.error { color: var(--danger); font-size: 0.85rem; margin-bottom: 0.75rem; }
.provider-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.provider-row:last-child { border-bottom: none; }
.provider-name { font-weight: 600; min-width: 140px; }
.provider-hint { color: var(--muted); font-size: 0.85rem; flex: 1; min-width: 120px; }
.provider-status { font-size: 0.8rem; min-width: 90px; }
.provider-status.ok { color: var(--accent-2); }
.provider-status.bad { color: var(--danger); }
.db-browser-input { width: 100%; min-width: 6rem; box-sizing: border-box; }
.db-browser-row-status { display: none; margin-left: 0.5rem; font-size: 0.8rem; }
.db-browser-row-status.ok { display: inline; color: var(--accent-2); }
.db-browser-row-status.error { display: inline; color: var(--danger); }
.provider-actions { display: flex; gap: 0.4rem; align-items: center; }
.provider-actions input { margin: 0; width: 160px; }
.upload-section { margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--line); }
.upload-section h2 { font-size: 1rem; margin: 0 0 0.25rem; }
.upload-section .hint { color: var(--muted); font-size: 0.85rem; margin: 0 0 0.75rem; }
#extract-file-input { display: block; margin-bottom: 0.75rem; }
#extract-status { display: none; }
#extract-result {
  display: block;
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  resize: vertical;
}
#supplemental-instruction, #supplemental-provider {
  display: block;
  width: 100%;
  margin: 0.5rem 0;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.85rem;
  font-family: inherit;
}
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
#metric-file-input { display: block; margin-bottom: 0.75rem; }
#metric-instruction, #metric-provider {
  display: block;
  width: 100%;
  margin: 0.5rem 0;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.85rem;
  font-family: inherit;
}
#metric-status { display: none; }
#metric-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
#db-table-select {
  display: block;
  width: 100%;
  margin: 0.5rem 0;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.85rem;
  font-family: inherit;
}
#db-connection-status { display: none; }
#db-connection-status.ok { color: var(--accent-2); display: block; }
#db-connection-status.bad { color: var(--danger); display: block; }
#db-ingest-status { display: none; }
#db-preview-result {
  display: block;
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  resize: vertical;
}
#db-ingest-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
.records-table-wrap { overflow-x: auto; margin-top: 0.75rem; }
.records-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.records-table th, .records-table td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
.records-table th { color: var(--ink-soft); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.02em; }
.records-empty { color: var(--muted); text-align: center; padding: 0.75rem; white-space: normal; }
#supplemental-records-status, #override-records-status { display: none; margin-top: 0.5rem; }
#telegram-status { display: none; margin-top: 0.5rem; font-size: 0.85rem; }
#telegram-status.ok { color: var(--accent-2); display: block; }
#telegram-status.bad { color: var(--danger); display: block; }
#bot-facilities-status { display: none; margin-top: 0.5rem; }
.custom-table-block { border: 1px solid #ddd; border-radius: 6px; padding: 1rem; margin-top: 1rem; }
.custom-table-block h3 { margin-top: 0; }
.column-chip { display: inline-flex; align-items: center; gap: 0.4rem; border: 1px solid #ccc; border-radius: 999px; padding: 0.15rem 0.6rem; margin: 0.2rem 0.3rem 0.2rem 0; font-size: 0.85rem; }
.column-chip button { border: none; background: none; color: #b00; cursor: pointer; font-weight: bold; padding: 0; }
.new-column-row { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; align-items: center; }
#custom-tables-status { display: none; margin-top: 0.5rem; }
.pending-rows-grid { margin: 0.5rem 0; }
.pending-data-row { display: flex; gap: 0.5rem; margin-bottom: 0.4rem; align-items: center; }
.pending-data-row input { flex: 1; }
"""

ADMIN_JS = r"""
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function apiCall(method, url, body) {
    var options = { method: method };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (res) {
      return res.json().then(function (data) { return { status: res.status, data: data }; });
    });
  }

  function postFormData(url, formData) {
    return fetch(url, { method: "POST", body: formData }).then(function (res) {
      return res.json().then(function (data) { return { status: res.status, data: data }; });
    });
  }

  function showError(statusEl, text) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.display = "block";
  }

  // Every admin-panel action button (Extract, Add to Report, Apply Update,
  // DB Preview, DB Ingest, Telegram Save, Propose Schema, Create Table) used
  // to repeat the same ~25-40 line skeleton: disable the button and show
  // busy text, make a request, restore the button, then render success or
  // show an error - with several of them missing the network-failure
  // .catch() their siblings had. wireBusyButton() is that skeleton, once.
  //   btn         - the button element
  //   requestFn() - returns a Promise<{status, data}> (apiCall/postFormData shape)
  //   statusEl    - error paragraph, hidden/shown automatically
  //   busyText    - button text while the request is in flight
  //   idleText    - button text once it's done (defaults to the button's own current text)
  //   beforeRequest() - optional; return false to abort (after showing its own error via statusEl)
  //   onSuccess(result) - called when result.status === 200
  //   failureText - fallback error text when the server didn't send one
  function wireBusyButton(btn, config) {
    var idleText = config.idleText || btn.textContent;
    btn.addEventListener("click", function () {
      if (config.statusEl) config.statusEl.style.display = "none";
      if (config.beforeRequest && config.beforeRequest() === false) return;
      btn.disabled = true;
      btn.textContent = config.busyText;
      config.requestFn()
        .then(function (result) {
          btn.disabled = false;
          btn.textContent = idleText;
          if (result.status === 200) {
            config.onSuccess(result);
          } else {
            showError(config.statusEl, (result.data && result.data.detail) || config.failureText || "Request failed");
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = idleText;
          showError(config.statusEl, "Request failed: " + err);
        });
    });
  }

  // Shared by every "Added N record(s): ..." success block (Add to Report,
  // Apply Update, DB Ingest) - previously identical (or near-identical, for
  // Apply Update's "Applied N update(s)" wording), copy-pasted 3 times.
  function renderAddedRecordsSummary(result, resultEl, formatRow, verb, noun) {
    var added = (result.data && result.data.added) || [];
    var summary = added.map(formatRow).join("<br>");
    resultEl.innerHTML = "<p>" + (verb || "Added") + " " + added.length + " " + (noun || "record(s)") + ":</p><p>" + summary + "</p>";
    if (result.data && result.data.rebuild_warning) {
      resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
    }
  }

  function showEmptyRow(tbody, columnCount) {
    var emptyTr = document.createElement("tr");
    var emptyTd = document.createElement("td");
    emptyTd.colSpan = columnCount + 1;
    emptyTd.className = "records-empty";
    emptyTd.textContent = "No records yet.";
    emptyTr.appendChild(emptyTd);
    tbody.appendChild(emptyTr);
  }

  function renderRecordRow(record, options, statusEl) {
    var tr = document.createElement("tr");
    options.columns.forEach(function (col) {
      var td = document.createElement("td");
      td.textContent = record[col] == null ? "" : String(record[col]);
      tr.appendChild(td);
    });
    var actionTd = document.createElement("td");
    var deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger delete-record-btn";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", function () {
      if (deleteBtn.getAttribute("data-confirming") !== "true") {
        deleteBtn.setAttribute("data-confirming", "true");
        deleteBtn.textContent = "Confirm delete?";
        return;
      }
      deleteBtn.disabled = true;
      deleteBtn.textContent = "Deleting...";
      fetch(options.deleteUrlPrefix + encodeURIComponent(record.id), { method: "DELETE" })
        .then(function (res) {
          return res.json().then(function (data) { return { status: res.status, data: data }; });
        })
        .then(function (result) {
          if (result.status === 200 || result.status === 404) {
            var parent = tr.parentNode;
            if (parent) {
              parent.removeChild(tr);
              if (!parent.querySelector("tr")) {
                showEmptyRow(parent, options.columns.length);
              }
            }
            if (result.data && result.data.rebuild_warning) {
              statusEl.textContent = result.data.rebuild_warning;
              statusEl.style.display = "block";
            }
          } else {
            deleteBtn.removeAttribute("data-confirming");
            deleteBtn.disabled = false;
            deleteBtn.textContent = "Delete";
            statusEl.textContent = (result.data && result.data.detail) || "Delete failed";
            statusEl.style.display = "block";
          }
        })
        .catch(function (err) {
          deleteBtn.removeAttribute("data-confirming");
          deleteBtn.disabled = false;
          deleteBtn.textContent = "Delete";
          statusEl.textContent = "Request failed: " + err;
          statusEl.style.display = "block";
        });
    });
    actionTd.appendChild(deleteBtn);
    tr.appendChild(actionTd);
    return tr;
  }

  function initRecordsTable(options) {
    var tbody = byId(options.tbodyId);
    var statusEl = byId(options.statusId);
    if (!tbody) return;
    apiCall("GET", options.listUrl).then(function (result) {
      tbody.innerHTML = "";
      var records = (result.data && result.data.records) || [];
      if (!records.length) {
        showEmptyRow(tbody, options.columns.length);
        return;
      }
      records.forEach(function (record) {
        tbody.appendChild(renderRecordRow(record, options, statusEl));
      });
    });
  }

  document.addEventListener("click", function (evt) {
    document.querySelectorAll('.delete-record-btn[data-confirming="true"]').forEach(function (btn) {
      if (btn !== evt.target) {
        btn.removeAttribute("data-confirming");
        btn.textContent = "Delete";
      }
    });
    document.querySelectorAll('button[data-confirming="true"]').forEach(function (btn) {
      if (btn !== evt.target && /^confirm/i.test(btn.textContent)) {
        btn.removeAttribute("data-confirming");
        btn.textContent = btn.textContent.toLowerCase().indexOf("table") !== -1 ? "Delete Table" : "×";
      }
    });
  });

  function refreshSupplementalRecords() {
    initRecordsTable({
      listUrl: "/admin/api/supplemental-data/records",
      deleteUrlPrefix: "/admin/api/supplemental-data/records/",
      tbodyId: "supplemental-records-tbody",
      statusId: "supplemental-records-status",
      columns: ["district", "facility", "category", "label", "detail", "source_document", "added_at"],
    });
  }

  function refreshOverrideRecords() {
    initRecordsTable({
      listUrl: "/admin/api/metric-overrides/records",
      deleteUrlPrefix: "/admin/api/metric-overrides/records/",
      tbodyId: "override-records-tbody",
      statusId: "override-records-status",
      columns: ["district", "file", "column", "value", "reason", "added_at"],
    });
  }

  function refreshBotFacilities() {
    initRecordsTable({
      listUrl: "/admin/api/bot-facilities/records",
      deleteUrlPrefix: "/admin/api/bot-facilities/records/",
      tbodyId: "bot-facilities-tbody",
      statusId: "bot-facilities-status",
      columns: ["name", "district", "category", "lat", "lon", "added_at", "added_by"],
    });
  }

  var COLUMN_TYPES = ["text", "number", "date"];

  function newColumnRow(label, type) {
    var row = document.createElement("div");
    row.className = "new-column-row";
    var labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.placeholder = "Column name";
    labelInput.className = "new-column-label";
    labelInput.value = label || "";
    var typeSelect = document.createElement("select");
    typeSelect.className = "new-column-type";
    COLUMN_TYPES.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      if (t === type) opt.selected = true;
      typeSelect.appendChild(opt);
    });
    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "secondary";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", function () { row.remove(); });
    row.appendChild(labelInput);
    row.appendChild(typeSelect);
    row.appendChild(removeBtn);
    return row;
  }

  function collectNewTableColumns() {
    var rows = document.querySelectorAll("#new-table-columns .new-column-row");
    var columns = [];
    rows.forEach(function (row) {
      var label = row.querySelector(".new-column-label").value.trim();
      var type = row.querySelector(".new-column-type").value;
      if (label) columns.push({ label: label, type: type });
    });
    return columns;
  }

  function newDataRow(table, values) {
    values = values || {};
    var row = document.createElement("div");
    row.className = "pending-data-row";
    table.columns.forEach(function (col) {
      var input = document.createElement("input");
      input.className = "pending-row-input";
      input.dataset.column = col.column_name;
      input.placeholder = col.label;
      if (col.column_type === "number") {
        input.type = "number";
        input.step = "any";
      } else if (col.column_type === "date") {
        input.type = "date";
      } else {
        input.type = "text";
      }
      var value = values[col.column_name];
      if (value !== undefined && value !== null) {
        input.value = value;
      }
      row.appendChild(input);
    });
    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "secondary";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", function () { row.remove(); });
    row.appendChild(removeBtn);
    return row;
  }

  function collectPendingRows(gridEl) {
    var rows = [];
    gridEl.querySelectorAll(".pending-data-row").forEach(function (rowEl) {
      var values = {};
      rowEl.querySelectorAll(".pending-row-input").forEach(function (input) {
        values[input.dataset.column] = input.value;
      });
      rows.push(values);
    });
    return rows;
  }

  function renderCustomTableBlock(table) {
    var block = document.createElement("div");
    block.className = "custom-table-block";

    var heading = document.createElement("h3");
    heading.textContent = table.label;
    block.appendChild(heading);

    var deleteTableBtn = document.createElement("button");
    deleteTableBtn.type = "button";
    deleteTableBtn.className = "danger";
    deleteTableBtn.textContent = "Delete Table";
    deleteTableBtn.addEventListener("click", function () {
      if (deleteTableBtn.getAttribute("data-confirming") !== "true") {
        deleteTableBtn.setAttribute("data-confirming", "true");
        deleteTableBtn.textContent = "Confirm delete table?";
        return;
      }
      apiCall("DELETE", "/admin/api/custom-data/tables/" + encodeURIComponent(table.id)).then(function () {
        refreshCustomTables();
      });
    });
    block.appendChild(deleteTableBtn);

    var columnsWrap = document.createElement("div");
    table.columns.forEach(function (col) {
      var chip = document.createElement("span");
      chip.className = "column-chip";
      chip.textContent = col.label + " (" + col.column_type + ") ";
      var removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", function () {
        if (removeBtn.getAttribute("data-confirming") !== "true") {
          removeBtn.setAttribute("data-confirming", "true");
          removeBtn.textContent = "confirm?";
          return;
        }
        apiCall(
          "DELETE",
          "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/columns/" + encodeURIComponent(col.id)
        ).then(function () { refreshCustomTables(); });
      });
      chip.appendChild(removeBtn);
      columnsWrap.appendChild(chip);
    });
    block.appendChild(columnsWrap);

    var addColLabel = document.createElement("input");
    addColLabel.type = "text";
    addColLabel.placeholder = "New column name";
    var addColType = document.createElement("select");
    COLUMN_TYPES.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      addColType.appendChild(opt);
    });
    var addColBtn = document.createElement("button");
    addColBtn.type = "button";
    addColBtn.className = "secondary";
    addColBtn.textContent = "Add Column";
    addColBtn.addEventListener("click", function () {
      var label = addColLabel.value.trim();
      if (!label) return;
      apiCall("POST", "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/columns", {
        label: label, type: addColType.value,
      }).then(function () { refreshCustomTables(); });
    });
    block.appendChild(addColLabel);
    block.appendChild(addColType);
    block.appendChild(addColBtn);

    var pendingGrid = document.createElement("div");
    pendingGrid.className = "pending-rows-grid";

    var addRowBtn = document.createElement("button");
    addRowBtn.type = "button";
    addRowBtn.className = "secondary";
    addRowBtn.textContent = "+ Add Row";
    addRowBtn.addEventListener("click", function () {
      pendingGrid.appendChild(newDataRow(table));
    });

    var fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv";
    var instructionInput = document.createElement("textarea");
    instructionInput.rows = 2;
    instructionInput.placeholder = "Instruction (optional)";
    var providerSelect = document.createElement("select");
    providerSelect.innerHTML = document.getElementById("supplemental-provider").innerHTML;
    var previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.className = "secondary";
    previewBtn.textContent = "Preview with AI";
    var pendingStatus = document.createElement("p");
    pendingStatus.className = "error";
    previewBtn.addEventListener("click", function () {
      if (!fileInput.files.length) {
        pendingStatus.textContent = "Choose a file first";
        pendingStatus.style.display = "block";
        return;
      }
      var formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("provider", providerSelect.value);
      formData.append("instruction", instructionInput.value);
      previewBtn.disabled = true;
      previewBtn.textContent = "Previewing...";
      fetch("/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/preview", {
        method: "POST", body: formData,
      })
        .then(function (res) { return res.json().then(function (data) { return { status: res.status, data: data }; }); })
        .then(function (result) {
          previewBtn.disabled = false;
          previewBtn.textContent = "Preview with AI";
          if (result.status === 200) {
            pendingStatus.style.display = "none";
            (result.data.rows || []).forEach(function (values) {
              pendingGrid.appendChild(newDataRow(table, values));
            });
          } else {
            pendingStatus.textContent = (result.data && result.data.detail) || "Preview failed";
            pendingStatus.style.display = "block";
          }
        });
    });

    var confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "primary";
    confirmBtn.textContent = "Confirm & Add All Rows";
    confirmBtn.addEventListener("click", function () {
      var rows = collectPendingRows(pendingGrid);
      if (!rows.length) {
        pendingStatus.textContent = "Add or preview at least one row first";
        pendingStatus.style.display = "block";
        return;
      }
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Adding...";
      apiCall("POST", "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/records", {
        provider: providerSelect.value, rows: rows,
      }).then(function (result) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Confirm & Add All Rows";
        if (result.status === 200) {
          refreshCustomTables();
        } else {
          pendingStatus.textContent = (result.data && result.data.detail) || "Add failed";
          pendingStatus.style.display = "block";
        }
      });
    });

    block.appendChild(addRowBtn);
    block.appendChild(fileInput);
    block.appendChild(instructionInput);
    block.appendChild(providerSelect);
    block.appendChild(previewBtn);
    block.appendChild(pendingGrid);
    block.appendChild(confirmBtn);
    block.appendChild(pendingStatus);

    var recordsWrap = document.createElement("div");
    recordsWrap.className = "records-table-wrap";
    var recordsTable = document.createElement("table");
    recordsTable.className = "records-table";
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    table.columns.forEach(function (col) {
      var th = document.createElement("th");
      th.textContent = col.label;
      headRow.appendChild(th);
    });
    headRow.appendChild(document.createElement("th"));
    thead.appendChild(headRow);
    var tbody = document.createElement("tbody");
    tbody.id = "custom-table-tbody-" + table.id;
    recordsTable.appendChild(thead);
    recordsTable.appendChild(tbody);
    recordsWrap.appendChild(recordsTable);
    block.appendChild(recordsWrap);
    var recordsStatus = document.createElement("p");
    recordsStatus.id = "custom-table-status-" + table.id;
    recordsStatus.className = "error";
    block.appendChild(recordsStatus);

    return block;
  }

  function refreshCustomTables() {
    var container = byId("custom-tables-container");
    if (!container) return;
    apiCall("GET", "/admin/api/custom-data/tables").then(function (result) {
      container.innerHTML = "";
      var tables = (result.data && result.data.tables) || [];
      tables.forEach(function (table) {
        // initRecordsTable() looks up its tbody via document.getElementById,
        // so the block must already be attached to the document before
        // calling it - calling it from inside renderCustomTableBlock()
        // (before this appendChild) silently found nothing, since a
        // detached DOM subtree isn't visible to getElementById.
        container.appendChild(renderCustomTableBlock(table));
        initRecordsTable({
          listUrl: "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/records",
          deleteUrlPrefix: "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/records/",
          tbodyId: "custom-table-tbody-" + table.id,
          statusId: "custom-table-status-" + table.id,
          columns: table.columns.map(function (col) { return col.column_name; }),
        });
      });
    });
  }

  var DB_BROWSER_READONLY_COLUMNS = ["id", "added_at", "created_at"];

  function refreshDbBrowserTables() {
    var select = byId("db-browser-table-select");
    if (!select) return;
    apiCall("GET", "/admin/api/db-browser/tables").then(function (result) {
      var tables = (result.data && result.data.tables) || [];
      select.innerHTML = '<option value="">Select a table...</option>';
      tables.forEach(function (t) {
        var opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        select.appendChild(opt);
      });
    });
  }

  function _wireDbBrowserSaveButton(saveBtn, statusEl, tr, row, table) {
    wireBusyButton(saveBtn, {
      statusEl: statusEl,
      busyText: "Saving...",
      beforeRequest: function () {
        var fields = {};
        tr.querySelectorAll(".db-browser-input").forEach(function (input) {
          var original = row[input.dataset.column] == null ? "" : String(row[input.dataset.column]);
          if (input.value !== original) fields[input.dataset.column] = input.value;
        });
        if (!Object.keys(fields).length) return false;
        tr._pendingFields = fields;
      },
      requestFn: function () {
        return apiCall(
          "PUT",
          "/admin/api/db-browser/tables/" + encodeURIComponent(table) + "/rows/" + encodeURIComponent(row.id),
          tr._pendingFields,
        );
      },
      onSuccess: function (result) {
        Object.keys(tr._pendingFields).forEach(function (col) { row[col] = tr._pendingFields[col]; });
        statusEl.textContent = (result.data && result.data.rebuild_warning) || "Saved.";
        statusEl.className = "db-browser-row-status " + (result.data && result.data.rebuild_warning ? "error" : "ok");
      },
    });
  }

  function loadDbBrowserTable(table) {
    var content = byId("db-browser-content");
    content.innerHTML = "";
    if (!table) return;
    content.textContent = "Loading...";
    apiCall("GET", "/admin/api/db-browser/tables/" + encodeURIComponent(table) + "/rows").then(function (result) {
      if (result.status !== 200) {
        content.textContent = (result.data && result.data.detail) || "Failed to load table";
        return;
      }
      var columns = result.data.columns;
      var rows = result.data.rows;
      content.innerHTML = "";
      if (!rows.length) {
        content.textContent = "No rows yet.";
        return;
      }
      var wrap = document.createElement("div");
      wrap.className = "records-table-wrap";
      var tableEl = document.createElement("table");
      tableEl.className = "records-table";
      var thead = document.createElement("thead");
      var headRow = document.createElement("tr");
      columns.forEach(function (col) {
        var th = document.createElement("th");
        th.textContent = col.name;
        headRow.appendChild(th);
      });
      headRow.appendChild(document.createElement("th"));
      thead.appendChild(headRow);
      tableEl.appendChild(thead);
      var tbody = document.createElement("tbody");
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        columns.forEach(function (col) {
          var td = document.createElement("td");
          if (DB_BROWSER_READONLY_COLUMNS.indexOf(col.name) !== -1) {
            td.textContent = row[col.name] == null ? "" : String(row[col.name]);
          } else {
            var input = document.createElement("input");
            input.type = "text";
            input.className = "db-browser-input";
            input.dataset.column = col.name;
            input.value = row[col.name] == null ? "" : String(row[col.name]);
            td.appendChild(input);
          }
          tr.appendChild(td);
        });
        var actionTd = document.createElement("td");
        var saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.className = "secondary";
        saveBtn.textContent = "Save";
        var statusSpan = document.createElement("span");
        statusSpan.className = "db-browser-row-status";
        _wireDbBrowserSaveButton(saveBtn, statusSpan, tr, row, table);
        actionTd.appendChild(saveBtn);
        actionTd.appendChild(statusSpan);
        tr.appendChild(actionTd);
        tbody.appendChild(tr);
      });
      tableEl.appendChild(tbody);
      wrap.appendChild(tableEl);
      content.appendChild(wrap);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".provider-row").forEach(function (row) {
      var provider = row.getAttribute("data-provider");
      var input = row.querySelector("input");
      var statusEl = row.querySelector(".provider-status");
      var saveBtn = row.querySelector(".save-btn");
      var deleteBtn = row.querySelector(".delete-btn");
      var testBtn = row.querySelector(".test-btn");

      function setStatus(ok, text) {
        statusEl.textContent = text;
        statusEl.className = "provider-status " + (ok ? "ok" : "bad");
      }

      saveBtn.addEventListener("click", function () {
        var value = input.value.trim();
        if (!value) return;
        apiCall("PUT", "/admin/api/keys/" + provider, { api_key: value }).then(function (result) {
          if (result.status === 200) {
            input.value = "";
            window.location.reload();
          } else {
            setStatus(false, (result.data && result.data.detail) || "Save failed");
          }
        });
      });

      deleteBtn.addEventListener("click", function () {
        apiCall("DELETE", "/admin/api/keys/" + provider).then(function () {
          window.location.reload();
        });
      });

      testBtn.addEventListener("click", function () {
        var value = input.value.trim();
        var body = value ? { api_key: value } : undefined;
        setStatus(true, "Testing...");
        apiCall("POST", "/admin/api/keys/" + provider + "/test", body).then(function (result) {
          setStatus(!!(result.data && result.data.ok), (result.data && result.data.detail) || "Unknown error");
        });
      });
    });

    var logoutBtn = byId("logout-btn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", function () {
        apiCall("POST", "/admin/logout").then(function () {
          window.location.href = "/admin";
        });
      });
    }

    var extractBtn = byId("extract-btn");
    if (extractBtn) {
      var extractStatusEl = byId("extract-status");
      var extractResultEl = byId("extract-result");
      wireBusyButton(extractBtn, {
        statusEl: extractStatusEl,
        busyText: "Extracting...",
        beforeRequest: function () {
          extractResultEl.value = "";
          if (!byId("extract-file-input").files[0]) {
            showError(extractStatusEl, "Choose a file first");
            return false;
          }
        },
        requestFn: function () {
          var formData = new FormData();
          formData.append("file", byId("extract-file-input").files[0]);
          return postFormData("/admin/api/extract", formData);
        },
        onSuccess: function (result) { extractResultEl.value = result.data.text; },
        failureText: "Extraction failed",
      });
    }

    var addToReportBtn = byId("add-to-report-btn");
    if (addToReportBtn) {
      var supplementalStatusEl = byId("supplemental-status");
      var supplementalResultEl = byId("supplemental-result");
      wireBusyButton(addToReportBtn, {
        statusEl: supplementalStatusEl,
        busyText: "Adding...",
        beforeRequest: function () {
          supplementalResultEl.innerHTML = "";
          if (!byId("extract-file-input").files[0]) {
            showError(supplementalStatusEl, "Choose a file first");
            return false;
          }
        },
        requestFn: function () {
          var formData = new FormData();
          formData.append("file", byId("extract-file-input").files[0]);
          formData.append("provider", byId("supplemental-provider").value);
          formData.append("instruction", byId("supplemental-instruction").value);
          return postFormData("/admin/api/supplemental-data", formData);
        },
        onSuccess: function (result) {
          renderAddedRecordsSummary(result, supplementalResultEl, function (r) {
            var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
            return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
          });
          refreshSupplementalRecords();
        },
      });
    }

    var applyMetricUpdateBtn = byId("apply-metric-update-btn");
    if (applyMetricUpdateBtn) {
      var metricStatusEl = byId("metric-status");
      var metricResultEl = byId("metric-result");
      wireBusyButton(applyMetricUpdateBtn, {
        statusEl: metricStatusEl,
        busyText: "Applying...",
        beforeRequest: function () {
          metricResultEl.innerHTML = "";
          if (!byId("metric-file-input").files[0]) {
            showError(metricStatusEl, "Choose a file first");
            return false;
          }
        },
        requestFn: function () {
          var formData = new FormData();
          formData.append("file", byId("metric-file-input").files[0]);
          formData.append("provider", byId("metric-provider").value);
          formData.append("instruction", byId("metric-instruction").value);
          return postFormData("/admin/api/metric-overrides", formData);
        },
        onSuccess: function (result) {
          renderAddedRecordsSummary(result, metricResultEl, function (r) {
            return escapeHtml(r.district) + " / " + escapeHtml(r.column) + ": now " + escapeHtml(r.value);
          }, "Applied", "update(s)");
          refreshOverrideRecords();
        },
      });
    }

    function loadDbTables() {
      var select = byId("db-table-select");
      if (!select) return;
      apiCall("GET", "/admin/api/db/tables").then(function (result) {
        if (result.status === 200 && result.data && result.data.tables) {
          select.innerHTML = '<option value="">Select a table...</option>';
          result.data.tables.forEach(function (t) {
            var opt = document.createElement("option");
            opt.value = t;
            opt.textContent = t;
            select.appendChild(opt);
          });
        }
      });
    }

    var dbConnectBtn = byId("db-connect-btn");
    if (dbConnectBtn) {
      loadDbTables();

      dbConnectBtn.addEventListener("click", function () {
        var statusEl = byId("db-connection-status");
        statusEl.style.display = "none";
        var body = {
          host: byId("db-host").value.trim(),
          port: parseInt(byId("db-port").value, 10) || 5432,
          database: byId("db-database").value.trim(),
          user: byId("db-user").value.trim(),
          password: byId("db-password").value,
          sslmode: byId("db-sslmode").value.trim(),
        };
        dbConnectBtn.disabled = true;
        dbConnectBtn.textContent = "Testing...";
        apiCall("POST", "/admin/api/db/connection", body)
          .then(function (result) {
            dbConnectBtn.disabled = false;
            dbConnectBtn.textContent = "Save & Test Connection";
            statusEl.textContent = (result.data && result.data.detail) || "Unknown error";
            statusEl.className = result.data && result.data.ok ? "ok" : "bad";
            if (result.data && result.data.ok) {
              loadDbTables();
            }
          })
          .catch(function (err) {
            dbConnectBtn.disabled = false;
            dbConnectBtn.textContent = "Save & Test Connection";
            statusEl.textContent = "Request failed: " + err;
            statusEl.className = "bad";
          });
      });
    }

    var dbPreviewBtn = byId("db-preview-btn");
    if (dbPreviewBtn) {
      var dbIngestStatusEl = byId("db-ingest-status");
      var dbPreviewResultEl = byId("db-preview-result");
      wireBusyButton(dbPreviewBtn, {
        statusEl: dbIngestStatusEl,
        busyText: "Previewing...",
        beforeRequest: function () {
          dbPreviewResultEl.value = "";
          if (!byId("db-table-select").value) {
            showError(dbIngestStatusEl, "Choose a table first");
            return false;
          }
        },
        requestFn: function () {
          return apiCall("POST", "/admin/api/db/ingest", { table: byId("db-table-select").value, preview: true });
        },
        onSuccess: function (result) { dbPreviewResultEl.value = result.data.text; },
        failureText: "Preview failed",
      });
    }

    var dbIngestBtn = byId("db-ingest-btn");
    if (dbIngestBtn) {
      var dbIngestResultEl = byId("db-ingest-result");
      wireBusyButton(dbIngestBtn, {
        statusEl: byId("db-ingest-status"),
        busyText: "Adding...",
        beforeRequest: function () {
          dbIngestResultEl.innerHTML = "";
          if (!byId("db-table-select").value) {
            showError(byId("db-ingest-status"), "Choose a table first");
            return false;
          }
        },
        requestFn: function () {
          return apiCall("POST", "/admin/api/db/ingest", {
            table: byId("db-table-select").value,
            instruction: byId("db-instruction").value,
            provider: byId("db-provider").value,
          });
        },
        onSuccess: function (result) {
          renderAddedRecordsSummary(result, dbIngestResultEl, function (r) {
            var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
            return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
          });
          refreshSupplementalRecords();
        },
      });
    }

    function loadTelegramStatus() {
      var statusEl = byId("telegram-status");
      if (!statusEl) return;
      apiCall("GET", "/admin/api/telegram/config").then(function (result) {
        if (result.data && result.data.configured) {
          statusEl.textContent = "Configured (token " + result.data.token_hint + ", user id " + result.data.allowed_user_id + ")";
          statusEl.className = "ok";
        } else {
          statusEl.textContent = "Not configured";
          statusEl.className = "bad";
        }
      });
    }

    var telegramSaveBtn = byId("telegram-save-btn");
    if (telegramSaveBtn) {
      loadTelegramStatus();

      telegramSaveBtn.addEventListener("click", function () {
        var token = byId("telegram-token").value.trim();
        var userId = byId("telegram-user-id").value.trim();
        if (!token || !userId) return;
        telegramSaveBtn.disabled = true;
        telegramSaveBtn.textContent = "Saving...";
        apiCall("POST", "/admin/api/telegram/config", { token: token, allowed_user_id: userId })
          .then(function (result) {
            telegramSaveBtn.disabled = false;
            telegramSaveBtn.textContent = "Save";
            var statusEl = byId("telegram-status");
            if (result.status === 200) {
              byId("telegram-token").value = "";
              if (result.data && result.data.bot_warning) {
                statusEl.textContent = result.data.bot_warning;
                statusEl.className = "bad";
              } else {
                loadTelegramStatus();
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Save failed";
              statusEl.className = "bad";
            }
          })
          .catch(function (err) {
            telegramSaveBtn.disabled = false;
            telegramSaveBtn.textContent = "Save";
            var statusEl = byId("telegram-status");
            statusEl.textContent = "Request failed: " + err;
            statusEl.className = "bad";
          });
      });

      var telegramDeleteBtn = byId("telegram-delete-btn");
      telegramDeleteBtn.addEventListener("click", function () {
        apiCall("DELETE", "/admin/api/telegram/config").then(function () {
          loadTelegramStatus();
        });
      });
    }

    var addColumnRowBtn = byId("add-column-row-btn");
    if (addColumnRowBtn) {
      addColumnRowBtn.addEventListener("click", function () {
        byId("new-table-columns").appendChild(newColumnRow());
      });
    }

    var proposeSchemaBtn = byId("propose-schema-btn");
    if (proposeSchemaBtn) {
      wireBusyButton(proposeSchemaBtn, {
        statusEl: byId("custom-tables-status"),
        busyText: "Proposing...",
        beforeRequest: function () { return !!byId("schema-prompt").value.trim(); },
        requestFn: function () {
          return apiCall("POST", "/admin/api/custom-data/propose-schema", {
            provider: byId("schema-provider").value, prompt: byId("schema-prompt").value.trim(),
          });
        },
        onSuccess: function (result) {
          var proposal = result.data.proposal;
          byId("new-table-label").value = proposal.label;
          byId("new-table-columns").innerHTML = "";
          proposal.columns.forEach(function (col) {
            byId("new-table-columns").appendChild(newColumnRow(col.label, col.type));
          });
        },
        failureText: "Propose failed",
      });
    }

    var createTableBtn = byId("create-table-btn");
    if (createTableBtn) {
      wireBusyButton(createTableBtn, {
        statusEl: byId("custom-tables-status"),
        busyText: "Creating...",
        beforeRequest: function () {
          if (!byId("new-table-label").value.trim() || !collectNewTableColumns().length) {
            showError(byId("custom-tables-status"), "A table name and at least one column are required");
            return false;
          }
        },
        requestFn: function () {
          return apiCall("POST", "/admin/api/custom-data/tables", {
            label: byId("new-table-label").value.trim(), columns: collectNewTableColumns(),
          });
        },
        onSuccess: function () {
          byId("new-table-label").value = "";
          byId("new-table-columns").innerHTML = "";
          byId("schema-prompt").value = "";
          refreshCustomTables();
        },
        failureText: "Create failed",
      });
    }

    var dbBrowserSelect = byId("db-browser-table-select");
    if (dbBrowserSelect) {
      dbBrowserSelect.addEventListener("change", function () {
        loadDbBrowserTable(dbBrowserSelect.value);
      });
    }

    refreshSupplementalRecords();
    refreshOverrideRecords();
    refreshBotFacilities();
    refreshCustomTables();
    refreshDbBrowserTables();
  });
})();
"""


def render_setup_page(error=None):
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Set Up Admin Password</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card">
<h1>Set Up Admin Password</h1>
<p>This is a one-time step. This password protects the admin panel where AI provider API keys are stored.</p>
{error_html}
<form method="post" action="/admin/setup">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required minlength="8" autofocus>
  <label for="confirm">Confirm password</label>
  <input type="password" id="confirm" name="confirm" required minlength="8">
  <button type="submit" class="primary">Set Password</button>
</form>
</div>
</body>
</html>
"""


def render_login_page(error=None):
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Admin Login</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card">
<h1>Admin Login</h1>
{error_html}
<form method="post" action="/admin/login">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required autofocus>
  <button type="submit" class="primary">Log In</button>
</form>
</div>
</body>
</html>
"""


def _provider_option_html(status):
    provider = status["provider"]
    display_name = DISPLAY_NAMES.get(provider, provider)
    return f'<option value="{html.escape(provider)}">{html.escape(display_name)}</option>'


def _provider_row_html(status):
    provider = status["provider"]
    hint = status["hint"] or "not configured"
    display_name = DISPLAY_NAMES.get(provider, provider)
    return f"""<div class="provider-row" data-provider="{html.escape(provider)}">
  <span class="provider-name">{html.escape(display_name)}</span>
  <span class="provider-hint">{html.escape(hint)}</span>
  <span class="provider-status"></span>
  <div class="provider-actions">
    <input type="text" placeholder="Paste API key" autocomplete="off">
    <button type="button" class="primary save-btn">Save</button>
    <button type="button" class="secondary test-btn">Test</button>
    <button type="button" class="danger delete-btn">Delete</button>
  </div>
</div>"""


def render_admin_panel(statuses):
    rows = "\n".join(_provider_row_html(s) for s in statuses)
    provider_options = "\n".join(_provider_option_html(s) for s in statuses)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Provider Keys</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card panel-card">
<h1>AI Provider Keys</h1>
<p>Keys are stored in this machine's OS credential store, never in a file or sent to the browser after saving.</p>
{rows}
<div class="upload-section">
  <h2>Extract Document</h2>
  <p class="hint">Upload an Excel, PDF, Word, Text, CSV, or HTML file to preview its extracted text, or add it to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else). A facility readiness survey or equipment inventory will be recognized against the WHO SARA framework (e.g. "Thermometer: present", "Paracetamol: absent") and scored automatically in the report's Facility Readiness section.</p>
  <input type="file" id="extract-file-input" accept=".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv">
  <button type="button" class="primary" id="extract-btn">Extract</button>
  <p id="extract-status" class="error"></p>
  <textarea id="extract-result" readonly rows="12" placeholder="Extracted text will appear here"></textarea>
  <label for="supplemental-instruction">Instruction (optional)</label>
  <textarea id="supplemental-instruction" rows="2" placeholder="e.g. this equipment list is for Peshawar's DHQ Hospital"></textarea>
  <label for="supplemental-provider">AI provider</label>
  <select id="supplemental-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="add-to-report-btn">Add to Report</button>
  <p id="supplemental-status" class="error"></p>
  <div id="supplemental-result"></div>
</div>
<div class="upload-section">
  <h2>Supplemental Records</h2>
  <p class="hint">Every fact currently in the report from document upload or database ingestion. Delete a record to remove it and rebuild the report automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th><th>Added</th><th></th></tr>
      </thead>
      <tbody id="supplemental-records-tbody">
        <tr><td colspan="8" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="supplemental-records-status" class="error"></p>
</div>
<div class="upload-section">
  <h2>Update Pipeline Data</h2>
  <p class="hint">Upload a document (or a short instruction typed into a small text file) describing an update to a district's population or aggregate health-facility numbers - e.g. "Peshawar's population is now 5.1 million per the new provincial estimate." The AI proposes a validated change; implausible swings from the current value are rejected automatically. Applying a change recomputes the gap score, GIS layers, and report.</p>
  <input type="file" id="metric-file-input" accept=".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv">
  <label for="metric-instruction">Instruction (optional)</label>
  <textarea id="metric-instruction" rows="2" placeholder="e.g. Peshawar's population is now 5.1 million per the new provincial estimate"></textarea>
  <label for="metric-provider">AI provider</label>
  <select id="metric-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="apply-metric-update-btn">Apply Update</button>
  <p id="metric-status" class="error"></p>
  <div id="metric-result"></div>
</div>
<div class="upload-section">
  <h2>Pipeline Overrides</h2>
  <p class="hint">Every population/health-number override currently applied to the pipeline. Delete one to remove its effect and rerun the pipeline automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>District</th><th>File</th><th>Column</th><th>Value</th><th>Reason</th><th>Added</th><th></th></tr>
      </thead>
      <tbody id="override-records-tbody">
        <tr><td colspan="7" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="override-records-status" class="error"></p>
</div>
<div class="upload-section">
  <h2>Database Ingestion</h2>
  <p class="hint">Connect to a PostgreSQL database, browse its tables, and add a table's rows to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else) - same AI extraction as document upload, one connection at a time.</p>
  <label for="db-host">Host</label>
  <input type="text" id="db-host" placeholder="localhost">
  <label for="db-port">Port</label>
  <input type="text" id="db-port" placeholder="5432">
  <label for="db-database">Database</label>
  <input type="text" id="db-database" placeholder="kp_health">
  <label for="db-user">Username</label>
  <input type="text" id="db-user" placeholder="db username">
  <label for="db-password">Password</label>
  <input type="password" id="db-password" placeholder="db password">
  <label for="db-sslmode">SSL mode (optional)</label>
  <input type="text" id="db-sslmode" placeholder="prefer">
  <button type="button" class="primary" id="db-connect-btn">Save &amp; Test Connection</button>
  <p id="db-connection-status"></p>
  <label for="db-table-select">Table</label>
  <select id="db-table-select">
    <option value="">Select a table...</option>
  </select>
  <button type="button" class="secondary" id="db-preview-btn">Preview</button>
  <textarea id="db-preview-result" readonly rows="8" placeholder="Previewed rows will appear here"></textarea>
  <label for="db-instruction">Instruction (optional)</label>
  <textarea id="db-instruction" rows="2" placeholder="e.g. this table lists equipment per facility"></textarea>
  <label for="db-provider">AI provider</label>
  <select id="db-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="db-ingest-btn">Add to Report</button>
  <p id="db-ingest-status" class="error"></p>
  <div id="db-ingest-result"></div>
</div>
<div class="upload-section">
  <h2>Telegram Bot</h2>
  <p class="hint">Interact with this dashboard from Telegram - view the report/map, ask the AI, manage keys, and add facilities from the field. Create a bot via @BotFather to get a token, and find your numeric user id via @userinfobot.</p>
  <label for="telegram-token">Bot token</label>
  <input type="password" id="telegram-token" placeholder="Paste bot token" autocomplete="off">
  <label for="telegram-user-id">Your Telegram user id</label>
  <input type="text" id="telegram-user-id" placeholder="e.g. 123456789">
  <button type="button" class="primary" id="telegram-save-btn">Save</button>
  <button type="button" class="danger" id="telegram-delete-btn">Delete</button>
  <p id="telegram-status"></p>
</div>
<div class="upload-section">
  <h2>Bot-Added Facilities</h2>
  <p class="hint">Every facility added via the Telegram bot's /addpoint command. Delete one to remove it and rebuild the map/report automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>Name</th><th>District</th><th>Category</th><th>Lat</th><th>Lon</th><th>Added</th><th>Added By</th><th></th></tr>
      </thead>
      <tbody id="bot-facilities-tbody">
        <tr><td colspan="8" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="bot-facilities-status" class="error"></p>
</div>
<div class="upload-section">
  <h2>Custom Data Tables</h2>
  <p class="hint">Create your own tables for data that doesn't fit anywhere else (equipment tracking, staff records, anything). Populate them the same way as document upload elsewhere on this page - the AI decides how to title, summarize, and place each table's section in the report.</p>
  <h3>Create a new table</h3>
  <label for="new-table-label">Table name</label>
  <input type="text" id="new-table-label" placeholder="e.g. Cold Chain Equipment">
  <div id="new-table-columns"></div>
  <button type="button" class="secondary" id="add-column-row-btn">+ Add Column</button>
  <label for="schema-prompt">Or describe it and let AI propose the columns (optional)</label>
  <textarea id="schema-prompt" rows="2" placeholder="e.g. track cold-chain equipment status per facility"></textarea>
  <label for="schema-provider">AI provider</label>
  <select id="schema-provider">
{provider_options}
  </select>
  <button type="button" class="secondary" id="propose-schema-btn">Propose Schema</button>
  <button type="button" class="primary" id="create-table-btn">Create Table</button>
  <p id="custom-tables-status" class="error"></p>
  <div id="custom-tables-container"></div>
</div>
<div class="upload-section">
  <h2>Database Browser</h2>
  <p class="hint">View and edit every table in the bundled database directly - including internal tables like custom_tables/custom_table_columns that track Custom Data Tables' own structure. Editing those directly can desync the registry from the real database and break that feature; edit them only if you know what you're doing.</p>
  <label for="db-browser-table-select">Table</label>
  <select id="db-browser-table-select">
    <option value="">Select a table...</option>
  </select>
  <div id="db-browser-content"></div>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
