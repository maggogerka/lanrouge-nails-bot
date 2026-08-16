from pathlib import Path

WORKFLOW = Path(".github/workflows/release-images.yml")


def test_release_images_require_an_existing_version_tag_and_package_write_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "packages: write" in text
    assert "contents: read" in text
    assert "ref: refs/tags/${{ inputs.release_tag }}" in text
    assert "git cat-file -t" in text
    assert "git merge-base --is-ancestor" in text
    assert "Refuse to overwrite published image tags" in text


def test_release_images_publish_only_versioned_runtime_and_backup_targets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "target: runtime" in text
    assert "target: backup" in text
    assert text.count("push: true") == 2
    assert text.count(":${{ env.RELEASE_TAG }}") == 2
    assert ":latest" not in text
    assert ":main" not in text
    assert "steps.bot.outputs.digest" in text
    assert "steps.backup.outputs.digest" in text
    assert "image-digests.txt" in text


def test_release_workflow_actions_are_pinned_to_commit_shas() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    action_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("uses:")]
    assert action_lines
    for line in action_lines:
        reference = line.split("@", maxsplit=1)[1].split(maxsplit=1)[0]
        assert len(reference) == 40
        assert all(character in "0123456789abcdef" for character in reference)
