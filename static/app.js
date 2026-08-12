const LANGUAGES = ["English", "Spanish", "French", "German", "Italian", "Portuguese", "Dutch", "Russian", "Chinese", "Japanese", "Korean"];
const STORAGE_KEY = "cinetot_state";

const el = (id) => document.getElementById(id);

const state = {
  language: "English",
  knownWords: "",
  game: null,
  selectedOption: null,
  wordContext: null,
};

function loadLocalState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved.language) state.language = saved.language;
    if (saved.knownWords) state.knownWords = saved.knownWords;
  } catch (e) {}
}

function saveLocalState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ language: state.language, knownWords: state.knownWords }));
}

function initLanguageSelect() {
  const sel = el("language-select");
  sel.innerHTML = LANGUAGES.map((l) => `<option value="${l}" ${l === state.language ? "selected" : ""}>${l}</option>`).join("");
  sel.addEventListener("change", () => {
    state.language = sel.value;
    saveLocalState();
  });
}

function initKnownWords() {
  const box = el("known-words");
  box.value = state.knownWords;
  box.addEventListener("input", () => {
    state.knownWords = box.value;
    saveLocalState();
  });
}

function showError(msg) {
  const box = el("error-box");
  if (!msg) {
    box.style.display = "none";
    box.textContent = "";
    return;
  }
  box.style.display = "block";
  box.textContent = msg;
}

function cleanWord(raw) {
  return raw.replace(/[^\w\u00C0-\u00FF]/g, "");
}

function renderStory(story, highlightedWords) {
  const container = el("story-text");
  container.innerHTML = "";
  const lower = highlightedWords.map((w) => w.toLowerCase());
  story.split(/(\s+)/).forEach((segment) => {
    if (!segment.trim()) {
      container.appendChild(document.createTextNode(segment));
      return;
    }
    const cw = cleanWord(segment.trim());
    const span = document.createElement("span");
    span.textContent = segment;
    span.className = "word" + (lower.includes(cw.toLowerCase()) ? " highlight" : "");
    span.addEventListener("click", () => openWordModal(cw, story));
    container.appendChild(span);
  });
}

function renderOptions(game) {
  game.options.forEach((opt, i) => {
    const btn = el(`option-${i}`);
    btn.textContent = opt;
    btn.style.display = "block";
    btn.disabled = false;
    btn.className = "option-btn";
    btn.onclick = () => handleGuess(opt);
  });
}

function handleGuess(option) {
  if (state.selectedOption || !state.game) return;
  state.selectedOption = option;
  const correct = state.game.correctAnswer;

  state.game.options.forEach((opt, i) => {
    const btn = el(`option-${i}`);
    btn.disabled = true;
    if (opt === correct) btn.classList.add("correct");
    else if (opt === option) btn.classList.add("wrong");
    else btn.classList.add("muted");
  });

  const fb = el("feedback");
  fb.style.display = "block";
  if (option === correct) {
    fb.textContent = "\u{1F389} Correct! Great reading!";
    fb.className = "correct";
  } else {
    fb.textContent = `\u{1F605} Oops! The correct movie was "${correct}"`;
    fb.className = "wrong";
  }
}

async function handleStartGame() {
  showError(null);
  el("start-btn").disabled = true;
  el("start-btn").textContent = "Loading...";
  state.selectedOption = null;

  try {
    const res = await fetch("/api/game", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: state.language, knownWords: state.knownWords }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Something went wrong");

    state.game = data;
    el("setup-panel").style.display = "none";
    el("story-card").style.display = "block";
    el("quiz-title").style.display = "block";
    el("feedback").style.display = "none";
    el("story-emoji").textContent = data.emoji;
    renderStory(data.story, data.highlightedWords);
    renderOptions(data);
  } catch (err) {
    showError(err.message);
  } finally {
    el("start-btn").disabled = false;
    el("start-btn").textContent = state.game ? "Tell me another story!" : "Tell me a story!";
  }
}

function openWordModal(word, context) {
  if (!word) return;
  state.wordContext = { word, context };
  el("word-modal-title").textContent = word;
  el("word-menu-prompt").textContent = `What would you like to know about "${word}"?`;
  el("word-menu").style.display = "block";
  el("word-explain-view").style.display = "none";
  el("word-overlay").style.display = "block";
}

function closeWordModal() {
  el("word-overlay").style.display = "none";
}

async function handleExplain() {
  el("word-menu").style.display = "none";
  el("word-explain-view").style.display = "block";
  el("word-loading").style.display = "block";
  el("word-explanations").innerHTML = "";
  el("word-counterexamples").innerHTML = "";

  const { word, context } = state.wordContext;
  try {
    const res = await fetch("/api/word", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word, context, language: state.language }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to analyze word");

    el("word-explanations").innerHTML = data.explanations.map((e) => `<div class="word-line explain">${e}</div>`).join("");
    el("word-counterexamples").innerHTML = data.counterExamples.map((e) => `<div class="word-line counter">${e}</div>`).join("");
  } catch (err) {
    el("word-explanations").innerHTML = `<div class="word-line counter">${err.message}</div>`;
  } finally {
    el("word-loading").style.display = "none";
  }
}

function init() {
  loadLocalState();
  initLanguageSelect();
  initKnownWords();
  el("start-btn").addEventListener("click", handleStartGame);
  el("word-modal-close").addEventListener("click", closeWordModal);
  el("word-explain-btn").addEventListener("click", handleExplain);
  el("word-back-btn").addEventListener("click", () => {
    el("word-explain-view").style.display = "none";
    el("word-menu").style.display = "block";
  });
  el("word-overlay").addEventListener("click", (e) => {
    if (e.target.id === "word-overlay") closeWordModal();
  });
}

document.addEventListener("DOMContentLoaded", init);
