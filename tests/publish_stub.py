"""Canned GitHub issue-create endpoint for publish tests and fixtures."""

from ai_incident_investigator.collect.http import EnvBearerAuth, _resolve_token
from ai_incident_investigator.publish import IssueCreated, IssueCreateRequest


class GitHubIssueStub:
    def __init__(self) -> None:
        self.calls: list[tuple[IssueCreateRequest, EnvBearerAuth | None]] = []

    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated:
        self.calls.append((request, auth))
        return IssueCreated(number=101, html_url=f"https://github.local/{request.repo}/issues/101")


class AuthResolvingIssueStub(GitHubIssueStub):
    """Materializes the token exactly like the live client would - the
    control in credential-scrubbing tests."""

    def __init__(self) -> None:
        super().__init__()
        self.resolved: list[str] = []

    def create_issue(
        self, request: IssueCreateRequest, auth: EnvBearerAuth | None = None
    ) -> IssueCreated:
        if auth is not None:
            self.resolved.append(f"{auth.scheme} {_resolve_token(auth)}".strip())
        return super().create_issue(request, auth)
