"""Shared Mobile Pass application identity, not personal pass authorization.

Verified by local extraction from com.brivo.pass 4.33.2
(version code 1114), Constants' verified US / RELEASE_PROD branches.
These constants ship in the Android app; they do not grant door access alone.
Never put invitations, refresh tokens, or pass credentials in this module.
Existing config entries retain their saved application identity on upgrades.
"""

APP_CREDENTIALS_PROFILE = "mobile-pass-4.33.2-us-production"
SOURCE_APK_SHA256 = "ca66d673c605793e098921d03a1bd9b17bd738a37dc0b90177ca807be1b2d0e7"

DEFAULT_APPLICATION = {
    "client_id": "d510da70-b688-45c6-aade-db2127e05b45",
    "client_secret": "3twsg4LzZ82msy507CAWqqOsEYgzwZWe",
}
