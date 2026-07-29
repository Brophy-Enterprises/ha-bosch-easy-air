const EASYAIR_REDIRECT_PREFIX = "idsmobileapp://";
const CAPTURE_STORAGE_KEY = "lastCapture";
const POPUP_PATH = "popup.html";

const extensionApi = globalThis.browser ?? globalThis.chrome;
const storageArea = extensionApi.storage.session ?? extensionApi.storage.local;

extensionApi.webRequest.onBeforeRedirect.addListener(
  async (details) => {
    if (!details.redirectUrl?.startsWith(EASYAIR_REDIRECT_PREFIX)) {
      return;
    }

    await handleCapturedRedirect({
      redirectUrl: details.redirectUrl,
      sourceUrl: details.url,
      tabId: details.tabId
    });
  },
  {
    urls: [
      "https://auth.smart-climate-ids.com/*"
    ],
    types: ["main_frame"]
  }
);

extensionApi.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "get-last-capture") {
    return sendAsyncResponse(sendResponse, async () => ({
      capture: await getLastCapture()
    }));
  }

  if (message?.type === "update-last-capture") {
    return sendAsyncResponse(sendResponse, async () => {
      const capture = await updateLastCapture(message.patch);
      return { capture };
    });
  }

  return false;
});

async function handleCapturedRedirect(details) {
  if (await isDuplicateCapture(details.redirectUrl)) {
    return;
  }

  const capture = buildCapture(details);
  await saveCapture(capture);
  await setActionStatus("NEW", "#475467", "EasyAir redirect captured");

  try {
    await openCaptureUi(capture);
  } catch (error) {
    await updateLastCapture({ popupError: error.message });
    await setActionStatus("CAP", "#b54708", "EasyAir redirect captured");
  }
}

function buildCapture(details) {
  const params = parseRedirectParams(details.redirectUrl);
  return {
    ...params,
    redirectUrl: details.redirectUrl,
    sourceUrl: details.sourceUrl,
    tabId: details.tabId,
    capturedAt: new Date().toISOString(),
    copied: false,
    copiedAt: ""
  };
}

function parseRedirectParams(redirectUrl) {
  try {
    const parsed = new URL(redirectUrl);
    return {
      code: parsed.searchParams.get("code") || "",
      state: parsed.searchParams.get("state") || "",
      error: parsed.searchParams.get("error") || "",
      errorDescription: parsed.searchParams.get("error_description") || ""
    };
  } catch (error) {
    return {
      code: "",
      state: "",
      error: "parse_failed",
      errorDescription: error.message
    };
  }
}

async function getLastCapture() {
  const result = await storageArea.get(CAPTURE_STORAGE_KEY);
  return result[CAPTURE_STORAGE_KEY] || null;
}

async function saveCapture(capture) {
  await storageArea.set({ [CAPTURE_STORAGE_KEY]: capture });
}

async function isDuplicateCapture(redirectUrl) {
  const current = await getLastCapture();
  if (current?.redirectUrl !== redirectUrl) {
    return false;
  }

  const capturedAt = Date.parse(current.capturedAt || "");
  return Number.isFinite(capturedAt) && Date.now() - capturedAt < 10000;
}

async function updateLastCapture(patch) {
  const current = await getLastCapture();
  if (!current || typeof patch !== "object" || patch === null) {
    return current;
  }

  const capture = {
    ...current,
    ...patch,
    updatedAt: new Date().toISOString()
  };
  await saveCapture(capture);

  if (capture.copied) {
    await setActionStatus("OK", "#1f8f4d", "EasyAir redirect copied");
  }

  return capture;
}

async function setActionStatus(text, color, title) {
  await Promise.all([
    extensionApi.action.setBadgeText({ text }),
    extensionApi.action.setBadgeBackgroundColor({ color }),
    extensionApi.action.setTitle({ title })
  ]);
}

async function openCaptureUi(capture) {
  if (extensionApi.action?.openPopup) {
    try {
      await extensionApi.action.openPopup();
      return;
    } catch (_error) {
      // Continue to the standalone window fallback. The toolbar popup can be
      // rejected depending on browser version, focus state, or extension UI.
    }
  }

  if (!extensionApi.windows?.create) {
    throw new Error("This browser cannot open the extension popup automatically");
  }

  await extensionApi.windows.create({
    url: extensionApi.runtime.getURL(POPUP_PATH),
    type: "popup",
    width: 400,
    height: 520,
    focused: true
  });
}

function sendAsyncResponse(sendResponse, task) {
  task()
    .then((response) => sendResponse(response))
    .catch((error) => sendResponse({ error: error.message }));
  return true;
}
