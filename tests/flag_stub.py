"""Canned flag-toggle endpoint for executor tests and fixtures."""

from ai_incident_investigator.collect.http import EnvBearerAuth, _resolve_token
from ai_incident_investigator.flags import FlagToggled
from ai_incident_investigator.models.execution import FlagToggleRequest


class FlagToggleStub:
    def __init__(self) -> None:
        self.calls: list[tuple[FlagToggleRequest, EnvBearerAuth | None]] = []

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        self.calls.append((request, auth))
        return FlagToggled(key=request.flag_key, on=request.on)


class AuthResolvingFlagStub(FlagToggleStub):
    """Materializes the token exactly like the live client would - the
    control in credential-scrubbing tests."""

    def __init__(self) -> None:
        super().__init__()
        self.resolved: list[str] = []

    def toggle(self, request: FlagToggleRequest, auth: EnvBearerAuth | None = None) -> FlagToggled:
        if auth is not None:
            self.resolved.append(f"{auth.scheme} {_resolve_token(auth)}".strip())
        return super().toggle(request, auth)
