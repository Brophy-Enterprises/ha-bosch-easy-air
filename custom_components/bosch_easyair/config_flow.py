"""Config flow for Bosch EasyAir."""
from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import logging
import secrets
from typing import Any, NamedTuple
from urllib.parse import parse_qs, quote, urlencode, urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    EasyAirAuthError,
    EasyAirClient,
    EasyAirError,
    EasyAirTokens,
    account_id_from_token,
)
from .const import (
    COGNITO_CLIENT_ID,
    COGNITO_IDENTITY_PROVIDER,
    COGNITO_OAUTH_REDIRECT_URI,
    COGNITO_OAUTH_SCOPE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    EASYAIR_AUTH_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

CONF_AUTHORIZATION_RESPONSE = "authorization_response"
CONF_AUTHORIZATION_URL = "authorization_url"

ACCOUNT_ID_PREFIX = "account:"
DEVICES_ID_PREFIX = "devices:"


class NoThermostatsFound(Exception):
    """Raised when credentials are valid but the account has no thermostats.

    Dedicated types rather than a bare ``ValueError``: the flow's handlers wrap
    the whole API/parse stack, so mapping ``ValueError`` to a user-facing error
    string would also catch an incidental conversion or JSON-decode failure
    from deeper down and misreport it.
    """


class InvalidAuthorizationResponse(Exception):
    """Raised when a pasted SingleKey redirect URL cannot be used."""


class AccountIdentity(NamedTuple):
    """Identity of the EasyAir account behind a set of credentials."""

    unique_id: str
    legacy_ids: frozenset[str]
    """Unique ids a pre-existing entry for this same account may still carry."""
    device_ids: frozenset[str]
    """Every thermostat id currently visible on this account."""

    def matches(self, existing_id: str | None) -> bool:
        """Return whether an existing entry's unique id is this same account.

        The id an entry was created with is not necessarily the one this
        account mints today: earlier builds keyed on a single device id, and
        the ``devices:`` fallback below is only as stable as the device set
        behind it. Every form we have ever produced has to keep resolving to
        the account, otherwise reauth aborts with ``wrong_account`` against the
        user's own account and the only recovery is deleting the entry.
        """
        if existing_id is None:
            return False
        if existing_id == self.unique_id or existing_id in self.legacy_ids:
            return True
        if existing_id.startswith(DEVICES_ID_PREFIX):
            # Compare membership rather than the whole string: a ``devices:``
            # id goes stale the moment a thermostat is added or removed, but
            # sharing one still means it is the same account.
            previous = existing_id.removeprefix(DEVICES_ID_PREFIX).split(",")
            return bool(self.device_ids.intersection(previous))
        return False


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> AccountIdentity:
    """Validate user input and return the account identity."""

    async def async_update_tokens(tokens: EasyAirTokens) -> None:
        """Keep refreshed setup tokens for the created config entry."""
        data[CONF_ACCESS_TOKEN] = tokens.access_token
        if tokens.refresh_token:
            data[CONF_REFRESH_TOKEN] = tokens.refresh_token

    client = EasyAirClient(
        session=async_get_clientsession(hass),
        access_token=data[CONF_ACCESS_TOKEN],
        refresh_token=data.get(CONF_REFRESH_TOKEN),
        token_updater=async_update_tokens,
    )
    thermostats = await client.async_get_thermostats()
    if not thermostats:
        raise NoThermostatsFound("No EasyAir thermostats found")

    device_ids = sorted({thermostat.id for thermostat in thermostats})
    devices_id = f"{DEVICES_ID_PREFIX}{','.join(device_ids)}"
    # Earlier builds keyed the entry on thermostats[0], so any device id in the
    # account is a legitimate unique id for an existing entry -- as is the
    # composite id minted by the fallback below.
    legacy_ids = frozenset({*device_ids, devices_id})

    # Prefer the Cognito subject: it identifies the account rather than
    # whichever device /device/list happened to return first, so it is stable
    # across reorderings and across thermostats being added or removed.
    if account_id := account_id_from_token(data.get(CONF_ACCESS_TOKEN)):
        return AccountIdentity(
            f"{ACCOUNT_ID_PREFIX}{account_id}", legacy_ids, frozenset(device_ids)
        )
    return AccountIdentity(devices_id, legacy_ids, frozenset(device_ids))


async def _exchange_authorization_response(
    hass: HomeAssistant, authorization_response: str, state: str, code_verifier: str
) -> dict[str, Any]:
    """Exchange an OAuth authorization response for setup tokens."""
    code = _authorization_code_from_response(authorization_response, state)
    client = EasyAirClient(
        session=async_get_clientsession(hass),
        access_token="",
    )
    tokens = await client.async_exchange_authorization_code(code, code_verifier)
    data = {
        CONF_ACCESS_TOKEN: tokens.access_token,
    }
    if tokens.refresh_token:
        data[CONF_REFRESH_TOKEN] = tokens.refresh_token
    return data


class BoschEasyAirConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Bosch EasyAir config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    _oauth_authorization_url: str | None = None
    _oauth_code_verifier: str | None = None
    _oauth_state: str | None = None
    _reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Let the user choose the setup method."""
        return self.async_show_menu(
            step_id="user",
            # A list lets HA resolve the labels from strings.json/translations;
            # the dict form would use the values as literal English labels and
            # leave the menu_options blocks in strings.json unused.
            menu_options=["browser_login", "manual"],
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Handle re-authentication when EasyAir rejects the stored token."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            # Without the entry, _async_create_validated_entry would take the
            # non-reauth branch and create a *second* entry for this account
            # instead of updating the one that needs new tokens.
            return self.async_abort(reason="reauth_entry_missing")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Let the user choose how to re-authenticate."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            # A list lets HA resolve the labels from strings.json/translations;
            # the dict form would use the values as literal English labels and
            # leave the menu_options blocks in strings.json unused.
            menu_options=["browser_login", "manual"],
        )

    async def async_step_browser_login(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle browser-based OAuth setup."""
        errors: dict[str, str] = {}
        self._ensure_oauth_request()

        if user_input is not None:
            data: dict[str, Any] | None = None
            if authorization_response := _clean_input(
                user_input.get(CONF_AUTHORIZATION_RESPONSE)
            ) or _authorization_response_from_url_field(
                user_input.get(CONF_AUTHORIZATION_URL)
            ):
                try:
                    data = await _exchange_authorization_response(
                        self.hass,
                        authorization_response,
                        self._oauth_state or "",
                        self._oauth_code_verifier or "",
                    )
                except InvalidAuthorizationResponse:
                    errors["base"] = "invalid_authorization_response"
                except EasyAirAuthError:
                    self._reset_oauth_request()
                    errors["base"] = "invalid_auth"
                except EasyAirError as err:
                    _LOGGER.debug("Unable to complete EasyAir login: %s", err)
                    errors["base"] = "cannot_connect"
            else:
                errors["base"] = "missing_authorization_response"

            if data is None:
                return self._show_browser_login_form(errors)

            if result := await self._async_create_validated_entry(data, errors):
                return result

            self._reset_oauth_request()

        return self._show_browser_login_form(errors)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Handle manual token setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data: dict[str, Any] | None = None
            if access_token := _clean_input(user_input.get(CONF_ACCESS_TOKEN)):
                data = {
                    CONF_ACCESS_TOKEN: access_token,
                }
                if refresh_token := _clean_input(user_input.get(CONF_REFRESH_TOKEN)):
                    data[CONF_REFRESH_TOKEN] = refresh_token
            else:
                errors["base"] = "missing_auth"

            if data is not None:
                if result := await self._async_create_validated_entry(data, errors):
                    return result

        return self._show_manual_form(errors)

    async def _async_create_validated_entry(
        self, data: dict[str, Any], errors: dict[str, str]
    ):
        """Validate setup data and create the config entry."""
        try:
            identity = await _validate_input(self.hass, data)
        except EasyAirAuthError:
            errors["base"] = "invalid_auth"
        except NoThermostatsFound:
            errors["base"] = "no_devices"
        except EasyAirError as err:
            _LOGGER.debug("Unable to connect to EasyAir: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected EasyAir setup error")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(identity.unique_id)
            if self._reauth_entry is not None:
                existing_id = self._reauth_entry.unique_id
                if existing_id is not None and not identity.matches(existing_id):
                    return self.async_abort(reason="wrong_account")
                # Also migrates an entry still keyed on a single device id, or
                # on a ``devices:`` set that no longer matches the account.
                return self.async_update_reload_and_abort(
                    self._reauth_entry, data=data, unique_id=identity.unique_id
                )
            self._abort_if_unique_id_configured()
            self._abort_if_legacy_entry_configured(identity)
            return self.async_create_entry(title="Bosch EasyAir", data=data)
        return None

    def _abort_if_legacy_entry_configured(self, identity: AccountIdentity) -> None:
        """Abort when an entry minted under an older id scheme covers this account.

        Those entries are keyed on a single device id, or on a ``devices:`` set
        that has since changed, so ``_abort_if_unique_id_configured`` cannot
        see them -- without this a second entry would be created and its
        entities would collide on the first entry's unique ids.
        """
        for entry in self._async_current_entries():
            if identity.matches(entry.unique_id):
                raise AbortFlow("already_configured")

    def _ensure_oauth_request(self) -> None:
        """Create a reusable OAuth authorization request for this flow."""
        if self._oauth_authorization_url:
            return
        self._oauth_code_verifier = _new_code_verifier()
        self._oauth_state = secrets.token_urlsafe(24)
        self._oauth_authorization_url = _build_authorization_url(
            state=self._oauth_state,
            code_challenge=_code_challenge(self._oauth_code_verifier),
        )

    def _reset_oauth_request(self) -> None:
        """Discard a failed OAuth request so the next form gets a fresh code."""
        self._oauth_authorization_url = None
        self._oauth_code_verifier = None
        self._oauth_state = None
        self._ensure_oauth_request()

    def _show_browser_login_form(self, errors: dict[str, str]):
        """Show the browser login setup form."""
        return self.async_show_form(
            step_id="browser_login",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AUTHORIZATION_URL,
                        default=self._oauth_authorization_url or "",
                    ): TextSelector(TextSelectorConfig()),
                    vol.Optional(CONF_AUTHORIZATION_RESPONSE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "authorization_url": self._oauth_authorization_url or "",
            },
        )

    def _show_manual_form(self, errors: dict[str, str]):
        """Show the manual token setup form."""
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_REFRESH_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )


def _build_authorization_url(*, state: str, code_challenge: str) -> str:
    """Build the captured Cognito authorization URL."""
    query = urlencode(
        {
            "redirect_uri": COGNITO_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "client_id": COGNITO_CLIENT_ID,
            "identity_provider": COGNITO_IDENTITY_PROVIDER,
            "scope": COGNITO_OAUTH_SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
        quote_via=quote,
    )
    return f"{EASYAIR_AUTH_BASE_URL}/oauth2/authorize?{query}"


def _authorization_code_from_response(
    authorization_response: str, expected_state: str
) -> str:
    """Extract and validate an authorization code or redirect URL."""
    parsed = urlparse(authorization_response)
    query = parse_qs(parsed.query)
    if code := _first_query_value(query, "code"):
        _verify_oauth_state(query, expected_state)
        return code

    if "code=" in authorization_response:
        query = parse_qs(authorization_response.split("?", 1)[-1])
        if code := _first_query_value(query, "code"):
            _verify_oauth_state(query, expected_state)
            return code

    code = _clean_input(authorization_response)
    if not code:
        raise InvalidAuthorizationResponse("Missing authorization code")
    return code


def _verify_oauth_state(query: dict[str, list[str]], expected_state: str) -> None:
    """Enforce the CSRF ``state`` value for a pasted redirect URL.

    When we issued an authorization request with a ``state`` we require the
    redirect to echo it back and match; a missing or mismatched ``state`` is
    rejected. Bare ``code`` paste-back (no query string) has no state to check
    and is handled by the caller.
    """
    if not expected_state:
        return
    state = _first_query_value(query, "state")
    if state != expected_state:
        raise InvalidAuthorizationResponse("OAuth state mismatch")


def _authorization_response_from_url_field(value: Any) -> str | None:
    """Accept a redirect URL if the user pastes it into the login URL field."""
    cleaned = _clean_input(value)
    if cleaned and "code=" in cleaned:
        return cleaned
    return None


def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first non-empty query value."""
    if not (values := query.get(key)):
        return None
    return _clean_input(values[0])


def _new_code_verifier() -> str:
    """Return a PKCE code verifier."""
    return secrets.token_urlsafe(96)[:128]


def _code_challenge(code_verifier: str) -> str:
    """Return the S256 PKCE code challenge for a verifier."""
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _clean_input(value: Any) -> str | None:
    """Normalize optional user input."""
    if value is None:
        return None
    return str(value).strip() or None
