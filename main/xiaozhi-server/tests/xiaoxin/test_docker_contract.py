from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]


def test_server_image_builds_its_base_locally_without_private_registry():
    dockerfile = (ROOT / "Dockerfile-server").read_text(encoding="utf-8")

    assert "FROM python:3.10-slim AS server-base" in dockerfile
    assert "ghcr.io/jiang-source-tech" not in dockerfile
    assert "ARG DEBIAN_MIRROR=" in dockerfile
    assert "ARG DEBIAN_SECURITY_MIRROR=" in dockerfile
    assert "/etc/apt/sources.list.d/debian.sources" in dockerfile
    assert '[ -f "$source_file" ] || continue' in dockerfile
    assert "ARG PYTORCH_CPU_INDEX_URL=" in dockerfile
    assert 'if [ "$arch" = "amd64" ]' in dockerfile
    assert "COPY main/xiaozhi-server/requirements.txt ." in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert "FROM server-base AS server" in dockerfile


def test_compose_build_does_not_require_legacy_version_or_private_base_image():
    for filename in ("docker-compose.yml",):
        compose = (ROOT / "main" / "xiaozhi-server" / filename).read_text(
            encoding="utf-8"
        )

        assert not compose.startswith("version:")
        assert "SERVER_BASE_IMAGE" not in compose
        assert "DEBIAN_MIRROR:" in compose
        assert "DEBIAN_SECURITY_MIRROR:" in compose


def test_server_base_prefers_cpu_pytorch_on_amd64():
    dockerfile = (ROOT / "Dockerfile-server-base").read_text(encoding="utf-8")

    assert "ARG DEBIAN_MIRROR=" in dockerfile
    assert "ARG DEBIAN_SECURITY_MIRROR=" in dockerfile
    assert "/etc/apt/sources.list.d/debian.sources" in dockerfile
    assert '[ -f "$source_file" ] || continue' in dockerfile
    assert (
        "ARG PYTORCH_CPU_INDEX_URL="
        "https://download.pytorch.org/whl/cpu"
    ) in dockerfile
    assert 'if [ "$arch" = "amd64" ]' in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert "torch-2.2.2%2Bcpu-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert "torchaudio-2.2.2%2Bcpu-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert dockerfile.index("torch-2.2.2%2Bcpu") < dockerfile.index(
        "pip install --no-cache-dir -r requirements.txt"
    )


def test_voiceprint_cpu_image_is_pinned_and_excludes_cuda_pytorch():
    dockerfile = (ROOT / "Dockerfile-voiceprint-cpu").read_text(encoding="utf-8")

    commit_match = re.search(r"ARG VOICEPRINT_COMMIT=([0-9a-f]{40})", dockerfile)
    assert commit_match
    assert commit_match.group(1) == "b2020836947d82d0ab8dc1b2562e8ded1ab17916"
    assert "ARG DEBIAN_MIRROR=" in dockerfile
    assert "ARG DEBIAN_SECURITY_MIRROR=" in dockerfile
    assert dockerfile.count("/etc/apt/sources.list.d/debian.sources") == 2
    assert dockerfile.count('[ -f "$source_file" ] || continue') == 2
    assert (
        "ARG PYTORCH_CPU_INDEX_URL="
        "https://download.pytorch.org/whl/cpu"
    ) in dockerfile
    assert "ARG PYPI_INDEX_URL=https://pypi.org/simple" in dockerfile
    assert "ARG PYARROW_VERSION=20.0.0" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert "torch-2.2.2%2Bcpu-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert "torchaudio-2.2.2%2Bcpu-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert '"pyarrow==$PYARROW_VERSION" -r requirements.txt' in dockerfile
    assert "COPY --from=source /src /app" in dockerfile
    assert 'CMD ["python", "start_server.py"]' in dockerfile
