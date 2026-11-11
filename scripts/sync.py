"""Discover owned forks and mirror them without executing upstream code."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from releases import api, sync_releases


def selected(repository, config):
    name = repository["name"]
    full_name = repository["full_name"]
    return (
        repository["owner"]["login"].lower() == config["owner"].lower()
        and repository["fork"]
        and not repository.get("archived")
        and not repository.get("disabled")
        and not (config["public_only"] and repository["private"])
        and (not config["include"] or name in config["include"] or full_name in config["include"])
        and name not in config["exclude"] and full_name not in config["exclude"]
    )


def discover(client, config):
    repositories = []
    page = 1
    while True:
        items = client(f"installation/repositories?per_page=100&page={page}")["repositories"]
        repositories.extend(r for r in items if selected(r, config))
        if len(items) < 100:
            break
        page += 1
    return [{"repository": r["name"]} for r in sorted(repositories, key=lambda r: r["name"].lower())]


def sync_branch(client, repository, dry_run=False):
    target = repository["full_name"]
    branch = repository["default_branch"]
    source = repository["parent"]["full_name"]
    source_branch = repository["parent"]["default_branch"]
    target_path = f"repos/{target}/git/ref/heads/{quote(branch, safe='')}"
    source_sha = client(f"repos/{source}/git/ref/heads/{quote(source_branch, safe='')}")["object"]["sha"]
    before = client(target_path)["object"]["sha"]
    comparison = client(f"repos/{target}/compare/{source_sha}...{before}")
    report = {"upstream": source, "branch": branch, "upstream_branch": source_branch,
              "upstream_sha": source_sha, "previous_sha": before, "behind_by": comparison["behind_by"]}
    if not comparison["behind_by"]:
        return {**report, "status": "up_to_date"}
    if dry_run:
        return {**report, "status": "would_merge"}
    client(f"repos/{target}/merges", "POST", {
        "base": branch, "head": source_sha,
        "commit_message": f"chore: sync upstream {source}:{source_branch} [skip ci]",
    })
    after = client(target_path)["object"]["sha"]
    if client(f"repos/{target}/compare/{source_sha}...{after}")["behind_by"]:
        raise RuntimeError("Merged branch does not contain the upstream commit")
    return {**report, "status": "synced", "result_sha": after}


def sync_tags(client, source, target, dry_run=False):
    source_tags = {r["ref"]: r["object"]["sha"] for r in client(f"repos/{source}/git/matching-refs/tags/")}
    target_tags = {r["ref"]: r["object"]["sha"] for r in client(f"repos/{target}/git/matching-refs/tags/")}
    conflicts = [ref for ref, sha in source_tags.items() if ref in target_tags and target_tags[ref] != sha]
    if conflicts:
        raise RuntimeError("Existing tags differ from upstream; no tags overwritten: " + ", ".join(conflicts))
    missing = {ref: sha for ref, sha in source_tags.items() if ref not in target_tags}
    if not dry_run:
        for ref, sha in missing.items():
            result = client(f"repos/{target}/git/refs", "POST", {"ref": ref, "sha": sha})
            if result["object"]["sha"] != sha:
                raise RuntimeError(f"Tag verification failed: {ref}")
    return {"checked": len(source_tags), "created": 0 if dry_run else len(missing), "missing": len(missing)}


def sync_repository(client, config, name, dry_run=False, release_sync=sync_releases):
    # Recheck ownership and configuration before any write, including manual runs.
    repository = client(f"repos/{config['owner']}/{name}")
    if not selected(repository, config) or not repository.get("parent"):
        raise RuntimeError("Target is not an eligible owned fork")
    target = repository["full_name"]
    source = repository["parent"]["full_name"]
    if config["disable_fork_actions"] and not dry_run:
        permissions = client(f"repos/{target}/actions/permissions")
        if permissions["enabled"]:
            # Synchronization runs centrally, so inherited workflows cannot publish over mirrored assets.
            client(f"repos/{target}/actions/permissions", "PUT", {"enabled": False})
    branch = sync_branch(client, repository, dry_run)
    tags = sync_tags(client, source, target, dry_run)
    releases = release_sync(source, target, dry_run)
    return {"repository": target, "dry_run": dry_run, "branch": branch, "tags": tags, "releases": releases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["discover", "sync"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--repository", default=os.environ.get("TARGET_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.command == "discover":
        matrix = discover(api, config)
        print(json.dumps({"include": matrix}, separators=(",", ":")))
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a") as file:
                file.write("matrix=" + json.dumps({"include": matrix}, separators=(",", ":")) + "\n")
                file.write(f"count={len(matrix)}\n")
        return
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "false").lower() == "true"
    report = sync_repository(api, config, args.repository, dry_run)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered, flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as file:
            file.write("## " + report["repository"] + "\n\n```json\n" + rendered + "\n```\n")


if __name__ == "__main__":
    main()
