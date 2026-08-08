#!/usr/bin/env python3

"""Check GitHub release-asset download counts and notify Discord of increases."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


GITHUB_API_URL = "https://api.github.com"
STATE_FILE = Path(".github/release-download-counts.json")


def api_request(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    """Send an HTTP request and return the decoded JSON response."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-release-download-monitor",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"

    body = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read()

            if not response_body:
                return None

            return json.loads(response_body.decode("utf-8"))

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} while requesting {url}: {error_body}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(f"Unable to reach {url}: {exc.reason}") from exc


def load_previous_counts() -> dict[str, int]:
    """Load the previously recorded download counts."""

    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read {STATE_FILE}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"{STATE_FILE} does not contain a JSON object.")

    return {
        str(key): int(value)
        for key, value in data.items()
        if isinstance(value, int)
    }


def get_releases(repository: str, token: str) -> list[dict[str, Any]]:
    """Retrieve every release for the repository, following pagination."""

    releases: list[dict[str, Any]] = []
    page = 1

    while True:
        url = (
            f"{GITHUB_API_URL}/repos/{repository}/releases"
            f"?per_page=100&page={page}"
        )

        result = api_request(url, token=token)

        if not isinstance(result, list):
            raise RuntimeError("GitHub returned an unexpected releases response.")

        releases.extend(result)

        if len(result) < 100:
            break

        page += 1

    return releases


def collect_counts(
    releases: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    """Collect current counts and descriptive information for each asset."""

    counts: dict[str, int] = {}
    details: dict[str, dict[str, str]] = {}

    for release in releases:
        release_id = str(release.get("id", ""))
        release_name = (
            release.get("name")
            or release.get("tag_name")
            or f"Release {release_id}"
        )
        release_url = release.get("html_url", "")

        for asset in release.get("assets", []):
            asset_id = str(asset.get("id", ""))

            if not asset_id:
                continue

            counts[asset_id] = int(asset.get("download_count", 0))

            details[asset_id] = {
                "asset_name": str(asset.get("name", "Unnamed asset")),
                "release_name": str(release_name),
                "release_url": str(release_url),
            }

    return counts, details


def build_notification(
    repository: str,
    previous_counts: dict[str, int],
    current_counts: dict[str, int],
    details: dict[str, dict[str, str]],
) -> str | None:
    """Create a Discord message describing download-count increases."""

    changes: list[tuple[str, int, int]] = []

    for asset_id, current_count in current_counts.items():
        if asset_id not in previous_counts:
            continue

        previous_count = previous_counts[asset_id]
        increase = current_count - previous_count

        if increase > 0:
            changes.append((asset_id, increase, current_count))

    if not changes:
        return None

    changes.sort(
        key=lambda item: (
            details[item[0]]["release_name"],
            details[item[0]]["asset_name"],
        )
    )

    total_increase = sum(change[1] for change in changes)

    lines = [
        "📥 **New GitHub release downloads**",
        f"**Repository:** `{repository}`",
        f"**New downloads detected:** {total_increase}",
        "",
    ]

    for asset_id, increase, current_total in changes:
        asset = details[asset_id]

        lines.append(
            f"• **{asset['asset_name']}** — "
            f"+{increase} download{'s' if increase != 1 else ''} "
            f"({current_total} total)"
        )
        lines.append(
            f"  Release: [{asset['release_name']}]({asset['release_url']})"
        )

    message = "\n".join(lines)

    # Discord messages have a finite size. Keep comfortably below the limit.
    if len(message) > 1_900:
        message = message[:1_850] + "\n\n_Message shortened because many assets changed._"

    return message


def send_discord_notification(webhook_url: str, message: str) -> None:
    """Post a message to Discord."""

    payload = {
        "content": message,
        "allowed_mentions": {"parse": []},
    }

    api_request(
        webhook_url,
        method="POST",
        payload=payload,
    )


def save_current_counts(current_counts: dict[str, int]) -> None:
    """Write current download counts to the repository state file."""

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    STATE_FILE.write_text(
        json.dumps(current_counts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY")
    github_token = os.environ.get("GITHUB_TOKEN")
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    missing = [
        name
        for name, value in (
            ("GITHUB_REPOSITORY", repository),
            ("GITHUB_TOKEN", github_token),
            ("DISCORD_WEBHOOK_URL", discord_webhook_url),
        )
        if not value
    ]

    if missing:
        print(
            f"Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    try:
        previous_counts = load_previous_counts()
        releases = get_releases(repository, github_token)
        current_counts, details = collect_counts(releases)

        first_run = not STATE_FILE.exists()

        if first_run:
            print(
                "No previous state file exists. Establishing the initial "
                "download-count baseline."
            )
        else:
            message = build_notification(
                repository,
                previous_counts,
                current_counts,
                details,
            )

            if message:
                send_discord_notification(discord_webhook_url, message)
                print("Discord notification sent.")
            else:
                print("No new downloads detected.")

        save_current_counts(current_counts)
        return 0

    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())