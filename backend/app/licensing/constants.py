"""Hard-coded licensing constants (spec §17: record the spec version; §2 rule 4: HTTPS only)."""

LICENSE_SPEC = "1.7"
HUB_ORIGIN = "https://license.21cel.com"
PRODUCT_ID = "invoiceflow"
DEFAULT_KEY_PREFIX = "IVF"
TOKEN_TYP = "21cel-license+jws"  # noqa: S105 - a media type, not a secret
REVOCATION_TYP = "21cel-revocations+jws"
SIGNING_ALG = "EdDSA"
TOKEN_CLAIM_V = 1
PLATFORM_CODE_SERVER = "08"
INSTANCE_PLATFORM = "server"
DAY = 86400
CLIENT_TIMEOUT_SECONDS = 10.0
MIN_VALIDATE_INTERVAL = 15 * 60  # spec §12: never call /validate more than once per 15 minutes per instance
MAX_TOKEN_BYTES = 16 * 1024  # spec §6: 16 KB bodies; anything larger is not a token
BACKOFF_SCHEDULE = (60, 300, 1800, 21600)  # spec §12: 1 min, 5 min, 30 min, then every 6 h
STATE_CACHE_SECONDS = 5 * 60  # recompute from the stored token at least every 5 minutes
REVOCATION_FETCH_INTERVAL = 7 * DAY  # spec §10: weekly
BLOCKED_RETRY_INTERVAL = DAY  # spec §6.4 instance_blocked: retry activation at most once a day
CORE_TOOL = "core"
