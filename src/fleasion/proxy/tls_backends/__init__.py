"""Optional TLS transport contracts and inactive provider registry."""

from .android_community import ANDROID_COMMUNITY_CLIENTS as ANDROID_COMMUNITY_CLIENTS
from .contracts import (
    AuthenticatedSession as AuthenticatedSession,
    BackendId as BackendId,
    BackendProbe as BackendProbe,
    BackendStatus as BackendStatus,
    BackendUnavailableError as BackendUnavailableError,
    SessionBackend as SessionBackend,
    dispatch_authenticated_session as dispatch_authenticated_session,
    validate_session as validate_session,
)
from .registry import backend_probes as backend_probes, create_backend as create_backend
