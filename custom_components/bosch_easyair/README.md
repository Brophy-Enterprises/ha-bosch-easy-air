# Bosch EasyAir

Experimental custom integration for Bosch EasyAir devices such as the BCC110.

This integration is intentionally standalone. The older Bosch custom component
is built around `bosch_thermostat_client`, XMPP/HTTP gateway database discovery,
and EMS/IVT/EasyControl circuits. EasyAir uses a different cloud app and should
not be forced through that gateway model.

## Installation

Install this repository as a HACS custom repository with category
`Integration`, or copy `custom_components/bosch_easyair/` into your Home
Assistant `config/custom_components/` directory by hand and restart.

Verified app targets:

- Android package: `com.idsmobileapp`
- iOS bundle: `com.bosch.tt.easyair`
- Bosch product page: `https://www.bosch-homecomfort.com/us/en/ocs/residential/easyair-app-20386966-p/`

Current state:

- Home Assistant config flow, coordinator, and climate entity are scaffolded.
- The cloud client uses the captured EasyAir/BCC API hosts and endpoints.
- Browser-based SingleKey sign-in is supported with a manual final redirect
  paste-back step. The mobile app's registered OAuth redirect URI is
  `idsmobileapp://`, so Home Assistant cannot receive the callback directly.
- Manual access-token setup is still supported. Refresh-token renewal is
  supported when a refresh token is provided.
- Entities are created once at setup, so a thermostat added to the account
  later needs the config entry reloaded before it appears.
- In `auto` (heat/cool) mode, set both the low and high setpoints. A single
  target temperature is ambiguous there and is rejected rather than silently
  applied to the cool setpoint only.
- **Every temperature change sets a permanent hold** (`hold=1`, matching the
  captured app), which overrides the thermostat's own schedule until the hold
  is released from the EasyAir app. Home Assistant cannot release it yet — the
  control call for that was not captured. Setting a setpoint the schedule
  immediately overwrites would be worse, so this is the default, but it is a
  real behaviour change if you run a schedule on the thermostat.
- Local LAN control is not implemented yet. The captures contained only cloud
  traffic, and the thermostat VLAN was not reachable from the development host.

Captured cloud contract:

- OAuth host: `https://auth.smart-climate-ids.com`
- BCC API host: `https://bccapi.smart-climate-ids.com`
- Cognito client id: `7q2puec5cov7ls93041mb38p6v`
- OAuth redirect URI used by the app: `idsmobileapp://`
- OAuth identity provider: `IDS-TTNA`
- OAuth authorization starts at `GET /oauth2/authorize` with PKCE `S256`
- OAuth code exchange uses `POST /oauth2/token` with
  `grant_type=authorization_code`, `client_id`, `redirect_uri`, `code`, and
  `code_verifier`
- Device list: `GET /device/list`
- Detailed thermostat status: `GET /control/status?device_id=<gatewayId>&timestamp=<ms>`
- Set temperature: `POST /control/temp`
- Change mode: `POST /control/change_mode`
- Fan circulation: `POST /control/fan`

BCC110 notes from captured traffic:

- `gatewayId` is MAC-shaped and is used as `device_id` for control calls.
- Mode values observed: `0=off`, `1=cool`, `2=heat`, `3=auto`.
- The BCC `temp` field is formatted as `<cool setpoint>-<heat setpoint>`.
  Reads accept integers and decimals. Writes emit whole degrees without a
  decimal part (`"78-68"`), which is what a Fahrenheit unit produces —
  **unconfirmed against the capture**, which recorded the field shape but not
  the write-direction format. If the cloud rejects or coerces a setpoint,
  check `_format_temp_pair` in `api.py` first.

The only module that should need cloud protocol changes is `api.py`.

Setup paths:

- `Sign in with Bosch/SingleKey`: copy the generated login URL, complete the
  browser login, then paste the final `idsmobileapp://?...` redirect URL or its
  `code` value back into Home Assistant.
- `Manual config with tokens`: enter a captured EasyAir access token and
  optional refresh token directly.
