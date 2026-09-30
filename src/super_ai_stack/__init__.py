"""Super AI Stack - local Mixture-of-Experts stack behind one API."""

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version

DEFAULT_SERVICE_DIRS = (
    "gateway",
    "router",
    "llm_general",
    "llm_reasoning",
    "llm_coding",
    "vision",
    "speech",
    "image_gen",
    "memory",
    "agent",
    "model_manager",
)


def _version() -> str:
    try:
        return version("super-ai-stack")
    except PackageNotFoundError:
        return "0.0.0+local"


def _catalog() -> list[dict[str, str]]:
    """The MoE catalog, without requiring the service modules to import."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from common.constants import EXPERT_CATALOG

        return list(EXPERT_CATALOG)
    except Exception:  # noqa: BLE001 - the CLI must work from an install too
        return []


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sas",
        description="Super AI Stack - a local Mixture-of-Experts stack behind one API.",
    )
    parser.add_argument("--version", action="version", version=f"super-ai-stack {_version()}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("info", help="show the service layout and MoE catalog")
    sub.add_parser("env", help="list every environment variable the stack reads")

    run = sub.add_parser("run", help="print the uvicorn command for one service")
    run.add_argument("service", choices=DEFAULT_SERVICE_DIRS, help="service directory to run")

    check = sub.add_parser("check", help="check that the shared package imports cleanly")
    check.add_argument(
        "--import",
        dest="module",
        default="gateway.main",
        help="module to import (default: gateway.main)",
    )
    return parser


def _cmd_info() -> int:
    print(f"Super AI Stack {_version()}\n")
    print("Services (run from the repository root so 'common' resolves):")
    for name in DEFAULT_SERVICE_DIRS:
        marker = "ok " if os.path.isdir(name) else "MISSING"
        print(f"  [{marker}] {name}")
    catalog = _catalog()
    if catalog:
        print("\nLocal MoE catalog:")
        width = max(len(row["layer"]) for row in catalog)
        for row in catalog:
            print(f"  {row['layer']:<{width}}  {row['model']:<20} {row['purpose']}")
    return 0


def _cmd_env() -> int:
    variables = {
        "Service wiring": [
            "ROUTER_URL",
            "GENERAL_URL",
            "CODING_URL",
            "MEMORY_URL",
            "REASONING_URL",
            "VISION_URL",
            "SPEECH_URL",
            "IMAGE_URL",
            "AGENT_URL",
        ],
        "Inference": [
            "MODEL_BASE_URL",
            "MODEL_NAME",
            "MODEL_API_KEY",
            "MODEL_MAX_TOKENS",
            "MODEL_TIMEOUT",
            "MODEL_RETRIES",
            "MODEL_RETRY_DELAY",
            "MODEL_TEMPERATURE",
            "MODEL_ENSURE_TIMEOUT",
            "MODEL_MANAGER_URL",
            "OLLAMA_URL",
        ],
        "Routing": ["PHI3_URL", "ROUTER_MODEL_BASE_URL", "ROUTER_MODEL_NAME", "ROUTER_MAX_TOKENS", "ROUTER_TIMEOUT"],
        "Gateway": ["SUPER_AI_API_KEY", "RATE_LIMIT_PER_MINUTE", "MAX_TRACKED_CLIENTS", "LOG_LEVEL"],
        "Storage": ["SESSION_DB_PATH", "MEMORY_DATA_DIR", "MEMORY_MAX_DOCUMENTS", "AUDIT_LOG_PATH", "ALLOWED_TOOLS"],
        "Experts": [
            "IMAGE_MODEL_BASE_URL",
            "IMAGE_MODEL_NAME",
            "WHISPER_MODEL",
            "WHISPER_DEVICE",
            "WHISPER_COMPUTE_TYPE",
        ],
    }
    for group, names in variables.items():
        print(f"\n{group}:")
        for name in names:
            value = os.getenv(name)
            shown = value if value else "(unset)"
            # Never print a secret back out.
            if "KEY" in name or "SECRET" in name or "TOKEN" in name:
                shown = "(set)" if value else "(unset)"
            print(f"  {name:<24} {shown}")
    return 0


def _cmd_run(service: str) -> int:
    print(f"uvicorn {service}.main:app --reload --port ${{PORT:-8000}}")
    return 0


def _cmd_check(module: str) -> int:
    import importlib

    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - report, do not traceback at the user
        print(f"FAIL  {module}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"ok    {module}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = _build_parser().parse_args(argv)
    command = args.command or "info"
    if command == "info":
        return _cmd_info()
    if command == "env":
        return _cmd_env()
    if command == "run":
        return _cmd_run(args.service)
    if command == "check":
        return _cmd_check(args.module)
    return _build_parser().print_help()


if __name__ == "__main__":
    raise SystemExit(main())
