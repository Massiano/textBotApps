/* CineTot Studio front end. No framework. */

// The studio runs standalone at / and mounted at /studio/. Resolving API
// calls against the current directory rather than the domain root makes both
// work without a build step or a hardcoded prefix.
const ROOT = location.pathname.replace(/\/[^/]*$/, "/");

const el = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  lang: "en", domain: "movies", view: "coverage",
  levels: [], picked: new Set(), band: null,
  queue: [], qi: 0, reviewed: 0,
  poll: null,
};

async function api(path, opts) {
  const r = await fetch(ROOT + path.replace(/^\//, ""), opts);
  const d = await r.json().catch(() => ({ error: "bad response" }));
  if (!r.ok) throw new Error(d.error || `failed (${r.status})`);
  return d;
}
const post = (p, b) => api(p, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(b || {}),
});

/* --------------------------------------------------------------- chrome */

function showView(v) {
  S.view = v;
  document.querySelectorAll(".view").forEach((x) => x.classList.remove("on"));
  el("view-" + v).classList.add("on");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.view === v));
  ({ coverage: loadCoverage, review: loadQueue, telemetry: loadTelemetry,
     subjects: loadSubjects, ladder: loadLadder, compose: loadCompose }[v] || (() => {}))();
}

function renderStats(counts) {
  const order = ["draft", "candidate", "probing", "accepted", "rejected"];
  el("stats").innerHTML = order
    .filter((k) => counts[k])
    .map((k) => `<span class="stat ${k}">${k} <b>${counts[k]}</b></span>`).join("")
    || `<span class="stat">no riddles yet</span>`;
  el("badge-review").textContent = counts.candidate || "";
}

async function loadOverview() {
  const d = await api("/api/overview");
  if (!el("lang").options.length) {
    el("lang").innerHTML = d.lang_options
      .map((l) => `<option value="${l.code}">${esc(l.label)}</option>`).join("");
    el("domain").innerHTML = d.domains
      .map((x) => `<option value="${x}">${esc(x)}</option>`).join("");
    el("lang").value = S.lang; el("domain").value = S.domain;
    S.levels = d.levels;
    el("gen-levels").innerHTML = d.levels
      .map((lv) => `<span class="lvl" data-lv="${lv}">${lv}</span>`).join("");
    el("gen-levels").querySelectorAll(".lvl").forEach((n) => {
      n.onclick = () => {
        const lv = Number(n.dataset.lv);
        S.picked.has(lv) ? S.picked.delete(lv) : S.picked.add(lv);
        n.classList.toggle("on");
      };
    });
    el("gen-badge").textContent = d.live_key
      ? `${d.generator} · live · ${(d.solvers || []).length} solvers`
      : `${d.generator} · offline, no API key`;
    el("gen-badge").title = (d.solvers || []).join("\n") || "no solver models resolved";
  }
  renderStats(d.counts);
  renderWorker(d.worker);
  renderJobs(d.jobs);
  renderLog(d.worker.log || []);
}

function renderLog(log) {
  const box = el("worker-log-rows");
  if (!box) return;
  box.innerHTML = log.length ? log.map((e) => {
    const m = e.msg || "";
    const cls = /rejected|failed|error/i.test(m) ? "rej"
              : /probe ok|drafts/i.test(m) ? "acc" : "";
    return `<div class="wl ${cls}"><span class="wt">${esc((e.at || "").slice(11, 19))}</span>`
      + `${esc(m)}</div>`;
  }).join("")
    : `<div class="wl">Nothing yet. Queue work, or try one to see the pipeline run.</div>`;
}

function renderWorker(w) {
  el("worker-dot").classList.toggle("on", w.running);
  const cur = w.current;
  const cooling = Object.keys(w.cooldowns || {}).length;
  let text;
  if (w.running && cur) {
    text = `${cur.kind} ${cur.lang}/${cur.domain}@${cur.level} — ${cur.done}/${cur.want}`;
  } else if (w.running) {
    text = "running · waiting for work";
  } else {
    text = w.log && w.log[0] ? w.log[0].msg : "idle";
  }
  if (cooling) text += ` · ${cooling} model(s) rate-limited`;
  if (w.calls) text += ` · ${w.calls} api calls`;
  el("worker-text").textContent = text;
  el("worker-toggle").textContent = w.running ? "stop" : "start";
}

function renderJobs(jobs) {
  el("joblist").innerHTML = (jobs || []).map((j) => {
    const pct = j.want ? Math.round(100 * j.done / j.want) : 0;
    return `<div class="job">
      <span class="jt">${esc(j.kind)} · ${esc(j.lang)}/${esc(j.domain || "-")} @ ${j.level || "-"}</span>
      <span class="jp">${j.done}/${j.want} done${j.failed ? ` · ${j.failed} rejected` : ""}${j.note ? ` · ${esc(String(j.note).slice(0, 40))}` : ""}</span>
      <span class="jbar"><span class="jbf" style="width:${pct}%"></span></span>
      <span class="js ${j.status}">${esc(j.status)}</span>
    </div>`;
  }).join("");
}

/* ------------------------------------------------------------- coverage */

async function loadCoverage() {
  const d = await api(`/api/coverage?lang=${S.lang}&domain=${S.domain}&hi=8000`);
  el("cov-rows").innerHTML = d.bands.map((b, i) => `
    <div class="covrow" data-i="${i}">
      <span class="cb">${b.band}</span>
      <span class="ctrack">
        <span class="cfill" style="width:${Math.round(b.pct * 4.8)}px"></span>
        <span class="cwell" style="width:${Math.round(b.pct_well * 4.8)}px"></span>
      </span>
      <span class="cn"><b>${b.well_covered}</b> of ${b.teachable} teachable at 3+
        · ${b.covered} at 1+</span>
    </div>`).join("");

  el("cov-rows").querySelectorAll(".covrow").forEach((n) => {
    n.onclick = () => {
      el("cov-rows").querySelectorAll(".covrow").forEach((x) => x.classList.remove("sel"));
      n.classList.add("sel");
      const b = d.bands[Number(n.dataset.i)];
      S.band = b;
      el("cov-thin-words").innerHTML = b.thin_sample.length
        ? b.thin_sample.map((w) => `<span class="tw">${esc(w)}</span>`).join("")
        : `<span class="tw">nothing thin in this band</span>`;
      S.picked = new Set([b.hi]);
      el("gen-levels").querySelectorAll(".lvl").forEach((x) =>
        x.classList.toggle("on", Number(x.dataset.lv) === b.hi));
    };
  });
  if (d.bands.length && !S.band) el("cov-rows").querySelector(".covrow").click();
}

async function queueWork() {
  const levels = [...S.picked];
  if (!levels.length) { el("gen-note").textContent = "Pick at least one level."; return; }
  const want = Number(el("gen-want").value) || 5;
  const d = await post("/api/generate", { lang: S.lang, domain: S.domain, levels, want });
  el("gen-note").textContent =
    `Queued ${d.queued.length} job × ${want}. With a real model each riddle takes `
    + `roughly 20-60 seconds including retries and probes, so the first result is `
    + `about a minute away. Watch the activity log below.`;
  loadOverview();
}

async function tryOne() {
  const level = [...S.picked][0] || 2000;
  const btn = el("gen-one");
  btn.disabled = true; btn.textContent = "working…";
  el("gen-result").innerHTML = `<span style="color:#6b7280">Generating at level ${level}. `
    + `A real model takes 20-60 seconds.</span>`;
  try {
    const d = await post("/api/generate/one", { lang: S.lang, domain: S.domain, level });
    if (d.error) {
      el("gen-result").innerHTML = `<span class="bad2">Failed in ${esc(d.stage)}: `
        + `${esc(d.error)}</span>`;
    } else {
      const p = d.probe;
      el("gen-result").innerHTML =
        `<span class="${d.ok ? "ok2" : "bad2"}">${d.ok ? "Accepted" : "Rejected"}</span>`
        + ` · ${esc(d.answer)} · ${d.drafts} draft(s) · ${d.seconds}s · ${esc(d.model || "?")}`
        + `<span class="rt">${esc(d.text)}</span>`
        + `teaches <b>${d.new.map(esc).join(", ") || "nothing"}</b> · ceiling ${d.ceiling_rank}`
        + (p ? ` · open recall ${p.solved_open}/${p.solved_open_of}`
             + ` · options-only ${p.blind_correct}/${p.blind_of}`
             + ` · cloze ${p.cloze_correct}/${p.cloze_of}` : "")
        + (d.reason ? `<br><span class="bad2">${esc(d.reason)}</span>` : "");
    }
  } catch (e) {
    el("gen-result").innerHTML = `<span class="bad2">${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false; btn.textContent = "Try one now";
    loadOverview();
  }
}

/* --------------------------------------------------------------- review */

async function loadQueue() {
  const d = await api(`/api/queue?status=candidate&lang=${S.lang}`);
  S.queue = d.items; S.qi = 0;
  renderStats(d.counts);
  showCard();
}

function showCard() {
  const r = S.queue[S.qi];
  el("rev-card").style.display = r ? "block" : "none";
  el("rev-empty").style.display = r ? "none" : "block";
  el("rev-progress").innerHTML = S.queue.length
    ? `${S.qi + 1} of ${S.queue.length} in queue · ${S.reviewed} decided this session`
    : "";
  if (!r) return;

  el("rev-meta").innerHTML =
    `<b>${esc(r.answer)}</b> · ${esc(r.lang)}/${esc(r.domain)} · level ${r.level}`
    + ` · ceiling <b>${r.ceiling_rank}</b> · ${r.drafts} draft${r.drafts > 1 ? "s" : ""}`
    + ` · ${esc(r.model || "?")}` + (r.origin === "live" ? " · from live play" : "");

  // Mark the new words in the text so a reviewer sees what it teaches.
  const newSet = new Set(r.new.map((w) => w.toLowerCase()));
  const nameSet = new Set((r.names || []).map((w) => w.toLowerCase()));
  el("rev-text").innerHTML = r.text.split(/(\s+)/).map((seg) => {
    const bare = seg.replace(/[^\p{L}'-]/gu, "").toLowerCase();
    if (!bare) return esc(seg);
    if (newSet.has(bare)) return `<span class="nw">${esc(seg)}</span>`;
    if (nameSet.has(bare)) return `<span class="nm">${esc(seg)}</span>`;
    return esc(seg);
  }).join("");

  el("rev-options").innerHTML = r.options
    .map((o) => `<span class="ro ${o === r.answer ? "ans" : ""}">${esc(o)}</span>`).join("");

  const p = r.probe || {};
  const bits = [`teaches <b>${r.new.map(esc).join(", ") || "nothing"}</b>`];
  if (p.solved_open_of) {
    bits.push(`open recall <span class="${p.solved_open ? "good" : "bad"}">`
      + `${p.solved_open}/${p.solved_open_of}</span>`);
    bits.push(`forced choice ${p.solved_forced}/${p.solved_forced_of}`);
    bits.push(`options-only <span class="${p.blind_correct > p.blind_of / 2 ? "bad" : "good"}">`
      + `${p.blind_correct}/${p.blind_of}</span>`);
    if (p.cloze_of) bits.push(`cloze <span class="${p.cloze_correct ? "good" : "bad"}">`
      + `${p.cloze_correct}/${p.cloze_of}</span>`);
    if (p.weakest_solver) bits.push(`weakest solver <b>${esc(p.weakest_solver)}</b>`);
    if (p.cues && p.cues.length) bits.push(`cues used: ${esc(p.cues[0].cues || "")}`);
  } else {
    bits.push(`<span class="bad">not probed yet</span>`);
  }
  el("rev-machine").innerHTML = bits.join(" &nbsp;·&nbsp; ")
    + `<br><span style="color:#5d6473">Vocabulary and solvability are machine-checked. `
    + `Judge whether it reads naturally and is worth a learner's time.</span>`;
  el("rev-note").value = "";
}

async function decide(decision) {
  const r = S.queue[S.qi];
  if (!r) return;
  const d = await post(`/api/review/${r.id}`, { decision, note: el("rev-note").value });
  renderStats(d.counts);
  S.reviewed++;
  S.queue.splice(S.qi, 1);
  if (S.qi >= S.queue.length) S.qi = Math.max(0, S.queue.length - 1);
  showCard();
}

async function probeCurrent() {
  const r = S.queue[S.qi];
  if (!r) return;
  el("rev-machine").innerHTML = "running the solver panel…";
  try {
    r.probe = await post(`/api/probe/${r.id}`);
  } catch (e) {
    el("rev-machine").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
    return;
  }
  showCard();
}

/* --------------------------------------------------------------- compose */

let cwTimer = null, cwOk = false;

async function loadCompose() {
  const d = await api(`/api/subjects?domain=${S.domain}`);
  el("cw-subject").innerHTML = d.items
    .filter((s) => s.status === "active")
    .map((s) => `<option value="${s.id}">${esc(s.title)}${s.year ? " (" + s.year + ")" : ""}`
      + ` — min ${s.min_frontier}</option>`).join("");
  if (!el("cw-level").options.length) {
    el("cw-level").innerHTML = S.levels.map((lv) => `<option value="${lv}">${lv}</option>`).join("");
    el("cw-level").value = S.levels[1] || S.levels[0];
  }
  checkText();
}

async function checkText() {
  const text = el("cw-text").value;
  const level = Number(el("cw-level").value);
  if (!text.trim()) {
    el("cw-mirror").innerHTML = `<span style="font-family:Inter,sans-serif;font-size:11px">`
      + `Your text appears here word by word, coloured by whether the learner knows it.</span>`;
    el("cw-readout").innerHTML = ""; el("cw-newwords").innerHTML = "";
    el("cw-save").disabled = true; return;
  }
  let d;
  try { d = await post("/api/analyse", { lang: S.lang, level, text }); }
  catch (e) { el("cw-msg").innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }

  const cls = { known: "k", new: "n", name: "p" };
  let out = "", cur = 0;
  d.tokens.forEach((t) => {
    out += esc(text.slice(cur, t.s));
    out += `<span class="${cls[t.kind] || "k"}">${esc(t.w)}</span>`;
    cur = t.e;
  });
  out += esc(text.slice(cur));
  el("cw-mirror").innerHTML = out;

  const n = d.new.length;
  el("cw-readout").innerHTML =
    `<span class="ro2 ${n >= 1 && n <= 3 ? "good" : "bad"}"><b>${n}</b> new word${n === 1 ? "" : "s"} <i>(1-3)</i></span>`
    + `<span class="ro2"><b>${d.ceiling_rank}</b> ceiling <i>(of ${level})</i></span>`
    + `<span class="ro2"><b>${d.stats.sentences}</b> sentences</span>`
    + `<span class="ro2"><b>${d.stats.words_per_sentence}</b> words per sentence</span>`
    + `<span class="ro2"><b>${d.names.length}</b> names</span>`;

  el("cw-newwords").innerHTML = d.new.length
    ? d.new.map((w) => {
        const far = w.rank === null || w.rank > d.shell[1];
        return `<span class="nwc ${far ? "far" : ""}">${esc(w.lemma)}`
          + `<span>${w.rank === null ? "not in list" : "rank " + w.rank}`
          + `${far ? " — too far" : ""}</span></span>`;
      }).join("")
    : `<span class="nwc" style="background:#232830;color:#6b7280">nothing new — the reader learns nothing</span>`;

  cwOk = d.ok;
  el("cw-save").disabled = !d.ok;
  el("cw-msg").innerHTML = d.ok
    ? `<span class="ok">Fits. Saving sends it to the review queue like any generated riddle.</span>`
    : d.reasons.map(esc).join(" · ");
}

async function saveComposed() {
  try {
    const d = await post("/api/compose", {
      lang: S.lang, domain: S.domain,
      level: Number(el("cw-level").value),
      subject_id: Number(el("cw-subject").value),
      text: el("cw-text").value,
      emoji: el("cw-emoji").value,
    });
    el("cw-msg").innerHTML = `<span class="ok">Saved — teaches `
      + `<b>${d.new.map(esc).join(", ")}</b>, ceiling ${d.ceiling_rank}. `
      + `It is in the review queue now.</span>`;
    el("cw-text").value = "";
    checkText(); loadOverview();
  } catch (e) {
    el("cw-msg").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

/* ------------------------------------------------------------ telemetry */

async function loadTelemetry() {
  const d = await api(`/api/telemetry?lang=${S.lang}`);
  const max = Math.max(1, ...d.by_model.map((m) => m.attempts));
  el("tel-models").innerHTML = d.by_model.length ? d.by_model.map((m) => `
    <div class="tm">
      <span class="tn">${esc(m.model)}</span>
      <span class="td">${m.accepted} accepted of ${m.attempts} · ${m.avg_drafts} drafts each · ${m.avg_seconds}s</span>
      <span class="tbar"><span class="tbf" style="width:${Math.round(260 * m.yield)}px"></span></span>
      <span class="ty">${Math.round(m.yield * 100)}%</span>
    </div>`).join("")
    : `<div class="trow">No attempts recorded yet.</div>`;

  el("tel-probes").innerHTML = d.probe_rates.length ? d.probe_rates.map((p) => `
    <div class="pk"><div class="pkn">${esc(p.kind)}</div>
      <div class="pkv">${p.ok}/${p.n}</div></div>`).join("") + `<div style="clear:both"></div>`
    : `<div class="trow">No probes run yet.</div>`;

  el("tel-fails").innerHTML = d.failures.length ? d.failures.map((f) => `
    <div class="trow">${esc(f.outcome)} — ${esc((f.detail || "").slice(0, 90))}
      <span class="r"><b>${f.n}</b></span></div>`).join("")
    : `<div class="trow">Nothing rejected yet.</div>`;

  el("tel-cells").innerHTML = d.attempts.map((a) => `
    <div class="trow">${esc(a.lang)}/${esc(a.domain)} @ ${a.level}
      <span class="r"><b>${a.ok}</b>/${a.n} · ${(a.d || 0).toFixed(1)}d</span></div>`).join("");
}

/* ------------------------------------------------------------- subjects */

function stars(n, measured, cb) {
  const wrap = document.createElement("span");
  wrap.className = "stars" + (measured ? " measured" : "");
  for (let i = 1; i <= 5; i++) {
    const s = document.createElement("i");
    s.textContent = "\u25cf";
    if (i <= n) s.className = "f";
    s.onclick = (e) => { e.stopPropagation(); cb(i); };
    wrap.appendChild(s);
  }
  return wrap;
}

async function loadSubjects() {
  const d = await api(`/api/subjects?domain=${S.domain}`);
  const box = el("subj-rows");
  box.innerHTML = "";
  d.items.forEach((s) => {
    const row = document.createElement("div");
    row.className = "subj";
    row.innerHTML =
      `<span class="sc1 st">${esc(s.title)}<span class="yr">${s.year || ""}</span></span>`
      + `<span class="sc4">${s.min_frontier}</span>`
      + `<span class="sc5 gr">${esc(s.distractor_group || "-")}</span>`
      + `<span class="sc6 cnt">${s.riddles}</span>`
      + `<span class="sc7"><span class="stt ${s.status === "active" ? "" : "off"}">${esc(s.status)}</span></span>`;

    const rec = document.createElement("span"); rec.className = "sc2";
    rec.appendChild(stars(s.recognizability, false, (v) =>
      post(`/api/subjects/${s.id}`, { recognizability: v }).then(loadSubjects)));
    const ret = document.createElement("span"); ret.className = "sc3";
    ret.appendChild(stars(s.retellability, true, (v) =>
      post(`/api/subjects/${s.id}`, { retellability: v }).then(loadSubjects)));
    row.appendChild(rec); row.appendChild(ret);

    row.querySelector(".stt").onclick = () =>
      post(`/api/subjects/${s.id}`, { status: s.status === "active" ? "off" : "active" })
        .then(loadSubjects);
    box.appendChild(row);
  });
}

/* --------------------------------------------------------------- ladder */

async function loadLadder() {
  const lo = Number(el("lad-lo").value) || 1000;
  const d = await api(`/api/ladder?lang=${S.lang}&lo=${lo}&hi=${lo + 90}`);
  el("lad-rows").innerHTML = d.items.map((it) => {
    const on = it.override === null ? it.teachable : it.override;
    return `<div class="lad ${on ? "yes" : "no"}" data-l="${esc(it.lemma)}">
      <span class="lr">${it.rank}</span>
      <span class="ll">${esc(it.lemma)}</span>
      <span class="lw">${esc(it.reasons.join(", ") || (it.override !== null ? "set by hand" : ""))}</span>
      <button class="lb">${on ? "\u2713" : "\u2715"}</button>
    </div>`;
  }).join("") + `<div style="clear:both"></div>`;
  el("lad-rows").querySelectorAll(".lad").forEach((n) => {
    n.querySelector(".lb").onclick = async () => {
      const on = n.classList.contains("yes");
      await post("/api/teachable", { lang: S.lang, lemma: n.dataset.l, teachable: !on });
      loadLadder();
    };
  });
}

/* --------------------------------------------------------------- wiring */

function init() {
  document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => showView(t.dataset.view); });
  el("lang").onchange = () => { S.lang = el("lang").value; S.band = null; showView(S.view); };
  el("domain").onchange = () => { S.domain = el("domain").value; S.band = null; showView(S.view); };
  el("gen-go").onclick = () => queueWork().catch((e) => { el("gen-note").textContent = e.message; });
  el("gen-one").onclick = tryOne;
  el("worker-toggle").onclick = async () => {
    const running = el("worker-toggle").textContent === "stop";
    renderWorker(await post("/api/worker", { action: running ? "stop" : "start" }));
  };
  el("lad-go").onclick = loadLadder;

  el("cw-text").oninput = () => {
    clearTimeout(cwTimer);
    cwTimer = setTimeout(() => checkText().catch(() => {}), 250);
  };
  el("cw-level").onchange = () => checkText();
  el("cw-save").onclick = saveComposed;
  el("cw-clear").onclick = () => { el("cw-text").value = ""; checkText(); };

  el("sa-go").onclick = async () => {
    const title = el("sa-title").value.trim();
    if (!title) { el("sa-msg").textContent = "Needs a title."; return; }
    try {
      await post("/api/subjects/new", {
        domain: S.domain, title,
        year: Number(el("sa-year").value) || null,
        distractor_group: el("sa-group").value.trim() || "misc",
        min_frontier: Number(el("sa-min").value) || 800,
      });
      el("sa-msg").textContent = `Added ${title}.`;
      ["sa-title", "sa-year", "sa-group"].forEach((i) => { el(i).value = ""; });
      loadSubjects();
    } catch (e) { el("sa-msg").textContent = e.message; }
  };

  el("rev-accept").onclick = () => decide("accepted");
  el("rev-reject").onclick = () => decide("rejected");
  el("rev-skip").onclick = () => { S.qi = (S.qi + 1) % Math.max(1, S.queue.length); showCard(); };
  el("rev-probe").onclick = probeCurrent;

  // Review is the throughput bottleneck, so it is keyboard-first.
  document.onkeydown = (e) => {
    if (S.view !== "review" || e.target.tagName === "INPUT") return;
    const k = e.key.toLowerCase();
    if (k === "a") decide("accepted");
    else if (k === "r") decide("rejected");
    else if (k === "s") el("rev-skip").click();
    else if (k === "p") probeCurrent();
  };

  loadOverview().then(() => showView("coverage"));
  S.poll = setInterval(() => {
    loadOverview().catch(() => {});
    if (S.view === "coverage") loadCoverage().catch(() => {});
  }, 3000);
}

document.addEventListener("DOMContentLoaded", init);
