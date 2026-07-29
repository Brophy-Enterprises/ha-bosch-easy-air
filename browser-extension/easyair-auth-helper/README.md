# Bosch EasyAir Auth Helper

Experimental desktop browser extension for capturing the final Bosch EasyAir
OAuth redirect. The implementation uses WebExtension APIs shared by
Chrome-compatible browsers and Firefox.

The EasyAir mobile OAuth client redirects to:

```text
idsmobileapp://?...code=...&state=...
```

Desktop browsers normally hand that URL to an OS-level app protocol handler.
Chrome exposes the redirect early enough for this extension to capture it first.
When it sees `idsmobileapp://`, it opens a capture page, copies the full
redirect URL to the clipboard, and stores the latest capture in extension
session storage for the popup.

## Chrome local install

Build a Chrome-specific local copy first from this directory:

```text
./build-chrome.sh
```

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select this generated directory:

   ```text
   browser-extension/easyair-auth-helper/build/chrome/easyair-auth-helper
   ```

After installation, start the Bosch EasyAir browser-login flow. When the
SingleKey login completes and Cognito redirects to `idsmobileapp://`, the
extension should copy that full redirect URL to the clipboard and open its
capture popup. Paste the copied URL into Home Assistant's EasyAir authorization
response field.

If you are updating an already-loaded local copy, reload the extension from
`chrome://extensions` before testing the new behavior.

## Firefox local install

Firefox uses a different Manifest V3 background format than Chrome, so build a
Firefox-specific local copy first from this directory:

```text
./build-firefox.sh
```

Then load the generated directory as a temporary extension:

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...**.
3. Select this generated file:

   ```text
   browser-extension/easyair-auth-helper/build/firefox/easyair-auth-helper/manifest.json
   ```

The Chrome and Firefox builds use the same JavaScript, popup, permissions, and
host scope. Only the manifest background declaration changes.

## Notes

- This is for local/development use. Normal end-user distribution generally
  requires publishing through the relevant browser store or packaging system.
- The extension does not see or collect SingleKey credentials.
- The copied redirect URL contains a short-lived OAuth authorization code. Do
  not share it in screenshots, logs, or issue reports.
- The extension observes top-frame redirects from `auth.smart-climate-ids.com`.
- Safari is intentionally not supported by this extension.
- Current desktop browser builds can usually open the toolbar popup
  programmatically. If the browser rejects that popup request, the extension
  opens the same page in a small focused window instead.
- If automatic clipboard writing fails, use one of the copy buttons from the
  popup.
- Browser `manifest.json` files are generated under `build/` so Home
  Assistant's hassfest validator only sees the integration manifest.
