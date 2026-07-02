"""GitHub deploy/release adapter: read-only REST subset into deploys.json.

Endpoints: GET /repos/{repo}/releases and GET /repos/{repo}/deployments,
per configured repo. Changes are collected over the *change lookback*
(wider than the incident window - an old change is ruling-out evidence),
and an empty result is written as an empty deploys.json: "we checked,
nothing shipped" is evidence, not missing data.

Mapping rules: docs/collection_sources.md. GitHub has no feature-flag
concept, so change_type is always "deploy".
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ai_incident_investigator.collect.adapter import CollectionContext, PackageContribution
from ai_incident_investigator.collect.config import CollectError, SourcesConfig
from ai_incident_investigator.collect.http import (
    EnvBearerAuth,
    HTTPClient,
    HTTPClientError,
    HTTPRequest,
    raise_for_status,
)
from ai_incident_investigator.models.package import Deploy, DeploysFile

SECTION = "github"

_DATETIME = TypeAdapter(datetime)


class GitHubRepo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(description="owner/name")
    service: str
    environment: str | None = Field(
        default=None, description="deployments filter; releases are not filtered"
    )


class GitHubConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "https://api.github.com"
    token_env: str | None = None
    post_minutes: int = 30
    per_page: int = 50
    repos: list[GitHubRepo] = Field(min_length=1)


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GitHubRelease(_WireModel):
    tag_name: str
    name: str | None = None
    published_at: str | None = None  # null for drafts
    draft: bool = False


class GitHubDeployment(_WireModel):
    id: int
    ref: str | None = None
    sha: str | None = None
    environment: str | None = None
    created_at: str
    description: str | None = None


def _parse_time(value: str) -> datetime:
    parsed = _DATETIME.validate_python(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class GitHubDeploysAdapter:
    def __init__(self, http: HTTPClient, config: GitHubConfig) -> None:
        self._http = http
        self._config = config
        self._auth = (
            EnvBearerAuth(env_var=config.token_env) if config.token_env is not None else None
        )

    @property
    def name(self) -> str:
        return "github"

    def _get_list(self, url: str, params: dict[str, str]) -> str:
        request = HTTPRequest(url=url, params=params)
        return raise_for_status(request, self._http.get(request, self._auth)).body

    def _releases(
        self, repo: GitHubRepo, start: datetime, end: datetime, notes: list[str]
    ) -> list[Deploy]:
        base = self._config.base_url.rstrip("/")
        body = self._get_list(
            f"{base}/repos/{repo.repo}/releases",
            {"per_page": str(self._config.per_page)},
        )
        adapter = TypeAdapter(list[GitHubRelease])
        releases = adapter.validate_json(body)
        deploys: list[Deploy] = []
        skipped_drafts = 0
        for release in releases:
            if release.draft or not release.published_at:
                skipped_drafts += 1
                continue
            published = _parse_time(release.published_at)
            if not (start <= published <= end):
                continue
            deploys.append(
                Deploy(
                    id=f"release_{repo.service}_{release.tag_name}",
                    service=repo.service,
                    version=release.tag_name,
                    deployed_at=published,
                    change_type="deploy",
                    description=release.name,
                )
            )
        if skipped_drafts:
            notes.append(f"{repo.repo}: {skipped_drafts} draft/unpublished release(s) skipped")
        return deploys

    def _deployments(self, repo: GitHubRepo, start: datetime, end: datetime) -> list[Deploy]:
        base = self._config.base_url.rstrip("/")
        params = {"per_page": str(self._config.per_page)}
        if repo.environment:
            params["environment"] = repo.environment
        body = self._get_list(f"{base}/repos/{repo.repo}/deployments", params)
        adapter = TypeAdapter(list[GitHubDeployment])
        deployments = adapter.validate_json(body)
        deploys: list[Deploy] = []
        for deployment in deployments:
            created = _parse_time(deployment.created_at)
            if not (start <= created <= end):
                continue
            version = deployment.ref or (deployment.sha or "")[:12] or f"id-{deployment.id}"
            environment = f"deployment to {deployment.environment or 'unknown environment'}"
            description = (
                f"{environment}: {deployment.description}"
                if deployment.description
                else environment
            )
            deploys.append(
                Deploy(
                    id=f"deployment_{repo.service}_{deployment.id}",
                    service=repo.service,
                    version=version,
                    deployed_at=created,
                    change_type="deploy",
                    description=description,
                )
            )
        return deploys

    def collect(self, context: CollectionContext) -> PackageContribution:
        start = context.anchor_time - context.change_lookback
        end = context.anchor_time + timedelta(minutes=self._config.post_minutes)
        notes: list[str] = []
        deploys: list[Deploy] = []
        failed_repos = 0
        for repo in self._config.repos:
            try:
                deploys.extend(self._releases(repo, start, end, notes))
                deploys.extend(self._deployments(repo, start, end))
            except (HTTPClientError, ValidationError) as exc:
                failed_repos += 1
                notes.append(f"{repo.repo} skipped: {exc}")
        if failed_repos == len(self._config.repos):
            raise CollectError("no repo could be collected: " + "; ".join(notes))
        if not deploys:
            notes.append(
                "no releases or deployments in the change window across "
                f"{len(self._config.repos)} repo(s) - recorded as an empty deploys.json"
            )
        deploys.sort(key=lambda d: (d.deployed_at, d.id))
        return PackageContribution(deploys=DeploysFile(deploys=deploys), notes=notes)


def github_adapter(config: SourcesConfig, http: HTTPClient) -> GitHubDeploysAdapter:
    """Build the adapter from a sources.toml [github] section."""
    try:
        section = GitHubConfig.model_validate(config.section(SECTION))
    except ValidationError as exc:
        raise CollectError(f"[{SECTION}] section is invalid: {exc}") from exc
    return GitHubDeploysAdapter(http, section)
