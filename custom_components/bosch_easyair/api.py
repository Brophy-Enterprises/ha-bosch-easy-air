"""Client primitives for Bosch EasyAir/BCC thermostats."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from json import loads as json_loads
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from homeassistant.const import UnitOfTemperature

from .const import (
    BCC_API_BASE_URL,
    COGNITO_CLIENT_ID,
    COGNITO_OAUTH_REDIRECT_URI,
    DEFAULT_LANGUAGE_TAG,
    EASYAIR_AUTH_BASE_URL,
    EASYAIR_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_PATH = "/oauth2/token"

# RFC 6749 error codes that mean the grant itself is dead. Cognito answers with
# HTTP 400 (not 401) for an expired or revoked refresh token, so these have to
# be mapped explicitly or reauth never fires.
OAUTH_AUTH_ERROR_CODES = frozenset(
    {
        "access_denied",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)

# Setpoint pairs arrive as "<cool>-<heat>"; both halves may be negative, so the
# separator cannot be found by splitting on the first "-".
_TEMP_PAIR_RE = re.compile(
    r"^\s*(?P<cool>[+-]?\d+(?:\.\d+)?)\s*-\s*(?P<heat>[+-]?\d+(?:\.\d+)?)\s*$"
)

# Keys we know how to read out of a /control/status payload. Used to detect a
# response shape we did not expect (e.g. a {"data": {...}} envelope).
_STATUS_FIELDS = frozenset(
    {
        "fanstatus",
        "humidity",
        "mode",
        "power",
        "roomTemp",
        "room_temp",
        "stage",
        "temp",
        "tempUnit",
        "temp_unit",
    }
)

BCC_MODE_OFF = "0"
BCC_MODE_COOL = "1"
BCC_MODE_HEAT = "2"
BCC_MODE_AUTO = "3"

BCC_TO_HVAC_MODE = {
    BCC_MODE_OFF: "off",
    BCC_MODE_COOL: "cool",
    BCC_MODE_HEAT: "heat",
    BCC_MODE_AUTO: "auto",
}
HVAC_MODE_TO_BCC = {value: key for key, value in BCC_TO_HVAC_MODE.items()}


class EasyAirError(Exception):
    """Base exception for EasyAir API errors."""


class EasyAirAuthError(EasyAirError):
    """Raised when the EasyAir API rejects credentials."""


class EasyAirConnectionError(EasyAirError):
    """Raised when the EasyAir API cannot be reached."""


class EasyAirInvalidInputError(EasyAirError):
    """Raised when a requested change cannot be expressed for this thermostat.

    Distinct from the connection/auth failures because it is the caller's
    request that is wrong, not the cloud: entity code maps it to
    ``ServiceValidationError`` so the message reaches the user.
    """


@dataclass(frozen=True)
class EasyAirTokens:
    """EasyAir OAuth tokens."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None


@dataclass(frozen=True)
class EasyAirThermostat:
    """Normalized thermostat state used by Home Assistant entities."""

    id: str
    name: str
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    wifi_firmware_version: str | None
    current_temperature: float | None
    target_temperature: float | None
    target_temperature_low: float | None
    target_temperature_high: float | None
    min_temperature: float | None
    max_temperature: float | None
    humidity: int | None
    temperature_unit: UnitOfTemperature
    hvac_mode: str | None
    hvac_action: str | None
    available_modes: list[str]
    raw: Mapping[str, Any]


class EasyAirClient:
    """Async client for the Bosch EasyAir BCC cloud API."""

    def __init__(
        self,
        session: ClientSession,
        access_token: str,
        refresh_token: str | None = None,
        token_updater: Callable[[EasyAirTokens], Awaitable[None]] | None = None,
        api_base_url: str = BCC_API_BASE_URL,
        auth_base_url: str = EASYAIR_AUTH_BASE_URL,
        language_tag: str = DEFAULT_LANGUAGE_TAG,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_updater = token_updater
        self._api_base_url = api_base_url.rstrip("/") + "/"
        self._auth_base_url = auth_base_url.rstrip("/") + "/"
        self._language_tag = language_tag
        self._refresh_lock = asyncio.Lock()

    async def async_get_thermostats(self) -> list[EasyAirThermostat]:
        """Return BCC thermostats visible to the configured EasyAir account."""
        payload = await self._request("GET", "/device/list")
        devices = payload.get("data", []) if isinstance(payload, Mapping) else []
        thermostats: list[EasyAirThermostat] = []

        for device in devices:
            if not isinstance(device, Mapping):
                continue
            device_id = _as_string(device.get("gatewayId"))
            if not device_id:
                continue

            raw = dict(device)
            try:
                status = await self.async_get_status(device_id)
            except EasyAirError as err:
                _LOGGER.debug(
                    "Falling back to device/list state for %s: %s", device_id, err
                )
            else:
                if isinstance(status, Mapping):
                    raw.update(_unwrap_status(status, device_id))
                elif status is not None:
                    _LOGGER.debug(
                        "Ignoring non-mapping status for %s: %r", device_id, status
                    )

            thermostats.append(_parse_thermostat(raw))

        _LOGGER.debug("Parsed %s EasyAir thermostat(s)", len(thermostats))
        return thermostats

    async def async_get_status(self, device_id: str) -> Any:
        """Return detailed BCC status for a thermostat.

        The BCC endpoint normally returns a mapping, but a 204 yields ``None``
        and a malformed response could yield a non-mapping value; callers must
        guard accordingly.
        """
        return await self._request(
            "GET",
            "/control/status",
            params={"device_id": device_id, "timestamp": _timestamp_ms()},
        )

    async def async_exchange_authorization_code(
        self, code: str, code_verifier: str
    ) -> EasyAirTokens:
        """Exchange an EasyAir authorization code for OAuth tokens."""
        payload = await self._request(
            "POST",
            TOKEN_PATH,
            base_url=self._auth_base_url,
            auth=False,
            token_request=True,
            form={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": COGNITO_CLIENT_ID,
                "redirect_uri": COGNITO_OAUTH_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
        tokens = _tokens_from_payload(payload)
        self._access_token = tokens.access_token
        self._refresh_token = tokens.refresh_token
        if self._token_updater:
            await self._token_updater(tokens)
        return tokens

    async def async_set_temperature(
        self,
        device_id: str,
        *,
        target_temperature: float | None = None,
        target_temperature_low: float | None = None,
        target_temperature_high: float | None = None,
        current: EasyAirThermostat | None = None,
    ) -> None:
        """Set thermostat setpoint values."""
        cool_setpoint = current.target_temperature_high if current else None
        heat_setpoint = current.target_temperature_low if current else None

        if target_temperature is not None:
            # BCC always writes both setpoints, so a lone target temperature is
            # only meaningful when the current mode says which half it is. In
            # auto/off (or with no known state) guessing would silently move the
            # cool setpoint, so refuse instead.
            mode = current.hvac_mode if current else None
            if mode == "cool":
                cool_setpoint = target_temperature
            elif mode == "heat":
                heat_setpoint = target_temperature
            else:
                raise EasyAirInvalidInputError(
                    "A single target temperature is ambiguous in "
                    f"{mode or 'unknown'} mode; set the low and high "
                    "setpoints instead"
                )

        if target_temperature_low is not None:
            heat_setpoint = target_temperature_low
        if target_temperature_high is not None:
            cool_setpoint = target_temperature_high

        if cool_setpoint is None and heat_setpoint is None:
            return
        if cool_setpoint is None or heat_setpoint is None:
            raise EasyAirInvalidInputError(
                "Both BCC cool and heat setpoints are required"
            )

        await self._request(
            "POST",
            "/control/temp",
            json={
                "device_id": device_id,
                "temp": _format_temp_pair(cool_setpoint, heat_setpoint),
                # The captured app always writes a permanent hold, which
                # overrides the thermostat's own schedule until it is cleared
                # from the app. Documented in README.md; releasing the hold
                # needs a control call we have not captured.
                "hold": "1",
                "timestamp": _timestamp_ms(),
            },
        )

    async def async_set_hvac_mode(self, device_id: str, hvac_mode: str) -> str:
        """Set thermostat HVAC mode and return the mode the API applied."""
        mode = HVAC_MODE_TO_BCC[hvac_mode]
        payload = await self._request(
            "POST",
            "/control/change_mode",
            json={
                "device_id": device_id,
                "mode": mode,
                # Every captured mode change sent distr="0"; the field was
                # never observed with another value, so it is replayed as-is.
                "distr": "0",
                "timestamp": _timestamp_ms(),
            },
        )
        if isinstance(payload, Mapping):
            confirmed_mode = BCC_TO_HVAC_MODE.get(
                _as_string(payload.get("mode")) or ""
            )
            if confirmed_mode is not None:
                return confirmed_mode

        # Some successful API responses may be empty. In that case the
        # accepted request remains the best available state until the next
        # regular poll.
        _LOGGER.debug(
            "Mode response for %s did not include a recognized mode", device_id
        )
        return hvac_mode

    async def async_set_fan(self, device_id: str, enabled: bool) -> None:
        """Set thermostat fan circulation."""
        await self._request(
            "POST",
            "/control/fan",
            json={
                "device_id": device_id,
                "fan": "1" if enabled else "0",
                "timestamp": _timestamp_ms(),
            },
        )

    async def async_refresh_access_token(self) -> EasyAirTokens:
        """Refresh the Cognito access token."""
        if not self._refresh_token:
            raise EasyAirAuthError("No EasyAir refresh token is configured")

        payload = await self._request(
            "POST",
            TOKEN_PATH,
            base_url=self._auth_base_url,
            auth=False,
            token_request=True,
            form={
                "grant_type": "refresh_token",
                "client_id": COGNITO_CLIENT_ID,
                "refresh_token": self._refresh_token,
            },
        )
        tokens = _tokens_from_payload(
            payload, fallback_refresh_token=self._refresh_token
        )
        self._access_token = tokens.access_token
        self._refresh_token = tokens.refresh_token
        if self._token_updater:
            await self._token_updater(tokens)
        return tokens

    async def _async_refresh_access_token_locked(self, stale_token: str) -> None:
        """Refresh the access token, coalescing concurrent refreshes.

        A single ``asyncio.Lock`` serializes refreshes so that overlapping
        401/403 responses cannot rotate the Cognito refresh token more than once
        per cycle and invalidate each other. Requests that were waiting on the
        lock skip the refresh once the token has already been rotated.
        """
        async with self._refresh_lock:
            if self._access_token != stale_token:
                # Another request already refreshed the token; reuse it.
                return
            await self.async_refresh_access_token()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        base_url: str | None = None,
        form: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        retry_auth: bool = True,
        token_request: bool = False,
    ) -> Any:
        """Send a request to EasyAir."""
        url = urljoin(base_url or self._api_base_url, _normalize_path(path).lstrip("/"))
        request_token = self._access_token
        headers = self._headers(auth=auth, form=form is not None)

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                data=form,
                json=json,
                params=params,
                timeout=ClientTimeout(total=20),
            ) as response:
                return await self._handle_response(
                    response,
                    method,
                    path,
                    auth=auth,
                    base_url=base_url,
                    form=form,
                    json=json,
                    params=params,
                    retry_auth=retry_auth,
                    token_request=token_request,
                    request_token=request_token,
                )
        except EasyAirError:
            raise
        except asyncio.TimeoutError as err:
            raise EasyAirConnectionError("Timed out connecting to EasyAir") from err
        except ClientError as err:
            raise EasyAirConnectionError(f"Error connecting to EasyAir: {err}") from err

    async def _handle_response(
        self,
        response: ClientResponse,
        method: str,
        path: str,
        *,
        auth: bool,
        base_url: str | None,
        form: Mapping[str, Any] | None,
        json: Mapping[str, Any] | None,
        params: Mapping[str, Any] | None,
        retry_auth: bool,
        token_request: bool,
        request_token: str,
    ) -> Any:
        """Validate and decode an EasyAir response."""
        if token_request and response.status >= 400:
            raise await _token_endpoint_error(response)
        if response.status in (401, 403):
            if auth and retry_auth and self._refresh_token:
                await self._async_refresh_access_token_locked(request_token)
                return await self._request(
                    method,
                    path,
                    auth=auth,
                    base_url=base_url,
                    form=form,
                    json=json,
                    params=params,
                    retry_auth=False,
                    token_request=token_request,
                )
            raise EasyAirAuthError("EasyAir rejected the configured access token")
        if response.status >= 400:
            body = await response.text()
            raise EasyAirConnectionError(
                f"EasyAir request failed with HTTP {response.status}: {body[:200]}"
            )
        if response.status == 204:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type:
            text = await response.text()
            raise EasyAirConnectionError(
                f"EasyAir returned a non-JSON response: {text[:200]}"
            )
        return await response.json()

    def _headers(self, *, auth: bool, form: bool) -> dict[str, str]:
        """Build request headers."""
        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Language-Tag": self._language_tag,
            "User-Agent": EASYAIR_USER_AGENT,
        }
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers


async def _token_endpoint_error(response: ClientResponse) -> EasyAirError:
    """Return the error to raise for a failed Cognito token request.

    Cognito reports a dead grant as HTTP 400 with an RFC 6749 ``error`` body
    rather than 401, so mapping on status alone would send an expired refresh
    token down the "connection failed" path and the integration would retry it
    forever instead of asking the user to sign in again.
    """
    body = await response.text()
    error_code = None
    try:
        payload = json_loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, Mapping):
        error_code = (_as_string(payload.get("error")) or "").lower() or None

    message = "EasyAir token request failed with HTTP {}{}: {}".format(
        response.status,
        f" ({error_code})" if error_code else "",
        body[:200],
    )

    # Rate limiting and server faults are transient; retrying is correct there.
    if response.status == 429 or response.status >= 500:
        return EasyAirConnectionError(message)
    if error_code is None or error_code in OAUTH_AUTH_ERROR_CODES:
        return EasyAirAuthError(message)
    return EasyAirConnectionError(message)


def _unwrap_status(status: Mapping[str, Any], device_id: str) -> Mapping[str, Any]:
    """Return the usable body of a ``/control/status`` payload.

    ``/device/list`` wraps its result in ``{"data": [...]}``. The status
    contract is inferred from captures rather than documented, so accept the
    same envelope here and log loudly when nothing recognizable comes back
    instead of silently falling through to the stale device-list fields.
    """
    if not _STATUS_FIELDS.isdisjoint(status):
        return status

    inner = status.get("data")
    if isinstance(inner, Mapping) and not _STATUS_FIELDS.isdisjoint(inner):
        _LOGGER.debug("Unwrapped enveloped status payload for %s", device_id)
        return inner

    _LOGGER.debug(
        "Status payload for %s contributed no known fields (keys: %s)",
        device_id,
        sorted(status),
    )
    return status


def account_id_from_token(token: str | None) -> str | None:
    """Return the Cognito ``sub`` claim carried by a JWT access or id token.

    The claim is only used to derive a stable, account-scoped unique id for the
    config entry, so the signature is deliberately not verified — no trust
    decision is made on the basis of this value.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    try:
        claims = json_loads(
            base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        )
    except ValueError:
        return None
    if not isinstance(claims, Mapping):
        return None
    return _as_string(claims.get("sub"))


def _parse_thermostat(raw: Mapping[str, Any]) -> EasyAirThermostat:
    """Normalize a BCC thermostat object."""
    device_id = _as_string(raw.get("device_id")) or _as_string(raw.get("gatewayId"))
    if not device_id:
        raise EasyAirError("BCC thermostat payload is missing gatewayId/device_id")

    cool_setpoint, heat_setpoint = _split_temp_pair(raw.get("temp"))
    current_mode = BCC_TO_HVAC_MODE.get(_as_string(raw.get("mode")) or "")
    target_temperature = cool_setpoint
    if current_mode == "heat":
        target_temperature = heat_setpoint
    elif current_mode == "off":
        target_temperature = None

    return EasyAirThermostat(
        id=device_id,
        name=_as_string(raw.get("memo")) or "Bosch EasyAir",
        model=_as_string(raw.get("model")) or _as_string(raw.get("deviceType")),
        serial_number=device_id,
        firmware_version=_as_string(raw.get("firmware")),
        wifi_firmware_version=_as_string(raw.get("wifiFirmware")),
        current_temperature=_first_float(raw.get("room_temp"), raw.get("roomTemp")),
        target_temperature=target_temperature,
        target_temperature_low=heat_setpoint,
        target_temperature_high=cool_setpoint,
        # temp_low/temp_high are not mapped to min/max: the same payload sends
        # setpoints in `temp`, so those fields are at least as likely to be the
        # auto-mode deadband as device capability limits. Clamping the HA
        # slider to the current setpoints would be worse than falling back to
        # ClimateEntity's defaults, so leave them unset until the captures
        # confirm otherwise.
        min_temperature=None,
        max_temperature=None,
        humidity=_as_int(raw.get("humidity")),
        temperature_unit=_temperature_unit(raw),
        hvac_mode=current_mode,
        hvac_action=_hvac_action(raw, current_mode),
        available_modes=["off", "cool", "heat", "auto"],
        raw=raw,
    )


def _tokens_from_payload(
    payload: Any, *, fallback_refresh_token: str | None = None
) -> EasyAirTokens:
    """Return OAuth tokens from a Cognito token response."""
    if not isinstance(payload, Mapping) or "access_token" not in payload:
        raise EasyAirAuthError("EasyAir token response did not return an access token")

    return EasyAirTokens(
        access_token=str(payload["access_token"]),
        refresh_token=_as_string(payload.get("refresh_token")) or fallback_refresh_token,
        expires_in=_as_int(payload.get("expires_in")),
    )


def _split_temp_pair(value: Any) -> tuple[float | None, float | None]:
    """Return BCC cool and heat setpoints from a temp pair.

    Captured BCC110 traffic sends setpoints as ``"<cool>-<heat>"``. Either half
    can be negative on a Celsius unit, so the pair is matched with a regex
    anchored on both numbers rather than split on the first ``-``. Round-trips
    with :func:`_format_temp_pair`.
    """
    text = _as_string(value)
    if text is None:
        return None, None
    if (match := _TEMP_PAIR_RE.match(text)) is None:
        _LOGGER.warning("Unable to parse BCC temp pair %r", text)
        return None, None
    return float(match["cool"]), float(match["heat"])


def _format_temp_pair(cool_setpoint: float, heat_setpoint: float) -> str:
    """Format BCC cool and heat setpoints.

    The EasyAir app writes both values with exactly one decimal place, including
    whole Fahrenheit degrees (for example, ``"75.0-65.0"``). The API validates
    that wire format and rejects integer-looking pairs such as ``"75-65"``.
    """
    return f"{_format_temp(cool_setpoint)}-{_format_temp(heat_setpoint)}"


def _format_temp(setpoint: float) -> str:
    """Format a single BCC setpoint with the app's one decimal place."""
    return f"{setpoint:.1f}"


def _temperature_unit(raw: Mapping[str, Any]) -> UnitOfTemperature:
    """Normalize a temperature unit."""
    unit = _as_string(raw.get("temp_unit") or raw.get("tempUnit"))
    if unit and unit.upper() in ("F", "FAHRENHEIT", "°F"):
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS


def _hvac_action(raw: Mapping[str, Any], hvac_mode: str | None) -> str | None:
    """Infer HVAC action from BCC status fields.

    Returns ``None`` when the equipment is demonstrably running but the mode
    cannot say *what* is running -- ``auto`` (the normal HEAT_COOL state for a
    BCC110) or an unrecognized mode. No captured field distinguishes a heat
    call from a cool call in auto, and reporting ``idle`` there would contradict
    the ``power``/``stage`` evidence this function just checked, so an unknown
    action is reported instead. ``fanstatus`` is still trusted as a positive
    signal on the way past, since it is the one field that names what is
    running; if a capture ever shows it set during a heat/cool call it should
    move below this fallback rather than above it.
    """
    if hvac_mode == "off":
        return "off"
    power = _as_string(raw.get("power"))
    stage = _as_string(raw.get("stage"))
    if power in (None, "0") and stage in (None, "0"):
        return "idle"
    if hvac_mode == "heat":
        return "heating"
    if hvac_mode == "cool":
        return "cooling"
    fan_status = _as_string(raw.get("fanstatus"))
    if fan_status == "1":
        return "fan"
    return None


def _as_string(value: Any) -> str | None:
    """Return a stripped string value."""
    if value is None:
        return None
    return str(value).strip() or None


def _as_float(value: Any) -> float | None:
    """Return a numeric value when possible."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    """Return the first value that can be parsed as a float."""
    for value in values:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _as_int(value: Any) -> int | None:
    """Return an integer value when possible."""
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_path(path: str) -> str:
    """Return an absolute API path."""
    return path if path.startswith("/") else f"/{path}"


def _timestamp_ms() -> str:
    """Return a millisecond timestamp string like the EasyAir app sends."""
    return str(int(time.time() * 1000))
