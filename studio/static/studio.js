/* CineTot Studio front end. No framework. */

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
  const r = await fetch(path, opts);
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
     subjects: loadSubjects, ladder: loadLadder }[v] || (() => {}))();
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
      ? `${d.generator} · live` : `${d.generator} · offline, no API key`;
  }
  renderStats(d.counts);
  renderWorker(d.worker);
  renderJobs(d.jobs);
}

function renderWorker(w) {
  el("worker-dot").classList.toggle("on", w.running);
  const cur = w.current;
  el("worker-text").textContent = w.running
    ? (cur ? `${cur.kind} ${cur.lang}/${cur.domain}@${cur.level} — ${cur.done}/${cur.want}`
           : "waiting for work")
    : (w.log[0] ? w.log[0].msg : "idle");
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
    `Queued ${d.queued.length} job(s), ${want} riddles each. Each accepted riddle is `
    + `automatically sent to the probe panel before it reaches review.`;
  loadOverview();
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
  el("worker-toggle").onclick = async () => {
    const running = el("worker-toggle").textContent === "stop";
    renderWorker(await post("/api/worker", { action: running ? "stop" : "start" }));
  };
  el("lad-go").onclick = loadLadder;

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
