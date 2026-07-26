"""Constants for the Bosch EasyAir integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import CONF_ACCESS_TOKEN, Platform

DOMAIN = "bosch_easyair"

CONF_REFRESH_TOKEN = "refresh_token"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
DEFAULT_LANGUAGE_TAG = "en"
EASYAIR_AUTH_BASE_URL = "https://auth.smart-climate-ids.com"
BCC_API_BASE_URL = "https://bccapi.smart-climate-ids.com"
COGNITO_CLIENT_ID = "7q2puec5cov7ls93041mb38p6v"
COGNITO_IDENTITY_PROVIDER = "IDS-TTNA"
COGNITO_OAUTH_REDIRECT_URI = "idsmobileapp://"
COGNITO_OAUTH_SCOPE = "phone email openid profile aws.cognito.signin.user.admin"

# The BCC API rejects requests that do not look like the EasyAir mobile app, so
# this value is deliberately spoofed and must not be "cleaned up" to something
# identifying this integration.
EASYAIR_USER_AGENT = "IDSMobileApp/13 CFNetwork/3860.600.12 Darwin/25.5.0"

MANUFACTURER = "Bosch"
MODEL_BCC110 = "BCC110"

PLATFORMS = [Platform.CLIMATE]

__all__ = [
    "CONF_ACCESS_TOKEN",
    "CONF_REFRESH_TOKEN",
    "BCC_API_BASE_URL",
    "COGNITO_CLIENT_ID",
    "COGNITO_IDENTITY_PROVIDER",
    "COGNITO_OAUTH_REDIRECT_URI",
    "COGNITO_OAUTH_SCOPE",
    "DEFAULT_LANGUAGE_TAG",
    "DEFAULT_SCAN_INTERVAL",
    "DOMAIN",
    "EASYAIR_AUTH_BASE_URL",
    "EASYAIR_USER_AGENT",
    "MANUFACTURER",
    "MODEL_BCC110",
    "PLATFORMS",
]
