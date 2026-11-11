import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync
import releases


CONFIG = {"owner": "couai", "include": [], "exclude": [], "public_only": True,
          "disable_fork_actions": True}


def repository(name="sample", **changes):
    result = {"name": name, "full_name": "couai/" + name, "owner": {"login": "couai"},
              "fork": True, "private": False, "archived": False, "default_branch": "master",
              "parent": {"full_name": "upstream/sample", "default_branch": "main"}}
    result.update(changes)
    return result


class ForkApi:
    def __init__(self):
        self.calls = []
        self.head = "fork-with-own-commit"
        self.target_tags = []
        self.repo = repository()

    def __call__(self, path, method="GET", data=None):
        self.calls.append((path, method, data))
        if path == "repos/couai/sample":
            return self.repo
        if path.endswith("/actions/permissions"):
            return {"enabled": True}
        if path == "repos/upstream/sample/git/ref/heads/main":
            return {"object": {"sha": "upstream-tip"}}
        if path == "repos/couai/sample/git/ref/heads/master":
            return {"object": {"sha": self.head}}
        if "/compare/" in path:
            return {"behind_by": 2 if self.head == "fork-with-own-commit" else 0, "ahead_by": 1}
        if path.endswith("/merges"):
            self.head = "merge-preserving-own-commit"
            return {"sha": self.head}
        if path == "repos/upstream/sample/git/matching-refs/tags/":
            return [{"ref": "refs/tags/v1", "object": {"sha": "annotated-tag-object", "type": "tag"}}]
        if path == "repos/couai/sample/git/matching-refs/tags/":
            return self.target_tags
        if path.endswith("/git/refs"):
            self.target_tags.append({"ref": data["ref"], "object": {"sha": data["sha"]}})
            return self.target_tags[-1]
        raise AssertionError((path, method, data))


class DiscoveryTests(unittest.TestCase):
    def test_new_fork_is_discovered_on_next_paginated_scan(self):
        first_page = [repository(str(i), fork=False) for i in range(100)]
        client = lambda path: {"repositories": first_page if path.endswith("page=1") else [repository("new-fork")]}
        self.assertEqual(sync.discover(client, CONFIG), [{"repository": "new-fork"}])

    def test_excludes_nonfork_private_archived_and_other_owner(self):
        for changes in [{"fork": False}, {"private": True}, {"archived": True},
                        {"disabled": True}, {"owner": {"login": "someone-else"}}]:
            self.assertFalse(sync.selected(repository(**changes), CONFIG))

    def test_pilot_and_exclusion_are_enforced(self):
        config = {**CONFIG, "include": ["GestaltX", "agent-purge"], "exclude": ["agent-purge"]}
        self.assertTrue(sync.selected(repository("GestaltX"), config))
        self.assertFalse(sync.selected(repository("agent-purge"), config))
        self.assertFalse(sync.selected(repository("new-fork"), config))


class SyncTests(unittest.TestCase):
    def test_merge_verification_uses_returned_commit_when_branch_read_is_stale(self):
        class StaleRefApi(ForkApi):
            def __call__(self, path, method="GET", data=None):
                result = super().__call__(path, method, data)
                if path == "repos/couai/sample/git/ref/heads/master":
                    return {"object": {"sha": "fork-with-own-commit"}}
                if "/compare/" in path:
                    return {"behind_by": 2 if path.endswith("...fork-with-own-commit") else 0}
                return result

        client = StaleRefApi()
        result = sync.sync_branch(client, client.repo)
        self.assertEqual(result["result_sha"], "merge-preserving-own-commit")

    def test_merge_preserves_history_and_annotated_tags(self):
        client = ForkApi()
        result = sync.sync_repository(client, CONFIG, "sample", release_sync=lambda *args: {"verified": True})
        self.assertEqual(result["branch"]["result_sha"], "merge-preserving-own-commit")
        self.assertEqual(result["tags"]["created"], 1)
        writes = [(path, data) for path, method, data in client.calls if method != "GET"]
        self.assertEqual(writes[0], ("repos/couai/sample/actions/permissions", {"enabled": False}))
        self.assertEqual(writes[1][1]["base"], "master")
        self.assertEqual(writes[1][1]["head"], "upstream-tip")
        self.assertEqual(writes[2][1]["sha"], "annotated-tag-object")
        self.assertTrue(all(path.startswith("repos/couai/sample/") for path, _ in writes))
        self.assertTrue(all("force" not in data for _, data in writes))

    def test_dry_run_never_changes_actions_code_or_tags(self):
        client = ForkApi()
        release_calls = []
        result = sync.sync_repository(client, CONFIG, "sample", True,
                                      release_sync=lambda *args: release_calls.append(args))
        self.assertTrue(all(method == "GET" for _, method, _ in client.calls))
        self.assertEqual(result["branch"]["status"], "would_merge")
        self.assertEqual(release_calls, [("upstream/sample", "couai/sample", True)])

    def test_manual_nonfork_target_is_rejected_before_writes(self):
        client = ForkApi()
        client.repo["fork"] = False
        with self.assertRaisesRegex(RuntimeError, "eligible"):
            sync.sync_repository(client, CONFIG, "sample")
        self.assertEqual(len(client.calls), 1)

    def test_conflicting_tag_is_never_overwritten(self):
        client = ForkApi()
        client.target_tags = [{"ref": "refs/tags/v1", "object": {"sha": "own-tag"}}]
        with self.assertRaisesRegex(RuntimeError, "no tags overwritten"):
            sync.sync_tags(client, "upstream/sample", "couai/sample")
        self.assertTrue(all(method == "GET" for _, method, _ in client.calls))

    def test_noop_does_not_create_a_merge_or_tag(self):
        client = ForkApi()
        client.head = "already-synced"
        client.target_tags = [{"ref": "refs/tags/v1", "object": {"sha": "annotated-tag-object"}}]
        self.assertEqual(sync.sync_branch(client, client.repo)["status"], "up_to_date")
        self.assertEqual(sync.sync_tags(client, "upstream/sample", "couai/sample")["created"], 0)
        self.assertTrue(all(method == "GET" for _, method, _ in client.calls))


class ReleaseTests(unittest.TestCase):
    def run_release(self, draft=False, body="notes", label=""):
        source = {"id": 1, "tag_name": "v1", "name": "Version 1", "body": "notes",
                  "prerelease": False, "draft": False, "published_at": "2026-01-01T00:00:00Z"}
        target = {**source, "id": 2, "draft": draft, "body": body}
        asset = {"id": 10, "name": "app.ipa", "size": 100, "digest": "sha256:abc",
                 "label": None, "state": "uploaded"}
        copied = {**asset, "id": 11, "label": label}
        writes = []

        def fake_pages(path):
            if path == "repos/upstream/sample/releases":
                return iter([source])
            if path == "repos/couai/sample/releases":
                return iter([target])
            if path.endswith("/1/assets"):
                return iter([asset])
            if path.endswith("/2/assets"):
                return iter([copied])
            raise AssertionError(path)

        def fake_api(path, method="GET", data=None):
            if method != "GET":
                writes.append((path, method, data))
                return {}
            if "/git/ref/" in path:
                return {"object": {"sha": "annotated-tag"}}
            raise AssertionError(path)

        with patch.object(releases, "pages", fake_pages), patch.object(releases, "api", fake_api), \
             patch.object(releases, "optional", lambda p: {"object": {"sha": "annotated-tag"}} if "/git/ref/" in p else {"tag_name": "v1"}), \
             patch.object(releases, "download", side_effect=AssertionError("unchanged asset downloaded")):
            report = releases.sync_releases("upstream/sample", "couai/sample")
        return report, writes

    def test_repeated_sync_has_no_writes_or_downloads(self):
        report, writes = self.run_release()
        self.assertEqual(writes, [])
        self.assertEqual(report["assets_uploaded"], 0)
        self.assertEqual(report["releases_updated"], 0)

    def test_resume_uploaded_draft_publishes_without_reupload(self):
        report, writes = self.run_release(draft=True)
        self.assertEqual(report["assets_uploaded"], 0)
        self.assertEqual(len(writes), 1)
        self.assertFalse(writes[0][2]["draft"])

    def test_edited_notes_update_metadata_without_reupload(self):
        report, writes = self.run_release(body="old notes")
        self.assertEqual(report["releases_updated"], 1)
        self.assertEqual(writes[0][2]["body"], "notes")

    def test_label_changes_do_not_replace_asset_bytes(self):
        report, writes = self.run_release(label="outdated label")
        self.assertEqual(report["asset_labels_updated"], 1)
        self.assertEqual(writes, [("repos/couai/sample/releases/assets/11", "PATCH", {"label": ""})])


if __name__ == "__main__":
    unittest.main()
