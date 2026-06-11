#!/usr/bin/env bash
# Merge-readiness sweep for a PR: status checks, top-level reviews/comments,
# and inline review comments, in one command (see github-shipping skill).
set -euo pipefail

pr="${1:?usage: pr_ready_check.sh <pr-number>}"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

echo "== PR #${pr} status =="
gh pr view "$pr" --json state,mergeStateStatus,reviewDecision \
  --template 'state: {{.state}}  merge: {{.mergeStateStatus}}  review decision: {{or .reviewDecision "none"}}
'

echo
echo "== Status checks =="
gh pr view "$pr" --json statusCheckRollup --jq '
  .statusCheckRollup[]? |
  "\(.name // .context): \(
    if (.conclusion // "") != "" then .conclusion
    elif (.status // "") != "" then .status
    else (.state // "UNKNOWN") end)"'

echo
echo "== Top-level reviews =="
gh pr view "$pr" --json reviews --jq '
  .reviews[]? | "[\(.state)] \(.author.login): \(.body | split("\n")[0])"'

echo
echo "== PR comments =="
gh pr view "$pr" --json comments --jq '
  .comments[]? | "\(.author.login): \(.body | split("\n")[0])"'

echo
echo "== Inline review comments =="
gh api "repos/${repo}/pulls/${pr}/comments" --jq '
  .[] | "\(.path):\(.line // .original_line // "?") \(.user.login): \(.body | split("\n")[0])"'

echo
verdict="$(gh pr view "$pr" --json statusCheckRollup --jq '
  [.statusCheckRollup[]? |
   if (.conclusion // "") != "" then .conclusion
   elif (.status // "") != "" and .status != "COMPLETED" then "PENDING"
   else (.state // "UNKNOWN") end] |
  "\([.[] | select(test("FAILURE|ERROR|TIMED_OUT|CANCELLED|UNKNOWN"))] | length) \([.[] | select(test("PENDING|QUEUED|IN_PROGRESS|EXPECTED|WAITING"))] | length)"')"
failing="${verdict%% *}"
pending="${verdict##* }"
decision="$(gh pr view "$pr" --json reviewDecision --jq '.reviewDecision // ""')"

if [[ "$failing" != "0" ]]; then
  echo "NOT READY: ${failing} failing check(s)."
  exit 1
fi
if [[ "$pending" != "0" ]]; then
  echo "NOT READY: ${pending} check(s) still pending."
  exit 1
fi
if [[ "$decision" == "CHANGES_REQUESTED" ]]; then
  echo "NOT READY: changes requested."
  exit 1
fi
echo "READY: checks green, no changes requested. Review the comment sections above before merging."
