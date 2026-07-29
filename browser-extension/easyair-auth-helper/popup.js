const statusEl = document.querySelector("#status");
const captureEl = document.querySelector("#capture");
const capturedAtEl = document.querySelector("#capturedAt");
const copiedEl = document.querySelector("#copied");
const codeEl = document.querySelector("#code");
const stateEl = document.querySelector("#state");
const redirectUrlEl = document.querySelector("#redirectUrl");
const copyRedirectButton = document.querySelector("#copyRedirect");
const copyCodeButton = document.querySelector("#copyCode");

const extensionApi = globalThis.browser ?? globalThis.chrome;
let currentCapture;

document.addEventListener("DOMContentLoaded", loadCapture);
copyRedirectButton.addEventListener("click", () => copyRedirectUrl());
copyCodeButton.addEventListener("click", () => copyValue(currentCapture?.code, "code"));

async function loadCapture() {
  const response = await extensionApi.runtime.sendMessage({ type: "get-last-capture" });
  currentCapture = response?.capture || null;

  if (!currentCapture) {
    captureEl.hidden = true;
    statusEl.textContent = "Waiting for an EasyAir redirect.";
    return;
  }

  captureEl.hidden = false;
  renderCapture();

  if (!currentCapture.copied && currentCapture.redirectUrl) {
    statusEl.textContent = "Captured the EasyAir redirect. Copying it now.";
    await copyRedirectUrl({ automatic: true });
  }
}

function renderCapture() {
  statusEl.textContent = currentCapture.error
    ? "Captured an EasyAir error redirect."
    : currentCapture.copied
      ? "Copied the EasyAir redirect URL to the clipboard."
      : "Captured the latest EasyAir redirect.";

  capturedAtEl.textContent = formatDate(currentCapture.capturedAt);
  copiedEl.textContent = currentCapture.copied ? "Yes" : "No";
  codeEl.textContent = currentCapture.code || "(none)";
  stateEl.textContent = currentCapture.state || "(none)";
  redirectUrlEl.value = currentCapture.redirectUrl || "";
  copyCodeButton.disabled = !currentCapture.code;
}

async function copyRedirectUrl(options = {}) {
  const copied = await copyValue(currentCapture?.redirectUrl, "redirect URL", options);
  if (!copied || !currentCapture) {
    return;
  }

  currentCapture = {
    ...currentCapture,
    copied: true,
    copiedAt: new Date().toISOString(),
    copyError: ""
  };
  renderCapture();

  const response = await extensionApi.runtime.sendMessage({
    type: "update-last-capture",
    patch: {
      copied: true,
      copiedAt: currentCapture.copiedAt,
      copyError: ""
    }
  });

  if (response?.capture) {
    currentCapture = response.capture;
    renderCapture();
  }
}

async function copyValue(value, label, options = {}) {
  if (!value) {
    statusEl.textContent = "Nothing to copy.";
    return false;
  }

  try {
    await writeToClipboard(value);
  } catch (error) {
    if (currentCapture && label === "redirect URL") {
      currentCapture = { ...currentCapture, copyError: error.message };
      await extensionApi.runtime.sendMessage({
        type: "update-last-capture",
        patch: { copied: false, copyError: error.message }
      });
    }
    statusEl.textContent = options.automatic
      ? `Captured the redirect, but automatic copy failed: ${error.message}`
      : `Copy failed: ${error.message}`;
    return false;
  }

  statusEl.textContent = `Copied ${label} to clipboard.`;
  return true;
}

async function writeToClipboard(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();

  try {
    if (!document.execCommand("copy")) {
      throw new Error("document.execCommand('copy') returned false");
    }
  } finally {
    textarea.remove();
  }
}

function formatDate(value) {
  if (!value) {
    return "(unknown)";
  }

  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return value;
  }
  return date.toLocaleString();
}
