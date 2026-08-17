from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.verify_release_image_artifact import (
    ARCHIVE_NAME,
    ArtifactIdentity,
    verify_artifact,
    verify_loaded_image,
)

REVISION = "1" * 40


def add_bytes(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    archive.addfile(info, io.BytesIO(value))


def write_artifact(directory: Path) -> ArtifactIdentity:
    image_ref = f"commerce-v2-rc:{REVISION}"
    config = json.dumps(
        {"config": {"Labels": {"org.opencontainers.image.revision": REVISION}}},
        separators=(",", ":"),
    ).encode()
    config_digest = f"sha256:{hashlib.sha256(config).hexdigest()}"
    config_name = f"blobs/sha256/{config_digest.removeprefix('sha256:')}"
    oci_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": config_digest, "size": len(config)},
            "layers": [],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = f"sha256:{hashlib.sha256(oci_manifest).hexdigest()}"
    manifest_name = f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}"
    index = json.dumps(
        {"schemaVersion": 2, "manifests": [{"digest": manifest_digest}]},
        separators=(",", ":"),
    ).encode()
    legacy_manifest = json.dumps(
        [{"Config": config_name, "RepoTags": [image_ref], "Layers": []}],
        separators=(",", ":"),
    ).encode()

    archive_path = directory / ARCHIVE_NAME
    with tarfile.open(archive_path, mode="w:gz") as archive:
        add_bytes(archive, "manifest.json", legacy_manifest)
        add_bytes(archive, "index.json", index)
        add_bytes(archive, config_name, config)
        add_bytes(archive, manifest_name, oci_manifest)

    archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (directory / f"{ARCHIVE_NAME}.sha256").write_text(
        f"{archive_sha256}  {ARCHIVE_NAME}\n", encoding="utf-8"
    )
    (directory / "production-image-id.txt").write_text(f"{config_digest}\n", encoding="utf-8")
    (directory / "production-source-revision.txt").write_text(f"{REVISION}\n", encoding="utf-8")
    return ArtifactIdentity(
        archive_sha256=archive_sha256,
        config_digest=config_digest,
        image_ref=image_ref,
        manifest_digest=manifest_digest,
        revision=REVISION,
    )


def test_release_artifact_binds_config_manifest_and_revision(tmp_path: Path) -> None:
    expected = write_artifact(tmp_path)

    assert verify_artifact(tmp_path, REVISION) == expected


def test_release_artifact_rejects_a_different_recorded_config(tmp_path: Path) -> None:
    write_artifact(tmp_path)
    (tmp_path / "production-image-id.txt").write_text(f"sha256:{'0' * 64}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not reference the recorded config"):
        verify_artifact(tmp_path, REVISION)


def test_loaded_image_accepts_only_bound_docker_store_identities(tmp_path: Path) -> None:
    identity = write_artifact(tmp_path)
    common = {
        "RepoTags": [identity.image_ref],
        "Config": {"Labels": {"org.opencontainers.image.revision": REVISION}},
    }

    verify_loaded_image({**common, "Id": identity.config_digest}, identity)
    verify_loaded_image(
        {
            **common,
            "Id": identity.manifest_digest,
            "Descriptor": {"digest": identity.manifest_digest},
        },
        identity,
    )
    with pytest.raises(RuntimeError, match="identity does not match"):
        verify_loaded_image({**common, "Id": f"sha256:{'f' * 64}"}, identity)
