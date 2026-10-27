"""Generate concise, user-facing release notes from a bounded Git snapshot diff."""

from __future__ import annotations

import argparse
import ast
from functools import cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "gpt-6.1-terra"
MODELS = (DEFAULT_MODEL, "gpt-6-luna")
MAX_INPUT_BYTES = 600_000
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:rc\d+)?$")
PROMPT_PATH = ROOT / ".github" / "release_notes_prompt.md"
DEPENDENCY_FILES = ("pyproject.toml", ".python-version", ".github/workflows/ci.yml")
INTERNAL_PATHS = {"src/gui/dev_tools.py", "src/gui/dev_tools_geometry.py"}
BEHAVIOR_CONTEXT_PATHS = (
    "src/models/__init__.py",
    "src/gui/ui_strings.py",
    "src/gui/filter_dialog.py",
    "src/core/pdf_processor.py",
    "tests/core/test_marking_colors.py",
)


class NotesError(RuntimeError):
    """A release-note precondition or validation failed."""


def git(*args: str, cwd: Path = ROOT) -> str:
    """Read Git state without executing hooks, external diff tools, or a shell."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
    )
    if result.returncode:
        raise NotesError(f"Git command failed: git {args[0]} (check that the requested refs are available).")
    return result.stdout


def resolve_ref(ref: str, cwd: Path = ROOT) -> str:
    """Resolve a tag, branch, or commit to an immutable commit ID."""
    return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=cwd).strip()


def verify_ci_release(target: str, cwd: Path = ROOT) -> None:
    """Restrict paid CI requests to the owner's release tags pointing into main."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    if (
        os.environ.get("GITHUB_REPOSITORY") != "jonalbr/heat-sheet-pdf-highlighter"
        or os.environ.get("GITHUB_ACTOR") != "jonalbr"
        or os.environ.get("GITHUB_TRIGGERING_ACTOR") != "jonalbr"
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF_TYPE") != "tag"
        or os.environ.get("GITHUB_REF_NAME") != target
    ):
        raise NotesError("CI release generation is restricted to the repository owner's tag pushes and reruns.")
    version_tuple(target)
    target_sha = resolve_ref(f"refs/tags/{target}", cwd)
    if target_sha != resolve_ref(os.environ.get("GITHUB_SHA", ""), cwd):
        raise NotesError("The release tag does not match the triggering commit.")
    main_sha = resolve_ref("refs/remotes/origin/main", cwd)
    if target_sha not in git("rev-list", main_sha, cwd=cwd).splitlines():
        raise NotesError("Release tags must point to a commit belonging to origin/main.")


def version_tuple(tag: str) -> tuple[int, int, int]:
    """Compare stable versions and RC baselines using their numeric base version."""
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise NotesError("Release tags must use vX.Y.Z or vX.Y.ZrcN.")
    return tuple(map(int, match.groups()))


def repository_name(value: str) -> str:
    """Validate a GitHub owner/repository name before using it in URLs."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise NotesError("Repository must be a GitHub owner/repository name.")
    return value


def infer_repository(cwd: Path = ROOT) -> str:
    """Use the Actions repository or the checkout's GitHub origin."""
    if value := os.environ.get("GITHUB_REPOSITORY"):
        return repository_name(value)
    origin = git("remote", "get-url", "origin", cwd=cwd).strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)(.+?)(?:\.git)?/?", origin)
    if not match:
        raise NotesError("Cannot infer a GitHub repository; pass --repository owner/repo.")
    return repository_name(match[1])


def github_headers() -> dict[str, str]:
    """Use read-only GitHub authentication when supplied, without exposing it to the model."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_releases(repository: str) -> list[dict]:
    """Read published releases, including pagination; never print credentials or response bodies."""
    headers = github_headers()
    releases = []
    for page in range(1, 11):
        try:
            response = requests.get(
                f"https://api.github.com/repos/{repository}/releases",
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=20,
            )
            response.raise_for_status()
            batch = response.json()
        except requests.RequestException, ValueError:
            raise NotesError("Cannot read GitHub releases; check access or supply an explicit --from ref.") from None
        if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
            raise NotesError("GitHub returned an invalid releases list.")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
    raise NotesError("Too many release pages; supply an explicit --from ref.")


def previous_release(releases: list[dict], target_version: str, target_sha: str, cwd: Path = ROOT) -> str:
    """Choose the highest earlier published stable release reachable from the target."""
    current = version_tuple(target_version)
    candidates = []
    for release in releases:
        tag = release.get("tag_name", "")
        if (
            release.get("draft")
            or release.get("prerelease")
            or not release.get("published_at")
            or not isinstance(tag, str)
            or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
        ):
            continue
        if version_tuple(tag) < current:
            candidates.append(tag)
    for tag in sorted(set(candidates), key=version_tuple, reverse=True):
        base_sha = resolve_ref(f"refs/tags/{tag}", cwd)
        if base_sha in git("rev-list", target_sha, cwd=cwd).splitlines():
            return tag
    raise NotesError("No earlier published stable release is available; supply an explicit --from ref.")


def is_app_source(path: str) -> bool:
    """Identify changes that can substantiate a normal user's visible app behavior."""
    return (
        path.startswith("src/")
        and path.endswith(".py")
        and path not in INTERNAL_PATHS
        and path not in {"src/_version.py", "src/version.py"}
    ) or path in {"main.py", "update_app.bat"}


def dependency_changes(base_sha: str, target_sha: str, cwd: Path = ROOT) -> dict:
    """Summarize dependency declarations instead of sending the generated lockfile."""
    before, after = (tomllib.loads(git("show", f"{sha}:pyproject.toml", cwd=cwd)) for sha in (base_sha, target_sha))
    changes = {}
    for label, old, new in (
        (
            "runtime",
            before.get("project", {}).get("dependencies", []),
            after.get("project", {}).get("dependencies", []),
        ),
        ("development", before.get("dependency-groups", {}), after.get("dependency-groups", {})),
        ("python", before.get("project", {}).get("requires-python"), after.get("project", {}).get("requires-python")),
    ):
        if old != new:
            changes[label] = {"before": old, "after": new}
    return changes


def dependency_declarations(manifest: dict) -> dict[str, list[str]]:
    """Group declared requirements by normalized package name, ignoring group moves."""
    project = manifest.get("project", {})
    groups = [project.get("dependencies", [])]
    groups.extend(project.get("optional-dependencies", {}).values())
    groups.extend(manifest.get("dependency-groups", {}).values())
    declarations = {}
    for group in groups:
        for requirement in group:
            if not isinstance(requirement, str):  # uv dependency-group includes are not packages.
                continue
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement.strip())
            if not match:
                raise NotesError("Cannot read a dependency declaration from pyproject.toml.")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            declarations.setdefault(name, set()).add(match[2].strip())
    return {name: sorted(values) for name, values in declarations.items()}


def inno_setup_version(workflow: str) -> str | None:
    """Read the declared compiler minimum from the Inno Setup installation step."""
    step = re.search(r"(?im)^([ \t]*)-\s+name:[^\n]*Inno Setup[^\n]*\n", workflow)
    if not step:
        return None
    body = workflow[step.end() :]
    next_step = re.search(rf"(?m)^{re.escape(step[1])}-\s+", body)
    if next_step:
        body = body[: next_step.start()]
    version = re.search(r"\$targetVersion\s*=\s*\[Version\]\s*['\"](\d+(?:\.\d+){1,3})['\"]", body, re.I)
    if not version:
        return None
    # Windows spells the same minimum as either 6.7.1 or 6.7.1.0.
    value = version[1]
    return value.removesuffix(".0") if value.count(".") == 3 else value


def read_dependency_requirements(sha: str | None, cwd: Path = ROOT) -> dict[str, list[str]]:
    """Read package and build-tool requirements from one committed snapshot."""
    files = (
        set(git("ls-tree", "--name-only", "-r", sha, "--", *DEPENDENCY_FILES, cwd=cwd).splitlines()) if sha else set()
    )
    values = dependency_declarations(
        tomllib.loads(git("show", f"{sha}:pyproject.toml", cwd=cwd)) if "pyproject.toml" in files else {}
    )
    if ".python-version" in files:
        version = git("show", f"{sha}:.python-version", cwd=cwd).strip()
        if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
            values["Python"] = [f"=={version}"]
    if ".github/workflows/ci.yml" in files:
        version = inno_setup_version(git("show", f"{sha}:.github/workflows/ci.yml", cwd=cwd))
        if version:
            values["Inno Setup"] = [f"=={version}"]
    return values


def pinned_dependency_version(values: list[str]) -> str | None:
    """Extract an exact version when one requirement pins the package."""
    match = re.fullmatch(r"==\s*([^\s;*]+)\s*(?:;.*)?", values[0]) if len(values) == 1 else None
    return match[1] if match else None


def format_dependency_note(name: str, old: list[str], new: list[str], sha: str) -> dict:
    """Describe one requirement change and retain its exact Git source."""
    version = pinned_dependency_version(new)
    if name == "Inno Setup":
        text = f"Set Inno Setup minimum to {version}." if new else "Remove the Inno Setup version requirement."
    elif not old:
        text = f"Add {name}{' ' + version if version else ''}."
    elif not new:
        text = f"Remove {name}."
    elif version and version != pinned_dependency_version(old):
        text = f"Bump {name} to {version}."
    else:
        text = f"Update {name} requirement."
    path = {"Inno Setup": ".github/workflows/ci.yml", "Python": ".python-version"}.get(name, "pyproject.toml")
    return {"text": text, "evidence": [path], "commits": [sha]}


def collect_dependency_notes(base_sha: str, target_sha: str, cwd: Path = ROOT) -> list[dict]:
    """Attribute final requirement changes to their integration on the target's first-parent history."""

    @cache
    def requirements(sha):
        return read_dependency_requirements(sha, cwd)

    before, after = requirements(base_sha), requirements(target_sha)
    pending = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    sources = {}
    history = git("rev-list", "--first-parent", f"{base_sha}..{target_sha}", "--", *DEPENDENCY_FILES, cwd=cwd)
    for sha in history.splitlines():
        if not pending:
            break
        parents = git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()[1:]
        old, new = requirements(parents[0] if parents else None), requirements(sha)
        for name in list(pending):
            if old.get(name) != new.get(name) and new.get(name) == after.get(name):
                sources[name] = sha
                pending.remove(name)
    if pending:
        raise NotesError(f"Cannot locate dependency changes in the release history: {', '.join(sorted(pending))}.")

    return [
        format_dependency_note(name, before.get(name, []), after.get(name, []), sources[name])
        for name in sorted(sources)
    ]


def add_dependency_notes(notes: dict, context: dict) -> dict:
    """Keep only model highlights; the dependency section comes exclusively from Git."""
    return {"highlights": notes["highlights"], "dependencies": context["dependency_notes"]}


def collect_commits(base_sha: str, target_sha: str, paths: list[str], cwd: Path = ROOT) -> list[dict]:
    """Collect citation candidates without authors or extra GitHub API requests."""
    history = git(
        "log",
        "--no-merges",
        "--format=%x1e%H%x00%s%x00",
        "--name-only",
        "-z",
        f"{base_sha}..{target_sha}",
        "--",
        cwd=cwd,
    )
    commits = []
    for record in history.split("\x1e"):
        if not record:
            continue
        sha, subject, filenames = record.split("\0", 2)
        files = [p for p in filenames.lstrip("\0\n").split("\0") if p in paths]
        if files:
            commits.append({"sha": sha, "subject": subject, "files": files})
    return commits


def collect_project_context(target_sha: str, cwd: Path = ROOT) -> dict:
    """Build an overview from the target snapshot without executing code or maintaining a second project manual."""
    project = tomllib.loads(git("show", f"{target_sha}:pyproject.toml", cwd=cwd))["project"]
    files = git("ls-tree", "-r", "--name-only", target_sha, "--", "src", "main.py", "README.md", cwd=cwd).splitlines()
    modules = []
    for path in files:
        if not path.endswith(".py") or path == "src/_version.py":
            continue
        module = ast.parse(git("show", f"{target_sha}:{path}", cwd=cwd))
        summary = (ast.get_docstring(module) or "").split("\n\n", 1)[0]
        symbols = [
            node.name
            for node in module.body
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            )
        ]
        modules.append({"path": path, "summary": summary, "public_symbols": symbols})
    return {
        "metadata": {
            key: project[key] for key in ("name", "description", "version", "requires-python") if key in project
        },
        "readme": git("show", f"{target_sha}:README.md", cwd=cwd) if "README.md" in files else "",
        "source_modules": modules,
    }


def collect_context(base: str, target: str, repository: str, cwd: Path = ROOT) -> dict:
    """Collect the net committed application diff, manifest, and supporting commit subjects."""
    base_sha, target_sha = resolve_ref(base, cwd), resolve_ref(target, cwd)
    if base_sha == target_sha:
        raise NotesError("The baseline and target resolve to the same commit.")
    if base_sha not in git("rev-list", target_sha, cwd=cwd).splitlines():
        raise NotesError("The baseline must be an ancestor of the target.")
    paths = git("diff", "--name-only", "-z", "--no-renames", base_sha, target_sha, "--", cwd=cwd).split("\0")
    # Only a known set of project paths enters the model input; never include .env, keys, or arbitrary files.
    paths = [
        p
        for p in paths
        if p
        and (
            is_app_source(p)
            or p in INTERNAL_PATHS
            or p.startswith("tests/")
            and p.endswith(".py")
            or p in {"pyproject.toml", ".python-version", "uv.lock", "README.md", "CONTRIBUTING.md"}
            or p.startswith(".github/workflows/")
            and p.endswith((".yml", ".yaml"))
        )
    ]
    patch_paths = [p for p in paths if is_app_source(p)]
    patch = (
        git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--unified=3",
            base_sha,
            target_sha,
            "--",
            *patch_paths,
            cwd=cwd,
        )
        if patch_paths
        else ""
    )
    dependency_notes = collect_dependency_notes(base_sha, target_sha, cwd)
    commits = collect_commits(base_sha, target_sha, paths, cwd)
    # Dependency citations can identify the PR merge that integrated the exact requirement.
    known = {commit["sha"]: commit for commit in commits}
    for item in dependency_notes:
        sha = item["commits"][0]
        if sha not in known:
            commit = {"sha": sha, "subject": git("show", "-s", "--format=%s", sha, cwd=cwd).strip(), "files": []}
            commits.append(commit)
            known[sha] = commit
        known[sha]["files"] = sorted(set(known[sha]["files"]) | set(item["evidence"]))
    context = {
        "repository": repository_name(repository),
        "from": base,
        "to": target,
        "from_sha": base_sha,
        "to_sha": target_sha,
        "changed_files": paths,
        "application_diff": patch,
        "dependencies": dependency_changes(base_sha, target_sha, cwd),
        "dependency_notes": dependency_notes,
        "commits": commits,
        "project_context": collect_project_context(target_sha, cwd),
        # Include unchanged UI guards and mode definitions that diff hunks can omit.
        "behavior_context": {
            path: git("show", f"{target_sha}:{path}", cwd=cwd)
            for path in git(
                "ls-tree", "-r", "--name-only", target_sha, "--", *BEHAVIOR_CONTEXT_PATHS, cwd=cwd
            ).splitlines()
        },
    }
    encode_input(context)  # Fail before making an API request; never silently truncate evidence.
    return context


def encode_input(context: dict) -> str:
    """Bound the complete prompt in UTF-8 bytes, conservatively bounding token consumption too."""
    payload = json.dumps(context, ensure_ascii=False, indent=2)
    size = (
        len(payload.encode("utf-8"))
        + len(PROMPT_PATH.read_bytes())
        + len(json.dumps(output_schema(context)).encode("utf-8"))
    )
    if size > MAX_INPUT_BYTES:
        raise NotesError(
            f"Input is {size:,} bytes; limit is {MAX_INPUT_BYTES:,}. Review a smaller explicit release range."
        )
    return payload


def output_schema(context: dict | None = None) -> dict:
    """Require short, evidenced bullets while leaving Markdown formatting to Python."""
    evidence_item = {"type": "string"}
    commit_item = {"type": "string"}
    if context is not None:
        if context["changed_files"]:
            evidence_item["enum"] = context["changed_files"]
        if context["commits"]:
            commit_item["enum"] = [commit["sha"] for commit in context["commits"]]
    bullet = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "evidence", "commits"],
        "properties": {
            "text": {"type": "string", "minLength": 1},
            "evidence": {"type": "array", "minItems": 1, "maxItems": 5, "items": evidence_item},
            "commits": {"type": "array", "minItems": 1, "maxItems": 2, "items": commit_item},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["highlights"],
        "properties": {
            "highlights": {"type": "array", "items": bullet},
        },
    }


def validate_bullet_text(text: str) -> None:
    """Require plain-text output without injected formatting or links."""
    if (
        not isinstance(text, str)
        or not text.strip()
        or text != text.strip()
        or re.search(r"[\x00-\x1f\x7f<>#`\[\]*_]|https?://|@", text)
    ):
        raise NotesError("Bullets must be non-empty plain-text sentences without links or attribution.")


def validate_bullet_evidence(evidence: list[str], section: str, changed_files: list[str]) -> None:
    """Require changed-file evidence and application evidence for user-facing claims."""
    if (
        not isinstance(evidence, list)
        or not 1 <= len(evidence) <= 5
        or any(not isinstance(p, str) or p not in changed_files for p in evidence)
    ):
        raise NotesError("A bullet cites a file outside the release diff.")
    if section == "highlights" and not any(is_app_source(p) for p in evidence):
        raise NotesError("A highlight needs evidence from application changes.")


def validate_bullet_commits(cited: list[str], commits: dict[str, set[str]], evidence: list[str]) -> None:
    """Require distinct release commits that touch the bullet's evidence files."""
    if (
        not isinstance(cited, list)
        or not 1 <= len(cited) <= 2
        or any(not isinstance(sha, str) or sha not in commits for sha in cited)
    ):
        raise NotesError("A bullet must cite one or two commits from the release range.")
    if len(set(cited)) != len(cited) or any(not commits[sha].intersection(evidence) for sha in cited):
        raise NotesError("Cited commits must be distinct and touch a bullet's evidence files.")


def validate_notes(notes: dict, context: dict) -> dict:
    """Reject malformed or unsupported output before replacing a notes file."""
    if not isinstance(notes, dict) or set(notes) != {"highlights", "dependencies"}:
        raise NotesError("Notes must contain highlights and dependencies arrays.")
    seen = set()
    commits = {commit["sha"]: set(commit["files"]) for commit in context["commits"]}
    for section in ("highlights", "dependencies"):
        items = notes[section]
        if not isinstance(items, list):
            raise NotesError(f"{section} must be an array.")
        for item in items:
            if not isinstance(item, dict) or set(item) != {"text", "evidence", "commits"}:
                raise NotesError("Each bullet must contain text, evidence, and commits.")
            text, evidence = item["text"], item["evidence"]
            validate_bullet_text(text)
            validate_bullet_evidence(evidence, section, context["changed_files"])
            validate_bullet_commits(item["commits"], commits, evidence)
            if text.casefold() in seen:
                raise NotesError("Model output contains duplicate bullets.")
            seen.add(text.casefold())
    if notes["dependencies"] != context["dependency_notes"]:
        raise NotesError("Dependency updates must match the changes derived from Git.")
    return notes


def generate(context: dict, model: str, client=None, *, reasoning_effort: str = "low") -> tuple[dict, dict, dict]:
    """Make one Responses API request with no tools or automatic retries."""
    from openai import OpenAI, OpenAIError

    payload = encode_input(context)
    if model not in MODELS:
        raise NotesError("Choose one of the supported release-note models.")
    if client is None and not os.environ.get("OPENAI_API_KEY"):
        raise NotesError("Set OPENAI_API_KEY, or use uv run --env-file .env python generate_release_notes.py ...")
    try:
        api = client or OpenAI(base_url="https://api.openai.com/v1", max_retries=0, timeout=90)
        response = api.responses.create(
            model=model,
            instructions=PROMPT_PATH.read_text(encoding="utf-8"),
            input=payload,
            reasoning={"effort": reasoning_effort},
            service_tier="default",
            store=False,
            text={
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "release_notes",
                    "strict": True,
                    "schema": output_schema(context),
                },
            },
        )
    except OpenAIError as exc:
        # Exception bodies may contain request data. Keep errors useful without echoing secrets or repository content.
        status = getattr(exc, "status_code", None)
        raise NotesError(
            f"OpenAI request failed ({type(exc).__name__}, HTTP {status or 'unavailable'}). No notes written."
        ) from None
    if response.status != "completed" or not response.output_text:
        raise NotesError("OpenAI returned an incomplete, refused, or empty response. No notes written.")
    try:
        output = json.loads(response.output_text)
        if not isinstance(output, dict) or set(output) != {"highlights"}:
            raise NotesError("Model output must contain only highlights.")
        notes = validate_notes(add_dependency_notes(output, context), context)
    except ValueError, TypeError:
        raise NotesError("OpenAI returned invalid JSON. No notes written.") from None
    usage = response.usage.model_dump() if response.usage else {}
    receipt = {
        "response_id": response.id,
        "response_model": response.model,
        "service_tier": response.service_tier,
        "reasoning_effort": reasoning_effort,
    }
    return notes, usage, receipt


def resolve_pull_requests(context: dict, notes: dict, cwd: Path = ROOT) -> dict[str, int]:
    """Prefer verified merged PRs for cited commits; fall back to commits if lookup is unavailable."""
    selected = sorted({sha for section in notes.values() for item in section for sha in item["commits"]})
    if not selected:
        return {}
    release_commits = set(git("rev-list", f"{context['from_sha']}..{context['to_sha']}", cwd=cwd).splitlines())
    mapping = {}
    failures = 0
    for sha in selected:
        try:
            response = requests.get(
                f"https://api.github.com/repos/{context['repository']}/commits/{sha}/pulls",
                headers=github_headers(),
                params={"per_page": 100},
                timeout=10,
            )
            response.raise_for_status()
            pulls = response.json()
            if not isinstance(pulls, list) or len(pulls) >= 100 or any(not isinstance(p, dict) for p in pulls):
                raise ValueError("Invalid or incomplete PR response")
        except requests.RequestException, ValueError:
            failures += 1
            continue
        candidates = [
            p
            for p in pulls
            if (
                isinstance(p.get("merged_at"), str)
                and p["merged_at"]
                and type(p.get("number")) is int
                and p["number"] > 0
                and isinstance(p.get("merge_commit_sha"), str)
                and p["merge_commit_sha"] in release_commits
                and isinstance(p.get("base"), dict)
                and isinstance(p["base"].get("repo"), dict)
                and p["base"]["repo"].get("full_name") == context["repository"]
            )
        ]
        if candidates:
            mapping[sha] = min(candidates, key=lambda p: p["number"])["number"]
    if failures:
        print(f"PR lookup unavailable for {failures} cited commit(s); using commit links for those sources.")
    return mapping


def render_notes(notes: dict, context: dict) -> str:
    """Render fixed headings and a deterministic comparison footer without exposing evidence metadata."""

    def render_bullet(item: dict) -> str:
        links = []
        for sha in item["commits"]:
            number = context.get("pull_requests", {}).get(sha)
            link = (
                f"[#{number}](https://github.com/{context['repository']}/pull/{number})"
                if number
                else f"[{sha[:7]}](https://github.com/{context['repository']}/commit/{sha})"
            )
            if link not in links:
                links.append(link)
        return f"- {item['text']} ({', '.join(links)})"

    lines = ["## Highlights", ""]
    lines += [render_bullet(item) for item in notes["highlights"]] or ["- No user-facing changes in this release."]
    lines += ["", "## Dependency updates", ""]
    lines += [render_bullet(item) for item in context["dependency_notes"]] or ["- No dependency updates."]
    base = quote(context["from"] if TAG_PATTERN.fullmatch(context["from"]) else context["from_sha"], safe="")
    target = quote(context["to"] if TAG_PATTERN.fullmatch(context["to"]) else context["to_sha"], safe="")
    url = f"https://github.com/{context['repository']}/compare/{base}...{target}"
    return "\n".join([*lines, "", f"**Full Changelog**: [{context['from']}…{context['to']}]({url})", ""])


def main(argv: list[str] | None = None) -> int:
    """Prepare an inspectable input or write validated local notes; never tag, build, or publish."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="base", help="Baseline ref; otherwise select a published stable GitHub release")
    parser.add_argument(
        "--to", default="HEAD", help="Target ref (default: committed HEAD; excludes working-tree edits)"
    )
    parser.add_argument("--repository", help="GitHub owner/repository (default: origin)")
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", default="low", help="OpenAI reasoning effort (default: low)")
    parser.add_argument("--output", type=Path, default=ROOT / "local_dev" / "release_notes.md")
    parser.add_argument(
        "--dry-run", action="store_true", help="Write .input.json beside the output; do not contact OpenAI"
    )
    args = parser.parse_args(argv)
    try:
        verify_ci_release(args.to)
        repository = repository_name(args.repository) if args.repository else infer_repository()
        target_sha = resolve_ref(args.to)
        target_version = "v" + tomllib.loads(git("show", f"{target_sha}:pyproject.toml"))["project"]["version"]
        base = args.base or previous_release(github_releases(repository), target_version, target_sha)
        context = collect_context(base, args.to, repository)
        payload = encode_input(context)
        print(
            f"Release range: {base} -> {args.to}; input: {len(payload.encode('utf-8')):,} bytes; "
            f"model: {args.model}; reasoning: {args.reasoning_effort}"
        )
        if args.dry_run:
            destination = args.output.with_suffix(".input.json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(payload + "\n", encoding="utf-8")
            print(f"Input saved to {destination}. No OpenAI request made.")
            return 0
        notes, usage, receipt = generate(context, args.model, reasoning_effort=args.reasoning_effort)
        print(f"OpenAI response: {receipt['response_id']}")
        context["pull_requests"] = resolve_pull_requests(context, notes)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".evidence.json").write_text(
            json.dumps(
                {
                    "from": base,
                    "to": args.to,
                    "from_sha": context["from_sha"],
                    "to_sha": context["to_sha"],
                    "model": args.model,
                    "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                    "usage": usage,
                    "api_receipt": receipt,
                    "notes": notes,
                    "dependency_notes": context["dependency_notes"],
                    "pull_requests": context["pull_requests"],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        args.output.write_text(render_notes(notes, context), encoding="utf-8")
        print(f"Notes saved to {args.output}; usage: {json.dumps(usage)}")
        return 0
    except (NotesError, OSError, subprocess.TimeoutExpired, tomllib.TOMLDecodeError) as exc:
        print(f"Release notes failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
