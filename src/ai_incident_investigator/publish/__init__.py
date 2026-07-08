"""Report publishing: the single write path (docs/product.md Safety Model).

This package can create ONE thing: a GitHub issue carrying the tool's own
investigation report. The narrowing is structural - `IssueCreateRequest`
has no URL field (the endpoint is derived from a validated repo name) and
its method is Literal["POST"] to one route. Collection stays GET-only by
type; nothing in collect/ can write, and nothing here can read.
"""

from ai_incident_investigator.publish.github_issue import (
    DEFAULT_TOKEN_ENV,
    IssueCreated,
    IssueCreateRequest,
    LivePublishClient,
    PublishClient,
    PublishError,
    RecordingPublishClient,
    ReplayPublishClient,
    render_issue,
)

__all__ = [
    "DEFAULT_TOKEN_ENV",
    "IssueCreateRequest",
    "IssueCreated",
    "LivePublishClient",
    "PublishClient",
    "PublishError",
    "RecordingPublishClient",
    "ReplayPublishClient",
    "render_issue",
]
