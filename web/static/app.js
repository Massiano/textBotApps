/* CineTot front end. No framework, no build step. */

const el = (id) => document.getElementById(id);
const state = {
  lang: "en",
  domain: "movies",
  rtl: false,
  test: null,          // {test_id, items, picked:Set}
  interest: null,      // {test_id, items, picked:Set}
  topics: [],
  round: null,
  answered: false,
  word: null,
  profile: null,
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch (e) { data = { error: "server returned no JSON" }; }
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------- panels */

function show(name) {
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("on"));
  const p = el("panel-" + name);
  if (p) p.classList.add("on");
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("on", b.dataset.panel === name));
  if (name === "words") loadWordList();
}

/* ------------------------------------------------------------ bootstrap */

async function boot() {
  const data = await api("/api/bootstrap");

  el("lang-select").innerHTML = data.languages
    .map((l) => `<option value="${l.code}">${escapeHtml(l.label)}</option>`).join("");
  el("domain-select").innerHTML = data.domains
    .map((d) => `<option value="${d.id}">${escapeHtml(d.label)}</option>`).join("");

  const saved = localStorage.getItem("cinetot_lang");
  if (saved && data.languages.some((l) => l.code === saved)) state.lang = saved;
  el("lang-select").value = state.lang;
  el("domain-select").value = state.domain;

  if (!data.configured) {
    el("play-status").textContent =
      "The server has no OPENROUTER_API_KEY, so riddles and explanations are switched off. Measuring vocabulary still works.";
  }

  await refreshProfile();
  show("setup");
}

async function refreshProfile() {
  const data = await api("/api/profile?lang=" + encodeURIComponent(state.lang));
  state.profile = data;
  const p = data.profile;

  el("rail-level").textContent = p.provisional ? "Not measured yet" : `CEFR ${p.cefr}`;
  el("rail-vocab").textContent = p.provisional ? "—" : p.vocab_estimate.toLocaleString();
  el("rail-meter-fill").style.width = Math.min(100, (p.vocab_estimate / 10000) * 100) + "%";
  el("rail-frontier").textContent = `texts capped at rank ${p.frontier_rank}, plus three`;

  el("rc-known").querySelector("b").textContent = data.counts.known;
  el("rc-learning").querySelector("b").textContent = data.counts.learning;
  el("rc-unknown").querySelector("b").textContent = data.counts.unknown;

  const queue = data.queue || [];
  el("rail-queue-list").innerHTML = queue.length
    ? queue.slice(0, 26).map((q) =>
        `<span class="qw${q.met ? " hot" : ""}" title="${q.met ? "met in a riddle" : "not met yet"}">`
        + `${escapeHtml(q.lemma)}</span>`).join("")
    : `<div id="rail-queue-empty">Empty. Words land here once you meet them in a riddle or ask to be taught them.</div>`;

  state.topics = data.interests || [];
  renderTopicChips();
  renderOverview(data);
}

function renderOverview(data) {
  const p = data.profile;
  el("setup-text").innerHTML = p.provisional
    ? "Nothing measured in this language yet. The riddles will start at a beginner setting until you take the word check."
    : `Texts are written using the ${p.vocab_estimate.toLocaleString()} most useful words in this language `
      + `— roughly CEFR ${p.cefr} — plus exactly three words just beyond that edge, chosen from your topics where possible.`
      + (p.reliable ? "" : ` <span style="color:#c85a4a">Your last word check had a high rate of yes on invented words, so this estimate is inflated. Worth retaking.</span>`)
      + (p.consistent === false
          ? ` <span style="color:#c85a4a">Your answers jumped around — you knew rarer words after missing commoner ones. The cap of ${p.frontier_rank} is the number being trusted, not the total above.</span>`
          : "");

  el("step-1").classList.toggle("done", !p.provisional);
  el("step-2").classList.toggle("done", (data.interests || []).length > 0);
  el("step-3").classList.toggle("done", data.counts.learning > 0);

  const bands = p.bands || [];
  el("setup-bands").innerHTML = bands.length
    ? `<div id="bands-title">What you know, by frequency band</div>` + bands.map((b) => `
        <div class="bandrow">
          <span class="bl">${b.band}</span>
          <span class="bbar"><span class="bfill" style="width:${Math.round(b.corrected * 700)}px"></span></span>
          <span class="bnum">${Math.round(b.corrected * 100)}% of ${b.range[1] - b.range[0]} words</span>
        </div>`).join("")
    : "";
}

/* ------------------------------------------------------- placement test */

async function startTest() {
  el("test-grid").innerHTML = "";
  el("test-report").innerHTML = "";
  el("test-count").textContent = "loading…";
  const data = await post("/api/placement/start", { lang: state.lang });
  state.test = { id: data.test_id, items: data.items, picked: new Set() };
  el("test-instructions").textContent = data.instructions;
  el("test-grid").innerHTML = data.items
    .map((it) => `<button class="chip" data-id="${it.id}">${escapeHtml(it.word)}</button>`).join("");
  el("test-grid").querySelectorAll(".chip").forEach((c) => {
    c.onclick = () => {
      const id = Number(c.dataset.id);
      if (state.test.picked.has(id)) state.test.picked.delete(id);
      else state.test.picked.add(id);
      c.classList.toggle("on");
      updateTestCount();
    };
  });
  updateTestCount();
}

function updateTestCount() {
  el("test-count").textContent =
    `${state.test.picked.size} of ${state.test.items.length} marked as known`;
}

async function submitTest() {
  if (!state.test) return;
  const responses = {};
  state.test.items.forEach((it) => { responses[it.id] = state.test.picked.has(it.id); });
  el("test-report").innerHTML = "scoring…";
  const rep = await post("/api/placement/submit", { test_id: state.test.id, responses });

  el("test-report").innerHTML =
    `<b>${rep.vocab_estimate.toLocaleString()} words</b> — CEFR ${rep.cefr}. `
    + `Texts will now be capped at rank ${rep.frontier_rank}.<br>`
    + `You said yes to ${Math.round(rep.false_alarm_rate * 100)}% of the invented words`
    + (rep.reliable
        ? `, low enough for the estimate to hold.`
        : `<span class="warn">, high enough that the number above is inflated. Try again and only mark words you could define.</span>`);

  // Reveal which chips were invented, so the check teaches something.
  state.test.items.forEach((it) => {
    const chip = el("test-grid").querySelector(`[data-id="${it.id}"]`);
    if (!chip) return;
    chip.classList.add(rep.reveal[String(it.id)] ? "real-yes" : "real-no");
    chip.title = rep.reveal[String(it.id)] ? "a real word" : "invented";
  });

  await refreshProfile();
}

/* ------------------------------------------------------------ interests */

function renderTopicChips() {
  el("topic-chips").innerHTML = state.topics
    .map((t, i) => `<span class="tchip">${escapeHtml(t)}<u data-i="${i}">&times;</u></span>`).join("");
  el("topic-chips").querySelectorAll("u").forEach((u) => {
    u.onclick = () => { state.topics.splice(Number(u.dataset.i), 1); renderTopicChips(); };
  });
}

function addTopic() {
  const v = el("topic-input").value.trim();
  if (!v) return;
  v.split(",").map((s) => s.trim()).filter(Boolean).forEach((t) => {
    if (state.topics.length < 8 && !state.topics.includes(t)) state.topics.push(t);
  });
  el("topic-input").value = "";
  renderTopicChips();
}

async function findTopicWords() {
  if (!state.topics.length) { el("interest-status").textContent = "Add at least one topic first."; return; }
  el("interest-status").textContent = "Asking for words that come up in those topics…";
  el("interest-grid").innerHTML = "";
  el("interest-submit").style.display = "none";
  try {
    const data = await post("/api/interest/start", { lang: state.lang, topics: state.topics });
    state.interest = { id: data.test_id, items: data.items, picked: new Set() };
    el("interest-status").textContent =
      `${data.items.length} words. Mark the ones you already know — the rest become candidates for teaching.`;
    el("interest-grid").innerHTML = data.items
      .map((it) => `<button class="chip" data-id="${it.id}" title="${it.band}">${escapeHtml(it.word)}</button>`).join("");
    el("interest-grid").querySelectorAll(".chip").forEach((c) => {
      c.onclick = () => {
        const id = Number(c.dataset.id);
        if (state.interest.picked.has(id)) state.interest.picked.delete(id);
        else state.interest.picked.add(id);
        c.classList.toggle("on");
      };
    });
    el("interest-submit").style.display = "block";
  } catch (e) {
    el("interest-status").textContent = e.message;
  }
}

async function submitInterest() {
  if (!state.interest) return;
  const responses = {};
  state.interest.items.forEach((it) => { responses[it.id] = state.interest.picked.has(it.id); });
  const r = await post("/api/interest/submit", { test_id: state.interest.id, responses });
  el("interest-status").textContent =
    `Saved. ${r.known} already known, ${r.to_learn} queued to be taught in future riddles.`;
  await refreshProfile();
}

/* ----------------------------------------------------------------- play */

async function playRound() {
  el("play-btn").disabled = true;
  el("play-btn").textContent = "Writing…";
  el("play-status").textContent = "Drafting, then checking every word against your level. This can take a few tries.";
  el("verdict").style.display = "none";
  el("quality").textContent = "";
  state.answered = false;

  try {
    const r = await post("/api/round", { lang: state.lang, domain: state.domain });
    state.round = r;
    state.rtl = r.rtl;

    el("screen-empty").style.display = "none";
    el("screen-legend").style.display = "block";
    el("screen-emoji").textContent = r.emoji;
    renderTokens(r);
    el("quiz-q").style.display = "block";
    r.options.forEach((opt, i) => {
      const b = el("opt-" + i);
      b.textContent = opt;
      b.style.display = "block";
      b.disabled = false;
      b.className = "opt";
      b.onclick = () => guess(opt);
    });

    el("play-status").textContent = "";
    el("quality").innerHTML = r.new.length
      ? `New here: <b>${r.new.map(escapeHtml).join(", ")}</b>`
        + ` · everything else is inside your vocabulary`
        + (r.source === "live" ? " · written just now" : "")
      : `Nothing new in this one — read it for speed.`
        + (r.source === "live" ? " · written just now" : "");
    refreshProfile();
  } catch (e) {
    el("play-status").textContent = e.message;
  } finally {
    el("play-btn").disabled = false;
    el("play-btn").textContent = "Another riddle";
  }
}

function renderTokens(r) {
  const box = el("screen-text");
  box.className = state.rtl ? "rtl" : "";
  box.innerHTML = "";
  let cursor = 0;
  r.tokens.forEach((t) => {
    if (t.s > cursor) box.appendChild(document.createTextNode(r.text.slice(cursor, t.s)));
    const span = document.createElement("span");
    span.className = "w " + t.kind;
    span.textContent = t.w;
    span.onclick = () => openWord(t.w, r.text);
    box.appendChild(span);
    cursor = t.e;
  });
  if (cursor < r.text.length) box.appendChild(document.createTextNode(r.text.slice(cursor)));
}

async function guess(choice) {
  if (state.answered || !state.round) return;
  state.answered = true;
  const res = await post(`/api/round/${state.round.round_id}/answer`, { choice });

  state.round.options.forEach((opt, i) => {
    const b = el("opt-" + i);
    b.disabled = true;
    if (opt === res.answer) b.classList.add("right");
    else if (opt === choice) b.classList.add("wrong");
    else b.classList.add("dim");
  });

  const v = el("verdict");
  v.style.display = "block";
  v.className = res.correct ? "right" : "wrong";
  v.textContent = res.correct
    ? "Right. Click the amber words before you move on — that is the part you are here for."
    : `Not this time. It was ${res.answer}.`;
}

/* -------------------------------------------------------- word overlay */

async function openWord(word, context) {
  state.word = { word, context };
  el("overlay").style.display = "block";
  el("wp-word").textContent = word;
  el("wp-lemma").textContent = "";
  el("wp-facts").innerHTML = "";
  el("wp-loading").style.display = "block";
  ["wp-explain", "wp-dict", "wp-images", "wp-notes"].forEach((i) => { el(i).innerHTML = ""; });
  document.querySelectorAll(".wp-mark").forEach((b) => b.classList.remove("on"));

  try {
    const d = await post("/api/word", { lang: state.lang, word, context });
    state.word.lemma = d.lexical.lemma;
    renderWord(d);
  } catch (e) {
    el("wp-notes").innerHTML = `<div class="note">${escapeHtml(e.message)}</div>`;
  } finally {
    el("wp-loading").style.display = "none";
  }
}

function renderWord(d) {
  const lx = d.lexical;
  const ex = d.explanation;

  el("wp-lemma").textContent = lx.inflected
    ? `inflected form of "${lx.lemma}"` : "dictionary form";

  const facts = [
    ["frequency band", lx.band === "rare" ? "outside the top 30,000" : `${lx.band} most common`],
    ["Zipf", lx.zipf],
  ];
  if (lx.rank != null) facts.push(["rank", "#" + lx.rank.toLocaleString()]);
  if (ex && ex.part_of_speech) facts.push(["part of speech", ex.part_of_speech]);
  const registers = (d.dictionary && d.dictionary.registers) || [];
  const reg = registers.length ? registers.join(", ") : (ex && ex.register) || "neutral";
  facts.push(["register", reg]);
  el("wp-facts").innerHTML = facts
    .map(([k, v]) => `<span class="fact">${escapeHtml(k)} <b>${escapeHtml(v)}</b></span>`).join("");

  let html = "";
  if (ex) {
    if (ex.english) html += `<div class="sec"><div class="sec-h">In English</div><div style="font-size:15px">${escapeHtml(ex.english)}</div></div>`;
    if (ex.senses && ex.senses.length) {
      html += `<div class="sec"><div class="sec-h">Explained with words you already have</div>`
        + ex.senses.map((s) =>
            `<div class="gloss">${escapeHtml(s.gloss)}<em>${escapeHtml(s.example)}</em></div>`).join("")
        + `</div>`;
    }
    if (ex.not_this && ex.not_this.length) {
      html += `<div class="sec"><div class="sec-h">What it is not</div>`
        + ex.not_this.map((n) => `<div class="notthis">${escapeHtml(n)}</div>`).join("") + `</div>`;
    }
    if (ex.synonyms && ex.synonyms.length) {
      html += `<div class="sec"><div class="sec-h">Near neighbours</div>`
        + ex.synonyms.map((s) => `<span class="syn" data-w="${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")
        + `<div style="clear:both"></div></div>`;
    }
  }
  el("wp-explain").innerHTML = html;
  el("wp-explain").querySelectorAll(".syn").forEach((s) => {
    s.onclick = () => openWord(s.dataset.w, state.word.context);
  });

  if (d.dictionary) {
    el("wp-dict").innerHTML = `<div class="sec"><div class="sec-h">Wiktionary senses</div>`
      + d.dictionary.entries.map((e) =>
          `<div class="dpos">${escapeHtml(e.pos)}</div>` + e.senses.map((s) =>
            `<div class="dsense">`
            + s.labels.map((l) => `<span class="dlabel">${escapeHtml(l)}</span>`).join("")
            + escapeHtml(s.gloss)
            + s.examples.map((x) => `<span class="dex">${escapeHtml(x)}</span>`).join("")
            + `</div>`).join("")).join("")
      + `<div class="srcline"><a href="${escapeHtml(d.dictionary.url)}" target="_blank" rel="noopener">${escapeHtml(d.dictionary.source)}</a></div></div>`;
  }

  if (d.images && d.images.length) {
    el("wp-images").innerHTML = `<div class="sec"><div class="sec-h">Pictures</div>`
      + d.images.map((im) =>
          `<span class="img"><img src="${escapeHtml(im.thumb)}" alt="${escapeHtml(im.title)}" loading="lazy">`
          + `<span>${escapeHtml(im.credit)}</span></span>`).join("")
      + `<div style="clear:both"></div></div>`;
  }

  if (d.notes && d.notes.length) {
    el("wp-notes").innerHTML = d.notes.map((n) => `<div class="note">${escapeHtml(n)}</div>`).join("");
  }
}

async function markWord(stateName, btn) {
  if (!state.word || !state.word.lemma) return;
  await post("/api/word/mark", { lang: state.lang, lemma: state.word.lemma, state: stateName });
  document.querySelectorAll(".wp-mark").forEach((b) => b.classList.remove("on"));
  btn.classList.add("on");
  refreshProfile();
}

/* -------------------------------------------------------------- word list */

async function loadWordList() {
  const d = await api("/api/vocabulary?lang=" + encodeURIComponent(state.lang));
  if (!d.words.length) {
    el("words-table").innerHTML = `<div style="color:#6b6f7a;font-size:13px">Nothing recorded yet. Take the word check or play a round.</div>`;
    return;
  }
  const states = ["known", "learning", "unknown", "mastered"];
  el("words-table").innerHTML = d.words.map((w) => `
    <div class="wrow">
      <span class="wl">${escapeHtml(w.lemma)}</span>
      <span class="wm">seen ${w.seen_count}× · from ${escapeHtml(w.source || "?")}</span>
      <span class="ws">${states.map((s) =>
        `<button data-l="${escapeHtml(w.lemma)}" data-s="${s}" class="${w.state === s ? "on" : ""}">${s}</button>`).join("")}</span>
    </div>`).join("");
  el("words-table").querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      await post("/api/word/mark", { lang: state.lang, lemma: b.dataset.l, state: b.dataset.s });
      loadWordList(); refreshProfile();
    };
  });
}

/* ---------------------------------------------------------------- wiring */

function init() {
  document.querySelectorAll(".nav-btn").forEach((b) => { b.onclick = () => show(b.dataset.panel); });
  document.querySelectorAll(".step .go").forEach((b) => {
    b.onclick = () => { show(b.dataset.panel); if (b.dataset.panel === "test") startTest(); };
  });

  el("lang-select").onchange = async () => {
    state.lang = el("lang-select").value;
    localStorage.setItem("cinetot_lang", state.lang);
    state.test = state.interest = state.round = null;
    el("test-grid").innerHTML = ""; el("test-report").innerHTML = "";
    await refreshProfile();
  };
  el("domain-select").onchange = () => { state.domain = el("domain-select").value; };

  el("test-submit").onclick = () => submitTest().catch((e) => { el("test-report").textContent = e.message; });
  el("test-restart").onclick = () => startTest();


  el("play-btn").onclick = playRound;

  el("wp-close").onclick = () => { el("overlay").style.display = "none"; };
  el("overlay").onclick = (e) => { if (e.target.id === "overlay") el("overlay").style.display = "none"; };
  document.querySelectorAll(".wp-mark").forEach((b) => { b.onclick = () => markWord(b.dataset.state, b); });
  document.onkeydown = (e) => { if (e.key === "Escape") el("overlay").style.display = "none"; };

  boot().catch((e) => { el("setup-text").textContent = "Could not start: " + e.message; });
}

document.addEventListener("DOMContentLoaded", init);
