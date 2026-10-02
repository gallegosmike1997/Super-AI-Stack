"""Package the Super AI Stack as an offline, installable bundle.

An offline install cannot reach a registry or a model host, so everything it
needs travels in the bundle: the container images, the Ollama model weights,
and a Python wheelhouse. ``install.ps1``/``install.sh`` then load all three
offline and bring the stack up.

    python scripts/package_offline.py                 # slim profile
    python scripts/package_offline.py --profile full
    python scripts/package_offline.py --out dist --skip-docker --skip-wheels

Slim (default) ships the two models the stack can actually answer with on a
CPU-only host. Full adds the coding and reasoning weights.

Ollama has no "export" API that writes portable blobs: ``/api/create`` with
``from`` only records a reference back into the local store. The portable form
is the store layout itself, so this copies the manifests and the blobs those
manifests actually reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Two profiles. Sizes are the download weight of the weights alone; the bundle
# is larger because it also carries the images and the wheelhouse.
PROFILES: dict[str, list[str]] = {
    "slim": ["phi3:mini", "llama3.1"],
    "full": ["phi3:mini", "llama3.1", "qwen2.5-coder:7b", "deepseek-r1:8b"],
}

# Same images Compose builds. One Dockerfile serves every service (it takes the
# module name from the compose command), so a single image covers all of them.
IMAGE = "super-ai-stack:offline"

WHEELS = [
    "fastapi>=0.141.1",
    "httpx>=0.28.1",
    "numpy>=2.2.0",
    "faiss-cpu>=1.10.0",
    "uvicorn[standard]>=0.35.0",
    "pypdf>=5.0.0",
]


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is constructed, never shell
        args, check=True, capture_output=True, text=True, **kwargs
    )


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def ollama_store() -> Path:
    """Where this machine keeps model manifests and weight blobs."""
    override = os.getenv("OLLAMA_MODELS")
    if override:
        return Path(override)
    return Path.home() / ".ollama" / "models"


def installed_models(store: Path) -> set[str]:
    """Model tags present locally, read straight from the manifest tree."""
    manifests = store / "manifests" / "registry.ollama.ai" / "library"
    if not manifests.is_dir():
        return set()
    tags: set[str] = set()
    for path in manifests.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(manifests).parts
        if len(parts) == 2:
            tags.add(f"{parts[0]}:{parts[1]}")
        elif len(parts) == 1:
            tags.add(f"{parts[0]}:latest")
    return tags


def manifest_path(store: Path, tag: str) -> Path | None:
    """Locate a model's manifest, tolerating an omitted or implicit ``:latest``."""
    base = store / "manifests" / "registry.ollama.ai" / "library"
    name, _, variant = tag.partition(":")
    if not name:
        return None
    directory = base / name
    if not directory.is_dir():
        return None
    for candidate in ([variant] if variant else []) + ["latest"]:
        path = directory / candidate
        if path.is_file():
            return path
    # A bare tag directory with no variant file is a single-version model.
    files = [f for f in directory.iterdir() if f.is_file()]
    return files[0] if files else None


def manifest_blobs(store: Path, tag: str) -> set[str]:
    """Blob filenames one model's manifest references.

    Reading the manifest keeps the bundle honest: copying every blob would ship
    unrelated models too, which on a 16GB store is most of the payload.
    """
    manifest = manifest_path(store, tag)
    if manifest is None:
        return set()
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  ! could not read manifest for {tag}: {exc}")
        return set()

    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            digest = node.get("digest")
            # Ollama stores blobs as sha256-<hex>; the manifest says sha256:<hex>.
            if isinstance(digest, str) and node.get("mediaType"):
                algorithm, _, value = digest.partition(":")
                names.add(f"{algorithm}-{value}" if value else algorithm)
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return names


def export_models(store: Path, out: Path, tags: list[str]) -> list[str]:
    """Copy manifests plus the exact weight blobs they reference."""
    dest = out / "models"
    blobs = store / "blobs"
    exported: list[str] = []
    for tag in tags:
        manifest = manifest_path(store, tag)
        if manifest is None:
            print(f"  ! {tag} is not installed locally; skipping")
            continue
        relative = manifest.relative_to(store / "manifests")
        target = dest / "manifests" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, target)

        wanted = manifest_blobs(store, tag)
        missing = 0
        for name in wanted:
            source = blobs / name
            if not source.is_file():
                missing += 1
                continue
            (dest / "blobs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / "blobs" / name)
        if missing:
            print(f"  ! {tag}: {missing} blob(s) missing from the local store")
        exported.append(tag)
        weight = sum((blobs / n).stat().st_size for n in wanted if (blobs / n).is_file())
        print(f"  + {tag:<22} {len(wanted) - missing} blobs  {human(weight)}")
    return exported


def build_image(out: Path) -> bool:
    """Build the service image and save it as a tar the installer can load."""
    if not have("docker"):
        print("  ! docker not found - skipping image export")
        return False
    try:
        run(["docker", "build", "-f", str(ROOT / "infra" / "Dockerfile"), "-t", IMAGE, str(ROOT)])
    except subprocess.CalledProcessError as exc:
        print(f"  ! image build failed: {exc.stderr.strip()[:200]}")
        return False
    tarball = out / "images" / "super-ai-stack.tar"
    tarball.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["docker", "save", "-o", str(tarball), IMAGE])
    except subprocess.CalledProcessError as exc:
        print(f"  ! docker save failed: {exc.stderr.strip()[:200]}")
        return False
    print(f"  + {IMAGE} -> {human(tarball.stat().st_size)}")
    return True


def build_wheels(out: Path) -> bool:
    """Vendor the runtime dependencies so the target needs no package index."""
    wheelhouse = out / "wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse), *WHEELS],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"  ! wheel download failed: {exc.stderr.strip()[:200]}")
        return False
    count = len(list(wheelhouse.glob("*")))
    print(f"  + {count} wheels ({human(dir_size(wheelhouse))})")
    return True


def write_env(out: Path, models: list[str]) -> None:
    """A ready .env pointed at the bundled models, safe to copy in place."""
    env = out / "env" / ".env"
    env.parent.mkdir(parents=True, exist_ok=True)
    # The offline host has no host.docker.internal; the models travel in the
    # bundle and Ollama runs on the host, so loopback is correct here.
    lines = [
        "# Generated by scripts/package_offline.py - offline install profile",
        "OLLAMA_URL=http://127.0.0.1:11434",
        "ROUTER_MODEL_BASE_URL=http://127.0.0.1:11434",
        f"ROUTER_MODEL_NAME={models[0] if models else 'phi3:mini'}",
        "GENERAL_MODEL_BASE_URL=http://127.0.0.1:11434",
        "GENERAL_MODEL_NAME=llama3.1",
        "CODING_MODEL_BASE_URL=http://127.0.0.1:11434",
        "CODING_MODEL_NAME=qwen2.5-coder:7b",
        "REASONING_MODEL_BASE_URL=http://127.0.0.1:11434",
        # CPU-only hosts cannot hold an 8B reasoning model resident; phi3 keeps
        # the expert answering instead of timing out.
        "REASONING_MODEL_NAME=phi3:mini",
        "VISION_MODEL_BASE_URL=http://127.0.0.1:11434",
        "VISION_MODEL_NAME=qwen2.5vl:3b",
        "MODEL_TIMEOUT=300",
        "MODEL_MAX_TOKENS=1024",
        "LOG_LEVEL=INFO",
        "",
    ]
    env.write_text("\n".join(lines), encoding="utf-8")
    print(f"  + {env.name}")


INSTALL_POWERSHELL = r"""<#
  Super AI Stack - offline installer (Windows).

  Loads the bundled container image and model weights, writes the local .env,
  and starts the stack. Assumes Docker Desktop and Ollama are already
  installed and running; neither can be installed from this bundle.
#>
param(
  [string]$Bundle = $PSScriptRoot,
  [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "docker not found. Install Docker Desktop, start it, then re-run this script."
}

$imageTar = Join-Path $Bundle 'images\super-ai-stack.tar'
if (Test-Path $imageTar) {
  Step 'Loading container image'
  docker load -i $imageTar
} else {
  Write-Warning "No image tarball at $imageTar - using any existing local image."
}

$models = Join-Path $Bundle 'models'
if (Test-Path $models) {
  Step 'Installing offline model weights'
  if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "ollama not found. Install Ollama, start it, then re-run this script."
  }
  # The bundle carries the store layout (manifests + blobs); copying it into
  # place is what makes the models visible to a fresh Ollama install.
  $store = Join-Path $env:USERPROFILE '.ollama\models'
  New-Item -ItemType Directory -Force -Path $store | Out-Null
  Copy-Item -Path (Join-Path $models '*') -Destination $store -Recurse -Force
  Write-Host '    weights copied into the Ollama store' -ForegroundColor DarkGray
}

$envFile = Join-Path $Bundle 'env\.env'
$target = Join-Path $Bundle 'infra\.env'
if (Test-Path $envFile) {
  Step 'Writing .env'
  Copy-Item $envFile $target -Force
}

if (-not $NoStart) {
  Step 'Starting the stack'
  Push-Location (Join-Path $Bundle 'infra')
  docker compose up -d
  Pop-Location
  Write-Host ''
  Write-Host 'Console: http://127.0.0.1:8000/' -ForegroundColor Green
  Write-Host 'Stop with: docker compose -f infra/docker-compose.yml down' -ForegroundColor DarkGray
}
"""

INSTALL_SH = """#!/usr/bin/env bash
# Super AI Stack - offline installer (Linux/macOS).
#
# Loads the bundled container image and model weights, writes the local .env,
# and starts the stack. Requires Docker and Ollama to be installed already;
# neither can be installed from this bundle.
set -euo pipefail

BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
step() { printf '==> %s\\n' "$1"; }

command -v docker >/dev/null 2>&1 || {
  echo "docker not found. Install Docker, start it, then re-run this script." >&2
  exit 1
}

if [ -f "$BUNDLE/images/super-ai-stack.tar" ]; then
  step "Loading container image"
  docker load -i "$BUNDLE/images/super-ai-stack.tar"
else
  echo "warning: no image tarball; using any existing local image" >&2
fi

if [ -d "$BUNDLE/models" ]; then
  step "Installing offline model weights"
  command -v ollama >/dev/null 2>&1 || {
    echo "ollama not found. Install Ollama, start it, then re-run this script." >&2
    exit 1
  }
  # The bundle carries the store layout (manifests + blobs); copying it into
  # place is what makes the models visible to a fresh Ollama install.
  store="${OLLAMA_MODELS:-$HOME/.ollama/models}"
  mkdir -p "$store"
  cp -R "$BUNDLE/models/." "$store/"
  echo "    weights copied into the Ollama store"
fi

if [ -f "$BUNDLE/env/.env" ]; then
  step "Writing .env"
  cp "$BUNDLE/env/.env" "$BUNDLE/infra/.env"
fi

if [ "${1:-}" != "--no-start" ]; then
  step "Starting the stack"
  cd "$BUNDLE/infra"
  docker compose up -d
  printf '\\nConsole: http://127.0.0.1:8000/\\n'
  printf 'Stop with: docker compose -f infra/docker-compose.yml down\\n'
fi
"""


def write_installers(out: Path) -> None:
    ps1 = out / "install.ps1"
    ps1.write_text(INSTALL_POWERSHELL, encoding="utf-8")
    sh = out / "install.sh"
    sh.write_text(INSTALL_SH, encoding="utf-8")
    sh.chmod(0o755)
    print("  + install.ps1, install.sh")


README = """# Super AI Stack - offline bundle

Everything needed to install the stack with no internet access.

## Requirements (install these first)

- Docker, running
- Ollama, running

Neither can be installed from this bundle.

## Install

Windows (PowerShell):

    .\\install.ps1

Linux/macOS:

    ./install.sh

Add `-NoStart` / `--no-start` to load everything without starting the stack.

## Verify

    curl http://127.0.0.1:8000/health
    curl http://127.0.0.1:8000/api/diagnostics

The console is at http://127.0.0.1:8000/

## Stop

    docker compose -f infra/docker-compose.yml down

## Layout

    images/    container image tarball
    models/    Ollama store layout: manifests + weight blobs
    wheels/    Python dependencies, for a native run without the container
    env/       .env configured for this bundle
    infra/     docker-compose.yml and Dockerfile

## Notes

- Models are the bulk of the download. The reasoning expert is configured for
  `phi3:mini` because larger reasoning weights time out on CPU-only hosts.
- Data lives in named volumes (`memory_data`, `session_data`, `agent_audit`).
  `docker compose down -v` deletes it.
"""


def write_manifest(out: Path, models: list[str], image: bool, wheels: bool) -> None:
    """Record what is inside, so the target can verify before installing."""
    entries = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(out).as_posix()
        if relative == "MANIFEST.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    manifest = {
        "name": "super-ai-stack",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "models": models,
        "image_included": image,
        "wheels_included": wheels,
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"  + MANIFEST.json ({len(entries)} files)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="slim")
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--skip-docker", action="store_true", help="do not build/save the image")
    parser.add_argument("--skip-wheels", action="store_true", help="do not vendor Python deps")
    parser.add_argument("--skip-models", action="store_true", help="do not copy model weights")
    args = parser.parse_args(argv)

    wanted = PROFILES[args.profile]
    out = Path(args.out).resolve() / f"super-ai-stack-offline-{args.profile}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"packaging {out}\n")

    models: list[str] = []
    if not args.skip_models:
        print("models:")
        models = export_models(ollama_store(), out, wanted)
        if not models:
            print("  ! no models exported - the bundle will need network on first run")

    image = False
    if not args.skip_docker:
        print("images:")
        image = build_image(out)

    wheels = False
    if not args.skip_wheels:
        print("wheels:")
        wheels = build_wheels(out)

    print("bundle:")
    write_env(out, models)
    write_installers(out)
    for name in ("infra", "web", "common"):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, out / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            print(f"  + {name}/")
    (out / "README.md").write_text(README, encoding="utf-8")
    write_manifest(out, models, image, wheels)

    size = dir_size(out)
    print(f"\nbundle: {out}")
    print(f"size:   {human(size)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())