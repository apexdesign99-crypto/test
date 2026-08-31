const form = document.getElementById("login-form");
const token = document.getElementById("token");
const error = document.getElementById("login-error");

function showError(message) {
  error.textContent = message;
  error.hidden = false;
}

// 既にログイン済みなら本体へ戻す
try {
  const state = await (await fetch("/api/session")).json();
  if (!state.authRequired || state.authenticated) location.replace("/");
} catch {
  // 到達できないときはフォームを出したままにする
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: token.value }),
    });
    if (response.ok) {
      location.replace("/");
      return;
    }
    const payload = await response.json().catch(() => ({}));
    showError(payload.error ?? `ログインできませんでした (${response.status})`);
  } catch {
    showError("サーバーに接続できませんでした。");
  }
  token.value = "";
  token.focus();
});
