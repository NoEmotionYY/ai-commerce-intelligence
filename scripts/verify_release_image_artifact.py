from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ARCHIVE_NAME = "commerce-v2-rc-image.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"
IMAGE_ID_NAME = "production-image-id.txt"
REVISION_NAME = "production-source-revision.txt"
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class ArtifactIdentity:
    archive_sha256: str
    config_digest: str
    image_ref: str
    manifest_digest: str
    revision: str


def _read_small_text(path: Path, *, maximum_bytes: int = 4096) -> str:
    if not path.is_file() or path.stat().st_size > maximum_bytes:
        raise RuntimeError(f"missing or oversized release metadata: {path.name}")
    return path.read_text(encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_bytes(
    archive: tarfile.TarFile,
    members_by_name: dict[str, list[tarfile.TarInfo]],
    name: str,
    *,
    maximum_bytes: int,
) -> bytes:
    matches = members_by_name.get(name, [])
    if len(matches) != 1 or not matches[0].isfile() or matches[0].size > maximum_bytes:
        raise RuntimeError(f"release archive must contain one bounded regular file: {name}")
    extracted = archive.extractfile(matches[0])
    if extracted is None:
        raise RuntimeError(f"release archive member is unreadable: {name}")
    value = extracted.read(maximum_bytes + 1)
    if len(value) > maximum_bytes:
        raise RuntimeError(f"release archive member is oversized: {name}")
    return value


def _json_object(value: bytes, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"release archive contains invalid JSON: {name}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"release archive JSON must be an object: {name}")
    return parsed


def verify_artifact(artifact_dir: Path, expected_revision: str) -> ArtifactIdentity:
    artifact_dir = artifact_dir.resolve(strict=True)
    if not REVISION_PATTERN.fullmatch(expected_revision):
        raise RuntimeError("expected revision must be a 40-character lowercase Git SHA")

    archive_path = artifact_dir / ARCHIVE_NAME
    if not archive_path.is_file():
        raise RuntimeError(f"release archive is missing: {archive_path}")
    checksum_text = _read_small_text(artifact_dir / CHECKSUM_NAME)
    checksum_match = re.fullmatch(
        rf"([0-9a-f]{{64}})  {re.escape(ARCHIVE_NAME)}(?:\r?\n)?", checksum_text
    )
    if checksum_match is None:
        raise RuntimeError("release archive checksum manifest is malformed")
    archive_sha256 = _sha256_file(archive_path)
    if archive_sha256 != checksum_match.group(1):
        raise RuntimeError("release archive checksum mismatch")

    revision = _read_small_text(artifact_dir / REVISION_NAME).strip()
    config_digest = _read_small_text(artifact_dir / IMAGE_ID_NAME).strip()
    if revision != expected_revision:
        raise RuntimeError("release artifact source revision mismatch")
    if SHA256_PATTERN.fullmatch(config_digest) is None:
        raise RuntimeError("release artifact config digest is malformed")

    image_ref = f"commerce-v2-rc:{revision}"
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members_by_name: dict[str, list[tarfile.TarInfo]] = {}
        for member in archive.getmembers():
            members_by_name.setdefault(member.name, []).append(member)

        legacy_manifest_bytes = _member_bytes(
            archive, members_by_name, "manifest.json", maximum_bytes=1024 * 1024
        )
        try:
            legacy_manifest = json.loads(legacy_manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("release archive contains invalid JSON: manifest.json") from exc
        if not isinstance(legacy_manifest, list) or len(legacy_manifest) != 1:
            raise RuntimeError("release archive must contain exactly one image manifest")
        image_manifest = legacy_manifest[0]
        if not isinstance(image_manifest, dict):
            raise RuntimeError("release image manifest must be an object")
        config_name = f"blobs/sha256/{config_digest.removeprefix('sha256:')}"
        if image_manifest.get("Config") != config_name:
            raise RuntimeError("release image manifest does not reference the recorded config")
        if image_manifest.get("RepoTags") != [image_ref]:
            raise RuntimeError("release image manifest contains an unexpected image reference")

        config_bytes = _member_bytes(
            archive, members_by_name, config_name, maximum_bytes=16 * 1024 * 1024
        )
        actual_config_digest = f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"
        if actual_config_digest != config_digest:
            raise RuntimeError("release image config blob digest mismatch")
        config = _json_object(config_bytes, config_name)
        labels = config.get("config", {}).get("Labels", {})
        if (
            not isinstance(labels, dict)
            or labels.get("org.opencontainers.image.revision") != revision
        ):
            raise RuntimeError("release image config revision label mismatch")

        index = _json_object(
            _member_bytes(archive, members_by_name, "index.json", maximum_bytes=1024 * 1024),
            "index.json",
        )
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise RuntimeError("release archive OCI index must contain exactly one manifest")
        descriptor = manifests[0]
        if not isinstance(descriptor, dict):
            raise RuntimeError("release archive OCI descriptor must be an object")
        manifest_digest = descriptor.get("digest")
        if (
            not isinstance(manifest_digest, str)
            or SHA256_PATTERN.fullmatch(manifest_digest) is None
        ):
            raise RuntimeError("release archive OCI manifest digest is malformed")
        manifest_name = f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}"
        manifest_bytes = _member_bytes(
            archive, members_by_name, manifest_name, maximum_bytes=16 * 1024 * 1024
        )
        if f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}" != manifest_digest:
            raise RuntimeError("release archive OCI manifest blob digest mismatch")
        oci_manifest = _json_object(manifest_bytes, manifest_name)
        oci_config = oci_manifest.get("config")
        if not isinstance(oci_config, dict) or oci_config.get("digest") != config_digest:
            raise RuntimeError("release archive OCI manifest config digest mismatch")

    return ArtifactIdentity(
        archive_sha256=archive_sha256,
        config_digest=config_digest,
        image_ref=image_ref,
        manifest_digest=manifest_digest,
        revision=revision,
    )


def verify_loaded_image(image: dict[str, Any], identity: ArtifactIdentity) -> None:
    labels = image.get("Config", {}).get("Labels", {})
    if (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != identity.revision
    ):
        raise RuntimeError("loaded image revision label mismatch")
    tags = image.get("RepoTags")
    if not isinstance(tags, list) or identity.image_ref not in tags:
        raise RuntimeError("loaded image does not retain the expected reference")

    actual_id = image.get("Id")
    descriptor = image.get("Descriptor")
    descriptor_digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
    classic_identity = actual_id == identity.config_digest
    containerd_identity = (
        actual_id == identity.manifest_digest and descriptor_digest == identity.manifest_digest
    )
    if not classic_identity and not containerd_identity:
        raise RuntimeError("loaded image identity does not match the verified archive")


def load_and_verify(artifact_dir: Path, identity: ArtifactIdentity) -> None:
    subprocess.run(
        ["docker", "image", "load", "--input", str(artifact_dir / ARCHIVE_NAME)], check=True
    )
    inspected = subprocess.run(
        ["docker", "image", "inspect", identity.image_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(inspected.stdout)
    if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
        raise RuntimeError("docker returned an unexpected image inspection result")
    verify_loaded_image(parsed[0], identity)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and optionally load a scanned RC image")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()

    identity = verify_artifact(args.artifact_dir, args.expected_revision)
    if args.load:
        load_and_verify(args.artifact_dir.resolve(), identity)
    print(json.dumps(asdict(identity), sort_keys=True))


if __name__ == "__main__":
    main()
