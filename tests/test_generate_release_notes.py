"""Exercise real Git ranges and the official SDK without making paid requests."""

import json
import os
import subprocess
from types import SimpleNamespace

import httpx2
from openai import OpenAI
import pytest

import generate_release_notes as notes


REPOSITORY = "example/project"


@pytest.fixture
def checkout(tmp_path):
    """Build a release history including generated noise and a reverted feature."""

    def run(*args):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            timeout=15,
        ).stdout.strip()

    def write(path, content):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def commit(message):
        run("add", ".")
        run(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    run("init", "--initial-branch=main")
    run("remote", "add", "origin", "https://github.com/example/project.git")
    write("pyproject.toml", '[project]\nversion="1.0.0"\ndependencies=["pillow==12.0.0"]\n')
    write("src/colors.py", 'COLORS = ["yellow"]\n')
    write("src/reverted.py", "FEATURE = False\n")
    write("src/models/__init__.py", 'MODES = ("all_matches", "named_matches")\n')
    write("src/gui/filter_dialog.py", 'def names_enabled(mode):\n    return mode != "all_matches"\n')
    commit("Initial version")
    run("tag", "v1.0.0")
    write("src/reverted.py", "FEATURE = True\n")
    commit("feat: Add a feature that will be reverted")
    write("src/reverted.py", "FEATURE = False\n")
    write("src/colors.py", 'COLORS = ["yellow", "blue"]\n')
    write("src/gui/dev_tools.py", "WINDOW_POSITION = 20\n")
    write("tests/test_colors.py", "# supporting test name only\n")
    write("uv.lock", "GENERATED_LOCKFILE_CONTENT\n")
    write(".env.example", "EXCLUDED_ENV_CONTENT\n")
    write("private.pem", "EXCLUDED_KEY_CONTENT\n")
    write("pyproject.toml", '[project]\nversion="1.1.0"\ndependencies=["pillow==13.0.0"]\n')
    commit("Add blue highlights and revert the other feature")
    run("tag", "v1.1.0rc1")
    run("tag", "v1.1.0")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path, run


@pytest.fixture
def context(checkout):
    return notes.collect_context("v1.0.0", "v1.1.0", REPOSITORY, cwd=checkout[0])


@pytest.fixture
def valid_notes(context):
    sha = context["commits"][0]["sha"]
    return {
        "highlights": [
            {"text": "Choose blue highlights as well as yellow.", "evidence": ["src/colors.py"], "commits": [sha]}
        ],
        "dependencies": [{"text": "Bump pillow to 13.0.0.", "evidence": ["pyproject.toml"], "commits": [sha]}],
    }


def test_context_uses_net_committed_diff_and_omits_generated_or_sensitive_content(context, checkout):
    checkout[0].joinpath("src/colors.py").write_text("UNCOMMITTED_CONTENT\n", encoding="utf-8")
    refreshed = notes.collect_context("v1.0.0", "v1.1.0", REPOSITORY, cwd=checkout[0])
    assert context == refreshed
    assert '+COLORS = ["yellow", "blue"]' in context["application_diff"]
    assert "src/reverted.py" not in context["changed_files"]
    assert all("will be reverted" not in commit["subject"] for commit in context["commits"])
    assert all("src/reverted.py" not in commit["files"] for commit in context["commits"])
    assert "tests/test_colors.py" in context["changed_files"]
    assert "src/gui/dev_tools.py" in context["changed_files"]
    assert "WINDOW_POSITION" not in context["application_diff"]
    payload = notes.encode_input(context)
    for excluded in (
        "UNCOMMITTED_CONTENT",
        "GENERATED_LOCKFILE_CONTENT",
        "EXCLUDED_ENV_CONTENT",
        "EXCLUDED_KEY_CONTENT",
    ):
        assert excluded not in payload
    assert context["dependencies"]["runtime"] == {
        "before": ["pillow==12.0.0"],
        "after": ["pillow==13.0.0"],
    }


@pytest.fixture
def dependency_checkout(checkout):
    """Allow real branch/merge histories without depending on the user's Git configuration."""
    cwd, run = checkout
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    run("config", "commit.gpgsign", "false")

    def change(runtime, groups=None, optional=None):
        text = '[project]\nversion="1.1.0"\ndependencies=' + json.dumps(runtime) + "\n"
        if optional:
            text += "[project.optional-dependencies]\n"
            text += "".join(f"{key}={json.dumps(value)}\n" for key, value in optional.items())
        if groups:
            text += "[dependency-groups]\n"
            text += "".join(f"{key}={json.dumps(value)}\n" for key, value in groups.items())
        (cwd / "pyproject.toml").write_text(text, encoding="utf-8")
        run("add", "pyproject.toml")
        run("commit", "-m", "Update requirements")
        return run("rev-parse", "HEAD")

    return cwd, run, change


def test_dependency_versions_follow_the_introducing_merge_not_later_manifest_commits(dependency_checkout):
    cwd, run, change = dependency_checkout
    base = run("rev-parse", "HEAD")
    run("checkout", "-b", "first-update")
    change(["pillow==13.1.0", "new-package==1.0"], {"lint": ["ruff==1.0"]})
    run("checkout", "main")
    run("merge", "--no-ff", "first-update", "-m", "Merge first update")
    first_merge = run("rev-parse", "HEAD")
    run("checkout", "-b", "second-update")
    change(["pillow==13.2.0", "new-package==1.0"], {"lint": ["ruff==1.0"]})
    change(["pillow==14.0.0", "new-package==1.0"], {"lint": ["ruff==1.0"]})
    run("checkout", "main")
    run("merge", "--no-ff", "second-update", "-m", "Merge second update")
    second_merge = run("rev-parse", "HEAD")
    with (cwd / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write("\n# Unrelated release bookkeeping\n")
    run("commit", "-am", "Release bookkeeping")
    context = notes.collect_context(base, "HEAD", REPOSITORY, cwd)
    by_text = {item["text"]: item["commits"] for item in context["dependency_notes"]}
    assert by_text == {
        "Bump pillow to 14.0.0.": [second_merge],
        "Add new-package 1.0.": [first_merge],
        "Add ruff 1.0.": [first_merge],
    }
    assert {first_merge, second_merge} <= {commit["sha"] for commit in context["commits"]}
    # The generated merge references pass the same evidence validation as normal notes.
    notes.validate_notes({"highlights": [], "dependencies": context["dependency_notes"]}, context)


def test_reverted_and_unreachable_dependency_versions_are_not_release_changes(dependency_checkout):
    cwd, run, change = dependency_checkout
    base = run("rev-parse", "HEAD")
    run("checkout", "-b", "unmerged")
    change(["pillow==99.0.0"])
    run("checkout", "main")
    change(["pillow==14.0.0"])
    change(["pillow==13.0.0"])
    assert notes.collect_dependency_notes(base, run("rev-parse", "HEAD"), cwd) == []
    final_bump = change(["pillow==14.0.0"])
    assert notes.collect_dependency_notes(base, final_bump, cwd) == [
        {"text": "Bump pillow to 14.0.0.", "evidence": ["pyproject.toml"], "commits": [final_bump]}
    ]


def test_dependency_group_moves_and_package_name_spelling_do_not_create_bumps(dependency_checkout):
    cwd, run, change = dependency_checkout
    base = run("rev-parse", "HEAD")
    target = change([], {"dev": ["PILLOW==13.0.0"]}, {"images": ["pillow==13.0.0"]})
    assert notes.collect_dependency_notes(base, target, cwd) == []
    assert notes.dependency_declarations(
        {"dependency-groups": {"dev": [{"include-group": "build"}], "build": ["CX_Freeze==8.7.3", "cx-freeze==8.7.3"]}}
    ) == {"cx-freeze": ["==8.7.3"]}


def test_dependency_additions_removals_markers_and_unpinned_requirements(dependency_checkout):
    cwd, run, change = dependency_checkout
    base = run("rev-parse", "HEAD")
    target = change(["pymupdf-lite==2.0", "requests>=2.0"], {"build": ["pywin32==312; sys_platform == 'win32'"]})
    assert {item["text"] for item in notes.collect_dependency_notes(base, target, cwd)} == {
        "Remove pillow.",
        "Add pymupdf-lite 2.0.",
        "Add requests.",
        "Add pywin32 312.",
    }
    next_target = change(["pymupdf-lite==2.0", "requests>=3.0"], {"build": ["pywin32==313; sys_platform == 'win32'"]})
    assert {item["text"] for item in notes.collect_dependency_notes(target, next_target, cwd)} == {
        "Update requests requirement.",
        "Bump pywin32 to 313.",
    }


def test_git_dependency_notes_discard_model_versions_and_implementation_details(context, valid_notes):
    valid_notes["dependencies"][0]["text"] = "Bump Pillow to 99.0.0."
    other = {
        "text": "Keep PDF preview work off the main thread.",
        "evidence": ["src/colors.py"],
        "commits": [context["commits"][0]["sha"]],
    }
    valid_notes["dependencies"].append(other)
    result = notes.add_dependency_notes(valid_notes, context)
    assert result["highlights"] == valid_notes["highlights"]
    assert result["dependencies"] == [
        {"text": "Bump pillow to 13.0.0.", "evidence": ["pyproject.toml"], "commits": [context["commits"][0]["sha"]]},
    ]


def test_tool_versions_follow_the_introducing_pr_and_ignore_unrelated_ci_changes(dependency_checkout):
    cwd, run, change = dependency_checkout
    workflow = cwd / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)

    def tools(python, inno):
        (cwd / ".python-version").write_text(python + "\n", encoding="utf-8")
        workflow.write_text(
            "jobs:\n  build:\n    steps:\n      - name: Install Inno Setup\n        run: |\n"
            f"          $targetVersion = [Version]'{inno}'\n"
            "      - name: Unrelated tool\n        run: |\n          $targetVersion = [Version]'99.0.0'\n",
            encoding="utf-8",
        )
        run("add", ".python-version", ".github/workflows/ci.yml")
        run("commit", "-m", "Update build tool versions")
        return run("rev-parse", "HEAD")

    base = tools("3.14.8", "6.7.1.0")
    run("checkout", "-b", "tool-update")
    tools("3.14.9", "6.8.0.0")
    change(["pillow==14.0.0"])
    run("checkout", "main")
    run("merge", "--no-ff", "tool-update", "-m", "Merge tool update")
    merge = run("rev-parse", "HEAD")
    context = notes.collect_context(base, "HEAD", REPOSITORY, cwd)
    assert {item["text"]: item["commits"] for item in context["dependency_notes"]} == {
        "Set Inno Setup minimum to 6.8.0.": [merge],
        "Bump Python to 3.14.9.": [merge],
        "Bump pillow to 14.0.0.": [merge],
    }
    assert next(commit["files"] for commit in context["commits"] if commit["sha"] == merge) == sorted(
        notes.DEPENDENCY_FILES
    )
    notes.validate_notes({"highlights": [], "dependencies": context["dependency_notes"]}, context)
    # The fourth Windows version component and unrelated CI changes are not new updates.
    unchanged = tools("3.14.9", "6.8.0")
    assert notes.collect_dependency_notes(merge, unchanged, cwd) == []


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  build:\n    steps:\n      - name: Other tool\n        run: $targetVersion = [Version]'6.8.0'\n",
        "steps:\n  - name: Install Inno Setup\n    run: echo 6.8.0\n"
        "  - name: Other tool\n    run: $targetVersion = [Version]'99.0.0'\n",
    ],
)
def test_inno_version_is_not_inferred_from_logs_or_other_steps(workflow):
    assert notes.inno_setup_version(workflow) is None


def test_behavior_context_includes_unchanged_target_ui_constraints(context, checkout):
    assert "src/gui/filter_dialog.py" not in context["changed_files"]
    assert 'return mode != "all_matches"' in context["behavior_context"]["src/gui/filter_dialog.py"]
    checkout[0].joinpath("src/gui/filter_dialog.py").write_text("UNCOMMITTED_BEHAVIOR\n", encoding="utf-8")
    refreshed = notes.collect_context("v1.0.0", "v1.1.0", REPOSITORY, cwd=checkout[0])
    assert refreshed["behavior_context"] == context["behavior_context"]
    assert "UNCOMMITTED_BEHAVIOR" not in notes.encode_input(refreshed)


def test_project_context_is_generated_from_the_target_and_tracks_new_modules(context, checkout):
    cwd, run = checkout
    assert context["project_context"]["metadata"]["version"] == "1.1.0"
    path = cwd / "src" / "new_feature.py"
    path.write_text('"""Describe a new capability."""\n\ndef new_feature():\n    pass\n', encoding="utf-8")
    run("add", "src/new_feature.py")
    run(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Add a new module",
    )
    updated = notes.collect_project_context(run("rev-parse", "HEAD"), cwd)
    assert {
        "path": "src/new_feature.py",
        "summary": "Describe a new capability.",
        "public_symbols": ["new_feature"],
    } in updated["source_modules"]
    assert updated != notes.collect_project_context(context["to_sha"], cwd)


def published(tag, **kwargs):
    return {"tag_name": tag, "draft": False, "prerelease": False, "published_at": "2026-01-01", **kwargs}


def test_baseline_uses_published_stable_ancestor_and_skips_rc_and_draft(checkout):
    cwd, run = checkout
    releases = [
        published("v1.1.0"),
        published("v1.1.0rc1", prerelease=True),
        published("v1.0.9", draft=True),
        published("v1.0.8", published_at=None),
        published("v1.0.0"),
    ]
    assert notes.previous_release(releases, "v1.1.0rc2", run("rev-parse", "HEAD"), cwd) == "v1.0.0"


def test_baseline_skips_release_from_unrelated_branch(checkout):
    cwd, run = checkout
    run("checkout", "--orphan", "unrelated")
    run(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-m",
        "Unrelated history",
    )
    run("tag", "v1.0.5")
    run("checkout", "main")
    releases = [published("v1.0.5"), published("v1.0.0")]
    assert notes.previous_release(releases, "v1.1.0", run("rev-parse", "HEAD"), cwd) == "v1.0.0"


def test_missing_baseline_and_identical_or_reversed_ranges_fail(checkout):
    cwd, run = checkout
    with pytest.raises(notes.NotesError, match="explicit --from"):
        notes.previous_release([], "v1.1.0", run("rev-parse", "HEAD"), cwd)
    for base, target, reason in (("HEAD", "HEAD", "same commit"), ("v1.1.0", "v1.0.0", "ancestor")):
        with pytest.raises(notes.NotesError, match=reason):
            notes.collect_context(base, target, REPOSITORY, cwd)


def test_oversized_input_is_rejected_before_an_api_call(context, monkeypatch):
    monkeypatch.setattr(notes, "MAX_INPUT_BYTES", 20)
    with pytest.raises(notes.NotesError, match="limit"):
        notes.generate(context, notes.DEFAULT_MODEL, client=SimpleNamespace())


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda n: n.update(extra=[]), "arrays"),
        (lambda n: n.update(highlights=None), "array"),
        (lambda n: n["highlights"].append(n["highlights"][0]), "duplicate"),
        (lambda n: n["highlights"][0].update(text="## New heading"), "plain-text"),
        (lambda n: n["highlights"][0].update(text="Visit https://example.com"), "plain-text"),
        (lambda n: n["highlights"][0].update(text="First line\nSecond line"), "plain-text"),
        (lambda n: n["highlights"][0].update(evidence=["unchanged.py"]), "outside"),
        (lambda n: n["highlights"][0].update(evidence=["tests/test_colors.py"]), "application"),
        (lambda n: n["highlights"][0].update(evidence=["src/gui/dev_tools.py"]), "application"),
        (lambda n: n["highlights"][0].update(commits=["0" * 40]), "release range"),
        (lambda n: n["highlights"][0].update(commits=[]), "release range"),
        (lambda n: n["highlights"][0].update(commits=n["highlights"][0]["commits"] * 2), "distinct"),
    ],
)
def test_invalid_output_cannot_change_format_or_claim_unsupported_evidence(context, valid_notes, mutate, reason):
    mutate(valid_notes)
    with pytest.raises(notes.NotesError, match=reason):
        notes.validate_notes(valid_notes, context)


def test_large_release_preserves_changes_without_a_total_word_limit(context):
    data = {
        section: [
            {
                "text": f"{section} item {i} " + "word " * 25 + "end.",
                "evidence": [path],
                "commits": [context["commits"][0]["sha"]],
            }
            for i in range(count)
        ]
        for section, count, path in (("highlights", 20, "src/colors.py"), ("dependencies", 8, "pyproject.toml"))
    }
    assert sum(len(item["text"].split()) for items in data.values() for item in items) > 150
    context["dependency_notes"] = data["dependencies"]
    assert notes.validate_notes(data, context) == data
    rendered = notes.render_notes(data, context)
    assert rendered.count("- highlights item") == 20
    assert rendered.count("- dependencies item") == 8


def test_more_than_five_highlights_are_allowed(context, valid_notes):
    source = valid_notes["highlights"][0]
    valid_notes["highlights"] = [{**source, "text": f"Highlight {index}."} for index in range(7)]
    validated = notes.validate_notes(valid_notes, context)
    rendered = notes.render_notes(validated, context)
    assert len(validated["highlights"]) == 7
    assert rendered.split("## Dependency updates")[0].count("- Highlight") == 7
    assert "maxItems" not in notes.output_schema()["properties"]["highlights"]


def test_bullet_text_has_no_arbitrary_word_or_character_limit(context, valid_notes):
    text = " ".join(f"Detail{index}" for index in range(40)) + "."
    assert len(text) > 200
    valid_notes["highlights"][0]["text"] = text
    assert notes.validate_notes(valid_notes, context) == valid_notes
    assert "maxLength" not in notes.output_schema()["properties"]["highlights"]["items"]["properties"]["text"]


def test_fixed_format_and_footer_do_not_publish_evidence(context, valid_notes):
    rendered = notes.render_notes(notes.validate_notes(valid_notes, context), context)
    assert rendered.splitlines()[0] == "## Highlights"
    assert rendered.count("## ") == 2
    assert "## Dependency updates" in rendered
    assert "https://github.com/example/project/compare/v1.0.0...v1.1.0" in rendered
    assert "src/colors.py" not in rendered
    sha = context["commits"][0]["sha"]
    assert f"([{sha[:7]}](https://github.com/example/project/commit/{sha}))" in rendered
    empty_context = {**context, "dependency_notes": []}
    empty = notes.render_notes(
        notes.validate_notes({"highlights": [], "dependencies": []}, empty_context), empty_context
    )
    assert "No user-facing changes" in empty
    assert "No dependency updates." in empty


def test_dependency_updates_are_preserved_as_individual_lines(context, valid_notes):
    source = valid_notes["dependencies"][0]
    texts = ["Bump Pillow to 13.0.0.", "Bump tkinterweb to 4.25.5.", "Replace pymupdf with pymupdf-lite 2.0."]
    valid_notes["dependencies"] = [{**source, "text": text} for text in texts]
    context["dependency_notes"] = valid_notes["dependencies"]
    rendered = notes.render_notes(notes.validate_notes(valid_notes, context), context)
    dependencies = rendered.split("## Dependency updates\n\n", 1)[1].split("\n\n**Full Changelog**", 1)[0]
    lines = dependencies.splitlines()
    assert len(lines) == 3
    assert all(line.startswith(f"- {text}") for line, text in zip(lines, texts))
    assert set(notes.output_schema()["properties"]) == {"highlights"}


@pytest.mark.parametrize(
    "text",
    [
        "Stage PDF output before replacing the destination and prevent overwriting the source.",
        "Wait for OCR workers to clean up when processing is cancelled.",
    ],
)
def test_implementation_details_cannot_enter_dependency_updates(context, valid_notes, text):
    valid_notes["dependencies"].append(
        {"text": text, "evidence": ["src/colors.py"], "commits": [context["commits"][0]["sha"]]}
    )
    with pytest.raises(notes.NotesError, match="derived from Git"):
        notes.validate_notes(valid_notes, context)
    # Even a caller skipping validation cannot render injected dependency text.
    assert text not in notes.render_notes(valid_notes, context)


def test_citations_must_touch_the_bullets_evidence_files(context, valid_notes):
    sha = "1" * 40
    context["commits"].append({"sha": sha, "subject": "Test-only change", "files": ["tests/test_colors.py"]})
    valid_notes["highlights"][0]["commits"] = [sha]
    with pytest.raises(notes.NotesError, match="evidence files"):
        notes.validate_notes(valid_notes, context)


def test_render_two_source_commits_without_a_misleading_contiguous_range(context, valid_notes):
    sha = "2" * 40
    context["commits"].append({"sha": sha, "subject": "Follow-up color fix", "files": ["src/colors.py"]})
    valid_notes["highlights"][0]["commits"].append(sha)
    rendered = notes.render_notes(notes.validate_notes(valid_notes, context), context)
    assert f", [{sha[:7]}](https://github.com/example/project/commit/{sha})" in rendered


def test_pr_links_replace_and_deduplicate_commit_links_per_bullet(context, valid_notes):
    first = context["commits"][0]["sha"]
    second = "2" * 40
    valid_notes["highlights"][0]["commits"].append(second)
    context["pull_requests"] = {first: 41, second: 41}
    rendered = notes.render_notes(valid_notes, context)
    assert rendered.split("## Dependency updates")[0].count("[#41]") == 1
    assert rendered.count("[#41](https://github.com/example/project/pull/41)") == 2
    assert "/commit/" not in rendered


def merged_pr(number, sha, **overrides):
    return {
        "number": number,
        "merged_at": "2026-01-01",
        "merge_commit_sha": sha,
        "base": {"repo": {"full_name": REPOSITORY}},
        **overrides,
    }


def test_resolve_merged_prs_uses_github_association_and_deduplicates_lookups(
    context, valid_notes, checkout, monkeypatch
):
    calls = []
    sha = context["commits"][0]["sha"]

    def get(url, **kwargs):
        calls.append((url, kwargs))
        data = [
            merged_pr(41, sha),
            merged_pr(99, sha, merged_at=None),
            merged_pr(100, "0" * 40),
            merged_pr(101, sha, base={"repo": {"full_name": "other/fork"}}),
        ]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    monkeypatch.setattr(notes.requests, "get", get)
    assert notes.resolve_pull_requests(context, valid_notes, checkout[0]) == {sha: 41}
    assert len(calls) == 1
    assert calls[0][0].endswith(f"/commits/{sha}/pulls")
    assert calls[0][1]["timeout"] == 10


@pytest.mark.parametrize(
    "data",
    [[], [merged_pr(41, "0" * 40)], [merged_pr(41, "x", merged_at=None)], [merged_pr(41, [], merged_at="2026-01-01")]],
)
def test_direct_or_unmerged_changes_keep_commit_links(context, valid_notes, checkout, monkeypatch, data):
    monkeypatch.setattr(
        notes.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
    )
    assert notes.resolve_pull_requests(context, valid_notes, checkout[0]) == {}


@pytest.mark.parametrize("failure", ["http", "invalid"])
def test_pr_lookup_failure_preserves_notes_and_falls_back_to_commits(
    context, valid_notes, checkout, monkeypatch, capsys, failure
):
    def get(*args, **kwargs):
        if failure == "http":
            raise notes.requests.ConnectionError("DO_NOT_ECHO")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"bad": "response"})

    monkeypatch.setattr(notes.requests, "get", get)
    context["pull_requests"] = notes.resolve_pull_requests(context, valid_notes, checkout[0])
    assert context["pull_requests"] == {}
    assert "/commit/" in notes.render_notes(valid_notes, context)
    assert "DO_NOT_ECHO" not in capsys.readouterr().out


def test_head_preview_link_uses_immutable_commit(context, valid_notes):
    context["to"] = "HEAD"
    assert f"...{context['to_sha']})" in notes.render_notes(valid_notes, context)


def sdk_client(handler):
    """Exercise SDK serialization and error handling against an in-memory HTTP transport."""
    return OpenAI(
        api_key="test-not-a-real-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def api_response(data, *, status="completed", output=None):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": notes.DEFAULT_MODEL,
        "status": status,
        "output": output
        if output is not None
        else [
            {
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(data), "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 20},
        },
    }


@pytest.mark.parametrize("effort", [None, "none", "low", "medium", "high", "xhigh", "max"])
def test_sdk_request_uses_low_verbosity_structured_output_and_requested_reasoning(context, valid_notes, effort):
    requests_seen = []

    def handler(request):
        requests_seen.append(json.loads(request.content))
        return httpx2.Response(200, json=api_response({"highlights": valid_notes["highlights"]}))

    with sdk_client(handler) as client:
        kwargs = {} if effort is None else {"reasoning_effort": effort}
        result, usage, receipt = notes.generate(context, notes.DEFAULT_MODEL, client, **kwargs)
    assert result["highlights"] == valid_notes["highlights"]
    assert result["dependencies"] == [
        {"text": "Bump pillow to 13.0.0.", "evidence": ["pyproject.toml"], "commits": [context["commits"][0]["sha"]]}
    ]
    assert usage["total_tokens"] == 150
    assert receipt["response_id"] == "resp_test"
    assert receipt["reasoning_effort"] == (effort or "low")
    assert "billed_cost" not in receipt
    assert len(requests_seen) == 1
    request = requests_seen[0]
    assert request["model"] == notes.DEFAULT_MODEL == "gpt-6.1-terra"
    assert "max_output_tokens" not in request
    assert request["text"]["verbosity"] == "low"
    assert request["store"] is False
    assert request["service_tier"] == "default"
    assert request["reasoning"] == {"effort": effort or "low"}
    assert "tools" not in request
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["additionalProperties"] is False
    assert request["text"]["format"]["schema"]["required"] == ["highlights"]
    assert set(request["text"]["format"]["schema"]["properties"]) == {"highlights"}
    fields = request["text"]["format"]["schema"]["properties"]["highlights"]["items"]["properties"]
    assert fields["evidence"]["items"]["enum"] == context["changed_files"]
    assert fields["commits"]["items"]["enum"] == [commit["sha"] for commit in context["commits"]]
    assert json.loads(request["input"])["application_diff"] == context["application_diff"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_sdk_http_failures_are_not_retried_or_echoed(context, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(status, json={"error": {"message": "DO_NOT_ECHO_RESPONSE_BODY"}})

    with sdk_client(handler) as client, pytest.raises(notes.NotesError) as error:
        notes.generate(context, notes.DEFAULT_MODEL, client)
    assert len(calls) == 1
    assert "DO_NOT_ECHO_RESPONSE_BODY" not in str(error.value)
    assert f"HTTP {status}" in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        api_response({}, status="incomplete"),
        api_response({}, output=[]),
        api_response(
            {},
            output=[
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                }
            ],
        ),
        api_response({"wrong": []}),
        api_response({"highlights": [], "technical": []}),
        api_response({"highlights": [], "dependencies": []}),
    ],
)
def test_refused_empty_incomplete_and_malformed_model_responses_fail(context, response):
    with sdk_client(lambda request: httpx2.Response(200, json=response)) as client:
        with pytest.raises(notes.NotesError):
            notes.generate(context, notes.DEFAULT_MODEL, client)


def test_missing_openai_key_fails_before_request(context, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(notes.NotesError, match="OPENAI_API_KEY"):
        notes.generate(context, notes.DEFAULT_MODEL)


def test_cli_dry_run_is_offline_with_explicit_refs_and_does_not_overwrite_notes(context, monkeypatch, tmp_path):
    output = tmp_path / "preview.md"
    output.write_text("Existing notes", encoding="utf-8")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(notes, "resolve_ref", lambda ref: context["to_sha"])
    monkeypatch.setattr(notes, "git", lambda *args: '[project]\nversion="1.1.0"\n')
    monkeypatch.setattr(notes, "collect_context", lambda *args: context)
    monkeypatch.setattr(notes, "github_releases", lambda *args: pytest.fail("Unexpected GitHub request"))
    monkeypatch.setattr(notes, "generate", lambda *args: pytest.fail("Unexpected OpenAI request"))
    assert notes.main(["--from", "v1.0.0", "--repository", REPOSITORY, "--dry-run", "--output", str(output)]) == 0
    assert json.loads(output.with_suffix(".input.json").read_text(encoding="utf-8")) == context
    assert output.read_text(encoding="utf-8") == "Existing notes"


def test_cli_failure_preserves_existing_notes(context, monkeypatch, tmp_path):
    output = tmp_path / "preview.md"
    output.write_text("Existing notes", encoding="utf-8")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(notes, "resolve_ref", lambda ref: context["to_sha"])
    monkeypatch.setattr(notes, "git", lambda *args: '[project]\nversion="1.1.0"\n')
    monkeypatch.setattr(notes, "collect_context", lambda *args: context)

    def fail(*args, **kwargs):
        raise notes.NotesError("Simulated refusal")

    monkeypatch.setattr(notes, "generate", fail)
    assert notes.main(["--from", "v1.0.0", "--repository", REPOSITORY, "--output", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == "Existing notes"
    assert not output.with_suffix(".evidence.json").exists()


def test_cli_success_writes_markdown_and_private_audit_metadata(context, valid_notes, monkeypatch, tmp_path, capsys):
    output = tmp_path / "preview.md"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(notes, "resolve_ref", lambda ref: context["to_sha"])
    monkeypatch.setattr(notes, "git", lambda *args: '[project]\nversion="1.1.0"\n')
    monkeypatch.setattr(notes, "collect_context", lambda *args: context)
    receipt = {"response_id": "resp_test"}

    def generate(*args, **kwargs):
        assert kwargs["reasoning_effort"] == "low"
        return valid_notes, {"total_tokens": 150}, receipt

    monkeypatch.setattr(notes, "generate", generate)
    monkeypatch.setattr(notes, "resolve_pull_requests", lambda *args: {context["commits"][0]["sha"]: 41})
    assert (
        notes.main(
            [
                "--from",
                "v1.0.0",
                "--repository",
                REPOSITORY,
                "--model",
                "gpt-6-luna",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_text(encoding="utf-8") == notes.render_notes(valid_notes, context)
    evidence = json.loads(output.with_suffix(".evidence.json").read_text(encoding="utf-8"))
    assert evidence["model"] == "gpt-6-luna"
    assert evidence["from_sha"] == context["from_sha"]
    assert evidence["usage"] == {"total_tokens": 150}
    assert evidence["api_receipt"] == receipt
    assert evidence["notes"] == valid_notes
    assert evidence["dependency_notes"] == context["dependency_notes"]
    assert evidence["pull_requests"] == context["pull_requests"]
    assert len(evidence["input_sha256"]) == 64
    console = capsys.readouterr().out
    assert "cost" not in console.casefold()
    assert "OpenAI response: resp_test" in console
    assert "$" not in console


@pytest.fixture
def ci_environment(monkeypatch):
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "jonalbr/heat-sheet-pdf-highlighter",
        "GITHUB_ACTOR": "jonalbr",
        "GITHUB_TRIGGERING_ACTOR": "jonalbr",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF_NAME": "v1.1.0",
        "GITHUB_SHA": "v1.1.0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_ci_accepts_owner_release_tag_on_main(checkout, ci_environment):
    notes.verify_ci_release("v1.1.0", checkout[0])


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REPOSITORY", "someone/fork"),
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_TRIGGERING_ACTOR", "other"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF_TYPE", "branch"),
        ("GITHUB_REF_NAME", "v1.0.0"),
        ("GITHUB_SHA", "v1.0.0"),
    ],
)
def test_ci_rejects_other_actors_forks_events_and_moved_tags(checkout, ci_environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(notes.NotesError):
        notes.verify_ci_release("v1.1.0", checkout[0])


def test_ci_rejects_tags_outside_main(checkout, ci_environment):
    cwd, run = checkout
    run("update-ref", "refs/remotes/origin/main", "v1.0.0")
    with pytest.raises(notes.NotesError, match="origin/main"):
        notes.verify_ci_release("v1.1.0", cwd)


def test_github_release_pagination_and_auth_do_not_leak_into_input(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        data = [published("v1.0.0")] * 100 if len(calls) == 1 else [published("v1.0.1")]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(notes.requests, "get", get)
    assert len(notes.github_releases(REPOSITORY)) == 101
    assert calls[1][1]["params"]["page"] == 2
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-token"


def test_github_failure_does_not_fall_back_to_an_arbitrary_tag(monkeypatch):
    def get(*args, **kwargs):
        raise notes.requests.ConnectionError("DO_NOT_ECHO")

    monkeypatch.setattr(notes.requests, "get", get)
    with pytest.raises(notes.NotesError, match="explicit --from") as error:
        notes.github_releases(REPOSITORY)
    assert "DO_NOT_ECHO" not in str(error.value)


@pytest.mark.parametrize("origin", ["https://github.com/example/project.git", "git@github.com:example/project.git"])
def test_infer_repository(origin, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(notes, "git", lambda *args, **kwargs: origin)
    assert notes.infer_repository() == REPOSITORY
