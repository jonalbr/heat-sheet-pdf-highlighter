"""Check that release publication depends on validated, bounded OpenAI notes."""

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def test_release_requires_generated_notes_and_preserves_existing_artifact_contract():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    notes = workflow.split("  generate_notes:\n", 1)[1].split("  release:\n", 1)[0]
    release = workflow.split("  release:\n", 1)[1]
    assert "needs: test" in notes
    assert "github.repository == 'jonalbr/heat-sheet-pdf-highlighter'" in notes
    assert "github.actor == 'jonalbr'" in notes
    assert "github.triggering_actor == 'jonalbr'" in notes
    assert "contents: read" in notes
    assert "pull-requests: read" in notes
    assert "contents: write" not in notes
    assert "models: read" not in workflow
    assert "continue-on-error" not in notes
    assert "secrets.OPENAI_API_KEY" in notes
    assert 'generate_release_notes.py --to "$RELEASE_TAG" --output release_notes.md' in notes
    assert "persist-credentials: false" in notes
    assert "fetch-depth: 0" in notes
    assert "uv sync --locked --group dev" in notes
    assert "release_notes.evidence.json" in notes
    assert "needs: [build_installer, generate_notes]" in release
    assert "generate_release_notes: false" in release
    assert "body_path: release_notes.md" in release
    assert "heat_sheet_pdf_highlighter_installer.exe.sha256" in release
    assert "models.github.ai" not in workflow
