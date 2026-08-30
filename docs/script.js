(function () {
  "use strict";

  // ---------------------------------------------------------------
  // Parsing / session-splitting / CSV logic — unchanged pure functions
  // (verified byte-identical to builder/*.py's output).
  // ---------------------------------------------------------------
  var MONTHS = {jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};
  var SKIP_LINE_RE = /^\d+\s+guides?$/i;
  var TIME_RE = /(\d{1,2})\s+(\w{3})\s+'(\d{2})\s+@\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(am|pm)/i;

  function groupIntoBlocks(lines) {
    var blocks = [], current = [], blankRun = 0;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line === "") { blankRun++; continue; }
      if (blankRun >= 2 && current.length) { blocks.push(current); current = []; }
      blankRun = 0;
      current.push(line);
    }
    if (current.length) blocks.push(current);
    return blocks;
  }

  function parseTimestamp(match) {
    var month = MONTHS[match[2].toLowerCase()];
    if (month === undefined) return null;
    var hour = parseInt(match[4], 10) % 12;
    if (match[7].toLowerCase() === "pm") hour += 12;
    var year = 2000 + parseInt(match[3], 10);
    var d = new Date(year, month, parseInt(match[1], 10), hour, parseInt(match[5], 10), match[6] ? parseInt(match[6], 10) : 0);
    return isNaN(d.getTime()) ? null : d.getTime();
  }

  function parseExport(text) {
    var lines = text.split(/\r?\n/).map(function (l) { return l.trim(); });
    var blocks = groupIntoBlocks(lines);
    var achievements = [];
    var totalCount = 0;

    blocks.forEach(function (block, bi) {
      var orderIndex = bi + 1;
      var idx = 0;
      while (idx < block.length && SKIP_LINE_RE.test(block[idx])) idx++;
      if (idx >= block.length) return;

      var achName = block[idx];
      idx++;
      totalCount++;

      var unlockTime = null;
      for (var i = idx; i < block.length; i++) {
        var m = block[i].match(TIME_RE);
        if (m) { unlockTime = parseTimestamp(m); break; }
      }

      if (unlockTime !== null) {
        achievements.push({ach_name: achName, ach_id: orderIndex, unlock_time: unlockTime});
      }
    });

    // ach_id is the achievement's position as pasted, which must match the
    // game's default/schema order (same order ASF's alist/aset use) for the
    // generated config to target the right achievements. If the pasted
    // timestamps already come out non-decreasing, that's a strong sign the
    // page was sorted by unlock date instead, which would silently scramble
    // ach_id even though the delay/session math below stays correct either
    // way (it re-sorts by real timestamp regardless of paste order).
    var sortedByDate = achievements.length > 1 && achievements.every(function (a, i) {
      return i === 0 || a.unlock_time >= achievements[i - 1].unlock_time;
    });

    achievements.sort(function (a, b) { return a.unlock_time - b.unlock_time; });
    return {list: achievements, sortedByDate: sortedByDate, totalCount: totalCount};
  }

  function addDelays(list) {
    var prev = null;
    return list.map(function (a) {
      var delay = prev === null ? 0 : Math.round((a.unlock_time - prev) / 1000);
      prev = a.unlock_time;
      return {ach_name: a.ach_name, ach_id: a.ach_id, unlock_time: a.unlock_time, delay: delay};
    });
  }

  function splitSessions(items, gapLimit, cumulativeLimit) {
    var sessions = [], current = [], initialDelays = [], durations = [], cumulative = 0;

    items.forEach(function (ach) {
      var delay = ach.delay;

      if (delay > gapLimit && current.length) {
        sessions.push(current); durations.push(cumulative); current = []; cumulative = 0;
      } else if (cumulative + delay > cumulativeLimit && current.length) {
        sessions.push(current); durations.push(cumulative); current = []; cumulative = 0;
      }

      if (current.length === 0) {
        initialDelays.push(ach.delay);
        ach = {ach_name: ach.ach_name, ach_id: ach.ach_id, unlock_time: ach.unlock_time, delay: 0};
      }

      current.push(ach);
      cumulative += ach.delay;
    });

    if (current.length) { sessions.push(current); durations.push(cumulative); }
    var gaps = sessions.length > 1 ? initialDelays.slice(1) : [];
    return {sessions: sessions, gaps: gaps, durations: durations};
  }

  function buildConfig(appid, sessions, gaps) {
    var achievements = [];
    sessions.forEach(function (session, si) {
      session.forEach(function (ach, ai) {
        var isFirst = ai === 0;
        var delay, newSession;
        if (si === 0 && isFirst) { delay = ach.delay || 0; newSession = false; }
        else if (isFirst) { delay = gaps[si - 1]; newSession = true; }
        else { delay = ach.delay || 0; newSession = false; }
        achievements.push({id: ach.ach_id, delay: delay, new_session: newSession});
      });
    });
    return {appid: appid, achievements: achievements};
  }

  function roughDuration(seconds) {
    seconds = Math.round(seconds);
    if (seconds >= 86400) return ">1 day";
    if (seconds >= 3600) return "~" + Math.ceil(seconds / 3600) + "h";
    if (seconds >= 60) return "~" + Math.ceil(seconds / 60) + "m";
    return seconds + "s";
  }

  // Matches builder/save.py's rough_duration() exactly, for CSV parity.
  function roughDurationPy(seconds) {
    seconds = Math.trunc(seconds);
    if (seconds >= 86400) return ">1 day";
    if (seconds >= 3600) return "~" + String(Math.ceil(seconds / 3600)).padStart(2, "0") + " hour";
    if (seconds >= 60) return "~" + String(Math.ceil(seconds / 60)).padStart(2, "0") + " min";
    return "=" + String(seconds).padStart(2, "0") + " sec";
  }

  function formatUnlockTime(ms) {
    var d = new Date(ms);
    function pad(n) { return String(n).padStart(2, "0"); }
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function csvCell(v) {
    var s = v === null || v === undefined ? "" : String(v);
    if (/[",\r\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function toCsv(rows) {
    return rows.map(function (r) { return r.map(csvCell).join(","); }).join("\r\n") + "\r\n";
  }

  function buildMergedRows(withDelays) {
    var rows = [["ach_name", "ach_id", "unlock_time", "delay (s)"]];
    withDelays.forEach(function (a) {
      rows.push([a.ach_name, a.ach_id, formatUnlockTime(a.unlock_time), a.delay]);
    });
    return rows;
  }

  function buildSessionsRows(sessions, gaps) {
    var rows = [["session_index", "ach_name", "ach_id", "unlock_time", "delay (h m s)"]];
    sessions.forEach(function (session, i) {
      session.forEach(function (a) {
        rows.push([i + 1, a.ach_name, a.ach_id, formatUnlockTime(a.unlock_time), roughDurationPy(a.delay)]);
      });
      if (i < sessions.length - 1) rows.push(["", "", "", "", roughDurationPy(gaps[i])]);
    });
    return rows;
  }

  function buildSummaryRows(durations, gaps) {
    var rows = [["session_index", "session_duration", "gap_from_previous"]];
    durations.forEach(function (d, i) {
      rows.push([i + 1, roughDurationPy(d), i !== 0 ? roughDurationPy(gaps[i - 1]) : ""]);
    });
    return rows;
  }

  // Renders the same row data as an HTML table for on-screen preview. The
  // CSV file itself (built via toCsv() from these same rows) is untouched.
  function rowsToTable(rows) {
    var head = rows[0];
    var body = rows.slice(1);
    var html = '<div class="table-wrap"><table class="csv-table"><thead><tr>';
    head.forEach(function (h) { html += "<th>" + escapeHtml(h) + "</th>"; });
    html += "</tr></thead><tbody>";
    body.forEach(function (r) {
      var isGapRow = r.slice(0, -1).every(function (c) { return c === ""; }) && r[r.length - 1] !== "";
      if (isGapRow) {
        html += '<tr class="gap-row"><td colspan="' + head.length + '">— ' + escapeHtml(r[r.length - 1]) + " gap until next session —</td></tr>";
      } else {
        html += "<tr>" + r.map(function (c) { return "<td>" + escapeHtml(c) + "</td>"; }).join("") + "</tr>";
      }
    });
    html += "</tbody></table></div>";
    return html;
  }

  function highlightJson(json) {
    var escaped = json.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    escaped = escaped.replace(/"(\\.|[^"\\])*"(\s*:)?/g, function (match) {
      var cls = /:$/.test(match) ? "tok-key" : "tok-str";
      return '<span class="' + cls + '">' + match + "</span>";
    });
    escaped = escaped.replace(/: (-?\d+(\.\d+)?)/g, ': <span class="tok-num">$1</span>');
    escaped = escaped.replace(/: (true|false)/g, ': <span class="tok-bool">$1</span>');
    return escaped;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ---------------------------------------------------------------
  // UI state machine
  // ---------------------------------------------------------------
  var els = {
    exportText: document.getElementById("export-text"),
    appid: document.getElementById("appid"),
    gameName: document.getElementById("game-name"),
    gapLimit: document.getElementById("gap-limit"),
    cumLimit: document.getElementById("cum-limit"),
    minGap: document.getElementById("min-gap"),
    resetBtn: document.getElementById("reset-btn"),
    downloadBtn: document.getElementById("download-btn"),
    folderBtn: document.getElementById("folder-btn"),
    folderStatus: document.getElementById("folder-status"),
    boxTitle: document.getElementById("box-title"),
    statsStrip: document.getElementById("stats-strip"),
    toast: document.getElementById("toast"),
    jsonSlot: document.getElementById("json-slot"),
    csvMergedSlot: document.getElementById("csv-merged-slot"),
    csvSessionsSlot: document.getElementById("csv-sessions-slot"),
    csvSummarySlot: document.getElementById("csv-summary-slot")
  };

  var NAV_TITLES = {
    input: "Achievements",
    settings: "Settings",
    json: "Schedule (config.json)",
    "csv-merged": "Full Timeline (merged.csv)",
    "csv-sessions": "Sessions (sessions.csv)",
    "csv-summary": "Session Summary (summary_session.csv)"
  };

  var navButtons = {
    input: document.getElementById("nav-input"),
    settings: document.getElementById("nav-settings"),
    json: document.getElementById("nav-json"),
    "csv-merged": document.getElementById("nav-csv-merged"),
    "csv-sessions": document.getElementById("nav-csv-sessions"),
    "csv-summary": document.getElementById("nav-csv-summary")
  };

  var currentView = "input";
  var latest = null; // { errors, warnings, config, jsonText, csv:{merged,sessions,summary}, filenames, stats }
  var dirHandle = null;

  function showToast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () { els.toast.classList.remove("show"); }, 2200);
  }

  function setView(view) {
    currentView = view;
    Object.keys(navButtons).forEach(function (k) { navButtons[k].classList.toggle("active", k === view); });
    document.querySelectorAll(".view-pane").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-view") === view);
    });
    els.boxTitle.textContent = NAV_TITLES[view];
    render();
  }

  Object.keys(navButtons).forEach(function (key) {
    navButtons[key].addEventListener("click", function () { setView(key); });
  });

  function noticeHtml(kind, text) {
    return '<div class="notice ' + kind + '">' + escapeHtml(text) + "</div>";
  }

  function emptyHtml(glyph, text) {
    return '<div class="empty-hint"><div class="glyph">' + glyph + "</div><p>" + escapeHtml(text) + "</p></div>";
  }

  function saveDraft() {
    try {
      localStorage.setItem("unlock-scheduler-draft", JSON.stringify({
        exportText: els.exportText.value,
        appid: els.appid.value,
        gameName: els.gameName.value,
        gapLimit: els.gapLimit.value,
        cumLimit: els.cumLimit.value,
        minGap: els.minGap.value
      }));
    } catch (e) {}
  }

  function loadDraft() {
    try {
      var raw = localStorage.getItem("unlock-scheduler-draft");
      if (!raw) return;
      var d = JSON.parse(raw);
      if (d.exportText) els.exportText.value = d.exportText;
      if (d.appid) els.appid.value = d.appid;
      if (d.gameName) els.gameName.value = d.gameName;
      if (d.gapLimit) els.gapLimit.value = d.gapLimit;
      if (d.cumLimit) els.cumLimit.value = d.cumLimit;
      if (d.minGap) els.minGap.value = d.minGap;
    } catch (e) {}
  }

  // ---- core recompute (pure, runs on every input change) ----
  function recompute() {
    var appidVal = parseInt(els.appid.value, 10);
    var gameName = els.gameName.value.trim().toLowerCase();
    var gapLimitSec = (parseFloat(els.gapLimit.value) || 0) * 3600;
    var cumLimitSec = (parseFloat(els.cumLimit.value) || 0) * 3600;
    var minGapSec = (parseFloat(els.minGap.value) || 0) * 3600;

    var errors = [];
    if (!appidVal || appidVal <= 0) errors.push("Enter a valid numeric App ID in Settings.");

    var parsed = parseExport(els.exportText.value || "");
    if (parsed.list.length === 0) errors.push("No unlocked achievements with a valid timestamp were found in the pasted text.");

    if (errors.length) { latest = {errors: errors}; return; }

    var withDelays = addDelays(parsed.list);

    var timeMap = {};
    withDelays.forEach(function (a, i) {
      if (i === 0) return;
      (timeMap[a.unlock_time] = timeMap[a.unlock_time] || []).push(a.ach_name);
    });
    var simultaneous = Object.keys(timeMap).filter(function (t) { return timeMap[t].length > 1; });

    var split = splitSessions(withDelays, gapLimitSec, cumLimitSec);
    var config = buildConfig(appidVal, split.sessions, split.gaps);

    var warnings = [];
    if (parsed.sortedByDate) warnings.push("This paste looks sorted by unlock date, not the game's default order. Achievement numbering needs default order.");
    if (simultaneous.length) warnings.push(simultaneous.length + " timestamp(s) have multiple achievements unlocking together.");
    split.gaps.forEach(function (g, i) {
      if (g <= minGapSec) warnings.push("Session " + (i + 2) + " starts only " + roughDuration(g) + " after the previous one (below your min gap).");
    });
    var zeroSessions = split.durations.filter(function (d) { return d <= 1; }).length;
    if (zeroSessions) warnings.push(zeroSessions + " session(s) have essentially zero duration (a single achievement).");

    var suffix = gameName ? "_" + gameName : "";

    var mergedRows = buildMergedRows(withDelays);
    var sessionsRows = buildSessionsRows(split.sessions, split.gaps);
    var summaryRows = buildSummaryRows(split.durations, split.gaps);

    latest = {
      errors: [],
      warnings: warnings,
      config: config,
      jsonText: JSON.stringify(config, null, 2),
      csv: {
        merged: toCsv(mergedRows),
        sessions: toCsv(sessionsRows),
        summary: toCsv(summaryRows)
      },
      rows: {
        merged: mergedRows,
        sessions: sessionsRows,
        summary: summaryRows
      },
      filenames: {
        json: "config" + suffix + ".json",
        merged: "merged" + suffix + ".csv",
        sessions: "sessions" + suffix + ".csv",
        summary: "summary_session" + suffix + ".csv"
      },
      stats: {
        achievements: config.achievements.length,
        totalAchievements: parsed.totalCount,
        sessions: split.sessions.length,
        sessionLen: split.durations.length ? roughDuration(Math.min.apply(null, split.durations)) + " – " + roughDuration(Math.max.apply(null, split.durations)) : "—",
        gapRange: split.gaps.length ? roughDuration(Math.min.apply(null, split.gaps)) + " – " + roughDuration(Math.max.apply(null, split.gaps)) : "—"
      }
    };
  }

  function fileKeyForView(view) {
    if (view === "json") return "json";
    if (view === "csv-merged") return "merged";
    if (view === "csv-sessions") return "sessions";
    if (view === "csv-summary") return "summary";
    return null;
  }

  function render() {
    // nav "has content" dots
    var ok = latest && !latest.errors.length;
    ["json", "csv-merged", "csv-sessions", "csv-summary"].forEach(function (k) {
      navButtons[k].classList.toggle("has-content", !!ok);
    });

    // stats strip (shown once we have valid data, regardless of view)
    if (ok) {
      els.statsStrip.style.display = "flex";
      els.statsStrip.innerHTML =
        "<span><b>" + latest.stats.achievements + " / " + latest.stats.totalAchievements + "</b> achievements</span>" +
        "<span><b>" + latest.stats.sessions + "</b> sessions</span>" +
        "<span>len <b>" + latest.stats.sessionLen + "</b></span>" +
        "<span>gap <b>" + latest.stats.gapRange + "</b></span>";
    } else {
      els.statsStrip.style.display = "none";
    }

    // Download always saves everything together, so it only depends on
    // whether we have valid data at all — not on which tab is open.
    els.downloadBtn.disabled = !ok;

    var fileKey = fileKeyForView(currentView);
    if (!fileKey) return;

    var slot = fileKey === "json" ? els.jsonSlot :
      fileKey === "merged" ? els.csvMergedSlot :
      fileKey === "sessions" ? els.csvSessionsSlot : els.csvSummarySlot;

    if (!latest || latest.errors.length) {
      var msgs = latest ? latest.errors : ["Paste an export first."];
      slot.innerHTML = msgs.map(function (m) { return noticeHtml("danger", m); }).join("") +
        emptyHtml("{ }", "Fix the issue above to generate this file.");
      return;
    }

    var warningsHtml = latest.warnings.map(function (w) { return noticeHtml("warn", w); }).join("");

    if (fileKey === "json") {
      slot.innerHTML = warningsHtml + '<pre class="file-preview">' + highlightJson(latest.jsonText) + "</pre>";
    } else {
      slot.innerHTML = warningsHtml + rowsToTable(latest.rows[fileKey]);
    }
  }

  var debounceTimer = null;
  function scheduleRecompute() {
    saveDraft();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () { recompute(); render(); }, 300);
  }

  [els.exportText, els.appid, els.gameName, els.gapLimit, els.cumLimit, els.minGap].forEach(function (el) {
    el.addEventListener("input", scheduleRecompute);
  });

  els.resetBtn.addEventListener("click", function () {
    els.exportText.value = "";
    els.appid.value = "";
    els.gameName.value = "";
    els.gapLimit.value = "2";
    els.cumLimit.value = "5";
    els.minGap.value = "1";
    try { localStorage.removeItem("unlock-scheduler-draft"); } catch (e) {}
    recompute();
    render();
    showToast("Fields reset");
  });

  // ---- collapsible side panels ----
  function wireCollapse(buttonId, panelId, storageKey) {
    var btn = document.getElementById(buttonId);
    var panel = document.getElementById(panelId);
    try {
      if (localStorage.getItem(storageKey) === "1") panel.classList.add("collapsed");
    } catch (e) {}
    btn.addEventListener("click", function () {
      panel.classList.toggle("collapsed");
      try { localStorage.setItem(storageKey, panel.classList.contains("collapsed") ? "1" : "0"); } catch (e) {}
    });
  }
  wireCollapse("collapse-left", "nav-panel-left", "unlock-scheduler-left-collapsed");
  wireCollapse("collapse-right", "nav-panel-right", "unlock-scheduler-right-collapsed");

  // ---------------------------------------------------------------
  // Saving: File System Access API folder (silent, Chromium-only,
  // won't work in a sandboxed iframe) > claude.ai downloads capability
  // > plain Blob download, in that order.
  // ---------------------------------------------------------------
  function useClaudeDownloads() {
    if (typeof claude === "undefined" || !claude.use) return Promise.resolve(null);
    return claude.use("downloads");
  }

  function blobDownload(filename, mime, content) {
    try {
      var blob = new Blob([content], {type: mime});
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      return true;
    } catch (e) {
      return false;
    }
  }

  // Walks/creates each folder in dirParts under dirHandle (never clearing
  // or recreating a folder that already exists — getDirectoryHandle with
  // create:true just opens it if present), then writes filename into it.
  function saveIntoFolder(dirParts, filename, content) {
    var p = Promise.resolve(dirHandle);
    dirParts.forEach(function (part) {
      p = p.then(function (dir) { return dir.getDirectoryHandle(part, {create: true}); });
    });
    return p
      .then(function (dir) { return dir.getFileHandle(filename, {create: true}); })
      .then(function (handle) { return handle.createWritable(); })
      .then(function (writable) { return writable.write(content).then(function () { return writable.close(); }); });
  }

  function saveOneFallback(downloads, filename, mime, content) {
    if (downloads) {
      return downloads.save({filename: filename, data: content}).catch(function (err) {
        if (err && err.code === "declined") throw err;
        blobDownload(filename, mime, content);
      });
    }
    blobDownload(filename, mime, content);
    return Promise.resolve();
  }

  // Saves config.json + all three CSVs together in one action:
  //   <folder>/jsons/config[_<name>].json
  //   <folder>/csvs/<name>/{merged,sessions,summary_session}.csv
  // matching generate_json.py's own layout. Falls back to one browser/
  // claude.ai save prompt per file (with a small stagger) when no folder
  // has been chosen.
  els.downloadBtn.addEventListener("click", function () {
    if (!latest || latest.errors.length) return;

    var folderName = (els.gameName.value.trim().toLowerCase()) || "default";
    var files = [
      {dirParts: ["jsons"], filename: latest.filenames.json, mime: "application/json", content: latest.jsonText},
      {dirParts: ["csvs", folderName], filename: "merged.csv", mime: "text/csv", content: latest.csv.merged},
      {dirParts: ["csvs", folderName], filename: "sessions.csv", mime: "text/csv", content: latest.csv.sessions},
      {dirParts: ["csvs", folderName], filename: "summary_session.csv", mime: "text/csv", content: latest.csv.summary}
    ];

    if (dirHandle) {
      Promise.all(files.map(function (f) { return saveIntoFolder(f.dirParts, f.filename, f.content); }))
        .then(function () { showToast("Saved config.json + 3 CSVs to " + dirHandle.name + "/"); })
        .catch(function (err) { showToast("Couldn't save: " + (err && err.message ? err.message : "unknown error")); });
      return;
    }

    useClaudeDownloads().then(function (downloads) {
      var chain = Promise.resolve();
      files.forEach(function (f, i) {
        chain = chain.then(function () {
          return saveOneFallback(downloads, f.filename, f.mime, f.content)
            .then(function () { return new Promise(function (r) { setTimeout(r, i < files.length - 1 ? 300 : 0); }); });
        });
      });
      chain.then(function () { showToast("Saved config.json + 3 CSVs"); }).catch(function () {});
    }).catch(function () {
      files.forEach(function (f) { blobDownload(f.filename, f.mime, f.content); });
      showToast("Saved config.json + 3 CSVs");
    });
  });

  if (typeof window.showDirectoryPicker === "function") {
    els.folderBtn.style.display = "inline-block";
    els.folderBtn.addEventListener("click", function () {
      window.showDirectoryPicker({mode: "readwrite"}).then(function (handle) {
        dirHandle = handle;
        els.folderStatus.innerHTML = 'Saving to <b>' + escapeHtml(handle.name) + "/</b>";
        showToast("Folder selected — downloads will save there silently");
      }).catch(function () { /* user cancelled, or blocked (e.g. inside a sandboxed iframe) */ });
    });
  }

  // ---------------------------------------------------------------
  loadDraft();
  recompute();
  render();
})();
