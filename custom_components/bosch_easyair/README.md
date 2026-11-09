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

## Entities

Each thermostat on the account becomes one device with:

| Entity | Reads | Writes |
| --- | --- | --- |
| `climate` | mode, action, current temperature, humidity, setpoints | mode, setpoints |
| `sensor` Temperature | `room_temp` in the thermostat's own unit (device class `temperature`) | — |
| `sensor` Humidity | `humidity` in % (device class `humidity`) | — |
| `sensor` Stage | `stage` as reported, with `power` as an attribute | — |
| `switch` Fan circulation | `fanstatus` (`1` = on) | `/control/fan` |

Temperature and humidity are real sensors (not only climate attributes) so
integrations that select inputs by sensor domain and device class — Thermal
Comfort, the statistics helper, long-term statistics — can use them.

The fan switch reads `fanstatus` as the mirror of the `fan` value written by
`/control/fan`: on = continuous circulation, off = auto. If the status payload
omits `fanstatus`, the switch reports unknown rather than guessing.

`stage` and `power` are surfaced raw. The captures show `stage`/`power` non-zero
when equipment is running (see `_hvac_action`), but their full vocabulary —
compressor stage vs. aux/emergency heat — has not been confirmed, so no
meaning is assigned yet.

Current state:

- Home Assistant config flow, coordinator, climate, sensor and switch
  platforms are implemented.
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
- Fan circulation: `POST /control/fan` with `fan` as `"1"`/`"0"`

BCC110 notes from captured traffic:

- `gatewayId` is MAC-shaped and is used as `device_id` for control calls.
- Mode values observed: `0=off`, `1=cool`, `2=heat`, `3=auto`.
- The BCC `temp` field is formatted as `<cool setpoint>-<heat setpoint>`.
  Reads accept integers and decimals. Writes match the captured EasyAir app
  request by emitting exactly one decimal place, including for whole degrees
  (`"75.0-65.0"`). The API rejects integer-looking pairs such as `"75-65"` as
  an invalid request body.

The only module that should need cloud protocol changes is `api.py`.

Setup paths:

- `Sign in with Bosch/SingleKey`: copy the generated login URL, complete the
  browser login, then paste the final `idsmobileapp://?...` redirect URL or its
  `code` value back into Home Assistant.
- `Manual config with tokens`: enter a captured EasyAir access token and
  optional refresh token directly.
