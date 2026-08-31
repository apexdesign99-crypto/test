// ブラウザ側の音声会話ループ:
//   音声認識 (Web Speech API) → /api/chat (SSE) → 音声合成
// 返答は文が完成したそばから読み上げるので、生成が終わるのを待たずに話し始める。

const $ = (id) => document.getElementById(id);

const els = {
  status: $("status"),
  notice: $("notice"),
  log: $("log"),
  mic: $("mic"),
  micLabel: $("mic-label"),
  reset: $("reset"),
  textForm: $("text-form"),
  textInput: $("text-input"),
  lang: $("lang"),
  voice: $("voice"),
  rate: $("rate"),
  rateOut: $("rate-out"),
  handsfree: $("handsfree"),
  speak: $("speak"),
};

const STORE_KEY = "voice-chat-settings";
const MAX_HISTORY = 40; // サーバー側の上限と揃える

const LABELS = {
  "ja-JP": {
    idle: "待機中",
    listening: "聞いています…",
    thinking: "考えています…",
    speaking: "話しています…",
    talk: "話す",
    stop: "止める",
    empty: "マイクを押して話しかけてください。",
    noSpeech: "うまく聞き取れませんでした。もう一度どうぞ。",
    micDenied: "マイクの使用が許可されていません。ブラウザの設定を確認してください。",
    unsupported: "このブラウザは音声認識に対応していません（Chrome・Edge・Safari を推奨）。テキスト入力は使えます。",
    refused: "この内容には答えられませんでした。",
  },
  "en-US": {
    idle: "Idle",
    listening: "Listening…",
    thinking: "Thinking…",
    speaking: "Speaking…",
    talk: "Talk",
    stop: "Stop",
    empty: "Press the mic and start talking.",
    noSpeech: "I did not catch that. Please try again.",
    micDenied: "Microphone access is blocked. Check your browser settings.",
    unsupported: "This browser does not support speech recognition (Chrome, Edge, or Safari recommended). Text input still works.",
    refused: "I could not answer that.",
  },
};

const settings = {
  lang: "ja-JP",
  voiceURI: "",
  rate: 1,
  handsfree: false,
  speak: true,
  ...loadSettings(),
};

/** @type {{role: "user" | "assistant", content: string}[]} */
const history = [];

let sending = false;
let listening = false;
let inFlight = null; // AbortController

const t = () => LABELS[settings.lang] ?? LABELS["ja-JP"];

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function saveSettings() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(settings));
  } catch {
    // プライベートモードなどで書けない場合は黙って諦める
  }
}

// ---------------------------------------------------------------- 表示

function setStatus(state) {
  els.status.dataset.state = state;
  els.status.textContent = t()[state] ?? state;
}

function showNotice(message) {
  els.notice.textContent = message;
  els.notice.hidden = !message;
}

function renderEmpty() {
  els.log.innerHTML = "";
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = t().empty;
  els.log.append(p);
}

function addTurn(role, text, extraClass = "") {
  els.log.querySelector(".empty")?.remove();
  const div = document.createElement("div");
  div.className = `turn ${role}${extraClass ? " " + extraClass : ""}`;
  div.textContent = text;
  els.log.append(div);
  els.log.scrollTop = els.log.scrollHeight;
  return div;
}

function scrollToEnd() {
  els.log.scrollTop = els.log.scrollHeight;
}

// ---------------------------------------------------------------- 読み上げ

const synth = window.speechSynthesis;

const tts = {
  buffer: "",
  queue: [],
  current: null,
  onDrain: null,

  /** 文の切れ目まで溜まったぶんだけ読み上げに回す。 */
  push(text) {
    if (!settings.speak || !synth) return;
    this.buffer += text;
    const boundary = /[^。．.!?！？\n]*[。．.!?！？\n]+/g;
    let taken = 0;
    let match;
    while ((match = boundary.exec(this.buffer)) !== null) {
      this.enqueue(match[0]);
      taken = boundary.lastIndex;
    }
    if (taken > 0) this.buffer = this.buffer.slice(taken);

    // 句点が来ないまま長くなったら読点で区切る（無音の間を作らないため）。
    if (this.buffer.length > 90) {
      const comma = Math.max(this.buffer.lastIndexOf("、"), this.buffer.lastIndexOf(","));
      if (comma > 20) {
        this.enqueue(this.buffer.slice(0, comma + 1));
        this.buffer = this.buffer.slice(comma + 1);
      }
    }
  },

  /** 残りを全部読み上げる。 */
  flush() {
    if (this.buffer.trim()) this.enqueue(this.buffer);
    this.buffer = "";
    if (!this.current && this.queue.length === 0) this.drain();
  },

  enqueue(text) {
    const trimmed = text.trim();
    if (!trimmed) return;
    this.queue.push(trimmed);
    this.speakNext();
  },

  speakNext() {
    if (!synth || this.current || this.queue.length === 0) return;
    const text = this.queue.shift();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = settings.lang;
    utterance.rate = settings.rate;
    const voice = availableVoices().find((v) => v.voiceURI === settings.voiceURI);
    if (voice) utterance.voice = voice;

    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearInterval(watchdog);
      if (this.current !== utterance) return; // cancel() 済み
      this.current = null;
      if (this.queue.length > 0) this.speakNext();
      else if (!sending) this.drain();
    };

    // 読み上げエンジンが黙って止まることがある（Chrome は長文で勝手に一時停止する）。
    // onend が来ないまま先に進めなくなるのを防ぐ見張り。
    const watchdog = setInterval(() => {
      if (synth.paused) synth.resume();
      else if (!synth.speaking && !synth.pending) finish();
    }, 1000);

    utterance.onend = finish;
    utterance.onerror = finish;
    this.current = utterance;
    setStatus("speaking");
    synth.speak(utterance);
  },

  drain() {
    const cb = this.onDrain;
    this.onDrain = null;
    if (cb) cb();
    else if (!listening && !sending) setStatus("idle");
  },

  cancel() {
    this.buffer = "";
    this.queue = [];
    this.current = null;
    this.onDrain = null;
    synth?.cancel();
  },
};

function availableVoices() {
  return synth ? synth.getVoices() : [];
}

function fillVoiceList() {
  const prefix = settings.lang.slice(0, 2);
  const voices = availableVoices().filter((v) => v.lang.toLowerCase().startsWith(prefix));
  els.voice.innerHTML = "";

  if (voices.length === 0) {
    const option = new Option("（この言語の音声が見つかりません）", "");
    els.voice.append(option);
    return;
  }
  for (const voice of voices) {
    els.voice.append(new Option(`${voice.name} (${voice.lang})`, voice.voiceURI));
  }
  if (!voices.some((v) => v.voiceURI === settings.voiceURI)) {
    settings.voiceURI = voices[0].voiceURI;
  }
  els.voice.value = settings.voiceURI;
}

// ---------------------------------------------------------------- 音声認識

const SpeechRecognitionCtor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
let recognition = null;
let interimBubble = null;
let finalTranscript = "";

function buildRecognition() {
  if (!SpeechRecognitionCtor) return null;
  const rec = new SpeechRecognitionCtor();
  rec.lang = settings.lang;
  rec.interimResults = true;
  rec.continuous = false;
  rec.maxAlternatives = 1;

  rec.onstart = () => {
    listening = true;
    finalTranscript = "";
    els.mic.setAttribute("aria-pressed", "true");
    els.micLabel.textContent = t().stop;
    setStatus("listening");
  };

  rec.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (result.isFinal) finalTranscript += result[0].transcript;
      else interim += result[0].transcript;
    }
    const preview = (finalTranscript + interim).trim();
    if (preview) {
      if (!interimBubble) interimBubble = addTurn("user", preview, "interim");
      else {
        interimBubble.textContent = preview;
        scrollToEnd();
      }
    }
  };

  rec.onerror = (event) => {
    if (event.error === "no-speech") showNotice(t().noSpeech);
    else if (event.error === "not-allowed" || event.error === "service-not-allowed") showNotice(t().micDenied);
    else if (event.error !== "aborted") showNotice(`音声認識エラー: ${event.error}`);
  };

  rec.onend = () => {
    listening = false;
    els.mic.setAttribute("aria-pressed", "false");
    els.micLabel.textContent = t().talk;
    interimBubble?.remove();
    interimBubble = null;

    const text = finalTranscript.trim();
    finalTranscript = "";
    if (text) sendTurn(text);
    else if (!sending) setStatus("idle");
  };

  return rec;
}

function startListening() {
  if (!recognition || listening) return;
  tts.cancel(); // バージイン: 読み上げ中に話しかけられたら黙る
  showNotice("");
  recognition.lang = settings.lang;
  try {
    recognition.start();
  } catch {
    // start() の二重呼び出しは無視してよい
  }
}

function stopListening() {
  if (recognition && listening) recognition.stop();
}

// ---------------------------------------------------------------- 送信

async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

async function sendTurn(text) {
  if (sending) return;
  sending = true;
  showNotice("");
  tts.cancel();
  setStatus("thinking");

  history.push({ role: "user", content: text });
  addTurn("user", text);

  const bubble = addTurn("assistant", "");
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  cursor.textContent = "▍";
  bubble.append(cursor);

  let reply = "";
  inFlight = new AbortController();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        messages: history.slice(-MAX_HISTORY),
        lang: settings.lang.startsWith("en") ? "en" : "ja",
      }),
      signal: inFlight.signal,
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error ?? `サーバーエラー (${response.status})`);
    }

    for await (const { event, data } of sseEvents(response)) {
      if (event === "delta") {
        reply += data.text;
        cursor.before(data.text);
        scrollToEnd();
        tts.push(data.text);
      } else if (event === "error") {
        throw new Error(data.message);
      } else if (event === "done" && data.refused) {
        showNotice(t().refused);
      }
    }

    cursor.remove();
    if (reply.trim()) history.push({ role: "assistant", content: reply });
    else bubble.remove();
  } catch (error) {
    cursor.remove();
    if (error.name !== "AbortError") {
      if (!reply.trim()) bubble.remove();
      addTurn("assistant", `⚠ ${error.message}`, "error");
    }
  } finally {
    inFlight = null;
    // 読み上げが終わってからマイクを開く（自分の声を拾わないため）。
    // sending を落とす前に登録しないと、最後の発話が先に終わったときに取りこぼす。
    tts.onDrain = () => {
      setStatus("idle");
      if (settings.handsfree && recognition) startListening();
    };
    sending = false;
    tts.flush();
  }
}

// ---------------------------------------------------------------- 入力の配線

els.mic.addEventListener("click", () => {
  if (listening) stopListening();
  else if (sending) {
    inFlight?.abort();
    tts.cancel();
    setStatus("idle");
  } else startListening();
});

els.textForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = els.textInput.value.trim();
  if (!text) return;
  els.textInput.value = "";
  stopListening();
  sendTurn(text);
});

els.reset.addEventListener("click", () => {
  inFlight?.abort();
  stopListening();
  tts.cancel();
  history.length = 0;
  renderEmpty();
  showNotice("");
  setStatus("idle");
});

els.lang.addEventListener("change", () => {
  settings.lang = els.lang.value;
  saveSettings();
  if (recognition) recognition.lang = settings.lang;
  fillVoiceList();
  setStatus(listening ? "listening" : "idle");
  els.micLabel.textContent = listening ? t().stop : t().talk;
  if (history.length === 0) renderEmpty();
});

els.voice.addEventListener("change", () => {
  settings.voiceURI = els.voice.value;
  saveSettings();
});

els.rate.addEventListener("input", () => {
  settings.rate = Number(els.rate.value);
  els.rateOut.textContent = settings.rate.toFixed(1);
  saveSettings();
});

for (const key of ["handsfree", "speak"]) {
  els[key].addEventListener("change", () => {
    settings[key] = els[key].checked;
    if (key === "speak" && !settings.speak) tts.cancel();
    saveSettings();
  });
}

// スペースキーでも話し始められるようにする（入力欄にいるときを除く）
document.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.target instanceof HTMLInputElement) return;
  if (event.repeat) return;
  event.preventDefault();
  if (listening) stopListening();
  else startListening();
});

// ---------------------------------------------------------------- 起動

els.lang.value = settings.lang;
els.rate.value = String(settings.rate);
els.rateOut.textContent = Number(settings.rate).toFixed(1);
els.handsfree.checked = settings.handsfree;
els.speak.checked = settings.speak;

renderEmpty();
setStatus("idle");

if (synth) {
  fillVoiceList();
  synth.addEventListener("voiceschanged", fillVoiceList);
} else {
  els.speak.checked = false;
  els.speak.disabled = true;
  settings.speak = false;
}

recognition = buildRecognition();
if (!recognition) {
  els.mic.disabled = true;
  showNotice(t().unsupported);
  els.textInput.focus();
}
