"""Command-line entry point for the demo agent."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from rs_agent.agent import run_image_inspection_agent
from rs_agent.harness import Harness
from rs_agent.llm import LLMClient, LLMConfig, LLMError, load_env_file, resolve_config
from rs_agent.profiles import (
    DEFAULT_PROFILES_FILE,
    Profile,
    ProfileError,
    ProfileStore,
)
from rs_agent.tools import RasterInspectionError, inspect_raster

_QUIT_WORDS = {"/exit", "/quit", "/q", "exit", "quit"}

_PROTOCOLS: tuple[tuple[str, str], ...] = (
    ("chat", "chat/completions（OpenAI Chat Completions 线格式）"),
    ("responses", "responses（OpenAI Responses API 线格式）"),
)

_KNOWN_MODELS: tuple[tuple[str, str], ...] = (
    ("deepseek-flash", "deepseek-flash（默认，支持工具与视觉）"),
    ("deepseek-v4-pro", "deepseek-v4-pro（更强推理）"),
)

_THINKING_CHOICES: tuple[tuple[str, str], ...] = (
    ("disabled", "关闭思考（稳定、输出快）"),
    ("low", "低强度思考"),
    ("high", "高强度思考"),
    ("max", "最高强度思考"),
)

_CUSTOM_MODEL = "__custom__"
_CIRCLED = "①②③④⑤⑥"

_HELP_TEXT = """可用斜杠命令：
  /help                 显示本帮助
  /profiles             列出已保存的配置档案
  /config               显示当前生效配置（key 脱敏）
  /use <name>           切换到指定档案（保留当前对话上下文）
  /save <name> [ENV]    把当前配置保存为档案；key 只记录环境变量名（默认自动探测）
  /delete <name>        删除档案
  /exit                 退出"""


def _ensure_utf8_streams() -> None:
    """Use UTF-8 for piped/redirected I/O on Windows consoles."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                continue
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - defensive
            continue


def _positive_int(text: str) -> int:
    """Argparse type for integers that must be at least 1."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the small command-line interface."""
    parser = argparse.ArgumentParser(prog="python -m rs_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="inspect one image with the scripted agent"
    )
    inspect_parser.add_argument("image_path", help="path to an image file")
    inspect_raster_parser = subparsers.add_parser(
        "inspect-raster", help="inspect low-cost raster metadata with gdalinfo"
    )
    inspect_raster_parser.add_argument("raster_path", help="path to a raster file")
    chat_parser = subparsers.add_parser(
        "chat",
        help="talk to a DeepSeek/OpenAI-compatible model with the inspection tools",
    )
    chat_parser.add_argument(
        "prompt",
        nargs="*",
        help="one-shot prompt; omit it to start an interactive session",
    )
    chat_parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL (default: https://api.deepseek.com, or RS_AGENT_BASE_URL)",
    )
    chat_parser.add_argument(
        "--api-key",
        default=None,
        help="API key (default: RS_AGENT_API_KEY or DEEPSEEK_API_KEY)",
    )
    chat_parser.add_argument(
        "--model",
        default=None,
        help="model id (default: deepseek-flash, or RS_AGENT_MODEL)",
    )
    chat_parser.add_argument(
        "--api",
        choices=("chat", "responses"),
        default=None,
        help="wire format: chat/completions or responses (default: chat)",
    )
    profile_group = chat_parser.add_mutually_exclusive_group()
    profile_group.add_argument(
        "--profile",
        default=None,
        help="use a saved configuration profile by name",
    )
    profile_group.add_argument(
        "--no-profile",
        action="store_true",
        help="ignore saved profiles and use .env / flags only",
    )
    chat_parser.add_argument(
        "--profiles-file",
        default=DEFAULT_PROFILES_FILE,
        help=f"profile store path (default: {DEFAULT_PROFILES_FILE})",
    )
    chat_parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=8,
        help="maximum tool-calling rounds per user turn (default: 8)",
    )
    chat_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="cap for generated tokens (max_tokens / max_output_tokens)",
    )
    chat_parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature (ignored in thinking mode)",
    )
    chat_parser.add_argument(
        "--thinking",
        choices=("enabled", "disabled"),
        default=None,
        help=(
            "DeepSeek thinking mode (default: disabled, or RS_AGENT_THINKING / .env)"
        ),
    )
    chat_parser.add_argument(
        "--reasoning-effort",
        choices=("low", "high", "max"),
        default=None,
        help="thinking effort when --thinking enabled",
    )
    chat_parser.add_argument(
        "--env-file",
        default=".env",
        help="local .env file with the API key and base URL (default: .env)",
    )
    chat_parser.add_argument(
        "--no-menu",
        action="store_true",
        help="skip the interactive profile/protocol/model/thinking menu",
    )
    chat_parser.add_argument(
        "--verbose",
        action="store_true",
        help="print token usage to stderr",
    )
    return parser


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def _profile_api_key(
    profile: Profile | None,
    args: argparse.Namespace,
    *,
    required: bool = True,
) -> str | None:
    """Resolve a profile's key from env/``.env``; literal keys are supported.

    ``required=False`` is used while collecting menu defaults, where the key is
    not needed yet: a stale ``last_profile`` must not block the profile picker.
    """
    if profile is None:
        return None
    if profile.api_key:
        return profile.api_key
    if profile.api_key_env:
        value = os.environ.get(profile.api_key_env)
        if not value:
            value = load_env_file(args.env_file).get(profile.api_key_env)
        if not value:
            if not required:
                return None
            raise ValueError(
                f"档案 {profile.name!r} 需要环境变量或 .env 中的 "
                f"{profile.api_key_env}，当前没有找到"
            )
        return value
    return None


def _build_config(
    args: argparse.Namespace,
    profile: Profile | None = None,
    *,
    require_profile_key: bool = True,
) -> LLMConfig:
    """Resolve the effective config: flags > profile > env/.env > defaults."""
    base_url = args.base_url or (profile.base_url if profile else None)
    model = args.model or (profile.model if profile else None)
    api = args.api or (profile.api if profile else None)
    thinking = (
        args.thinking
        if args.thinking is not None
        else (profile.thinking if profile else None)
    )
    reasoning_effort = (
        args.reasoning_effort
        if args.reasoning_effort is not None
        else (profile.reasoning_effort if profile else None)
    )
    api_key = args.api_key or _profile_api_key(
        profile, args, required=require_profile_key
    )
    return resolve_config(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api=api,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        env=None,
        env_file=args.env_file,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )


def _detect_api_key_env(
    args: argparse.Namespace, profile: Profile | None = None
) -> str | None:
    """Find the env var name that currently provides the API key."""
    candidates: list[str] = []
    if profile is not None and profile.api_key_env:
        candidates.append(profile.api_key_env)
    candidates.extend(["RS_AGENT_API_KEY", "DEEPSEEK_API_KEY"])
    dotenv = load_env_file(args.env_file)
    for name in candidates:
        if os.environ.get(name) or dotenv.get(name):
            return name
    return None


def _key_source(args: argparse.Namespace, profile: Profile | None = None) -> str:
    if args.api_key:
        return "命令行参数"
    if profile is not None and profile.api_key:
        return f"档案 {profile.name}"
    if profile is not None and profile.api_key_env:
        return f"档案 {profile.name} → {profile.api_key_env}"
    if os.environ.get("RS_AGENT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"):
        return "环境变量"
    dotenv = load_env_file(args.env_file)
    if dotenv.get("RS_AGENT_API_KEY") or dotenv.get("DEEPSEEK_API_KEY"):
        return os.path.basename(args.env_file) or args.env_file
    return "未配置"


def _print_banner(
    config: LLMConfig,
    args: argparse.Namespace,
    profile: Profile | None = None,
) -> None:
    thinking = config.thinking or "default"
    if config.thinking == "enabled" and config.reasoning_effort:
        thinking = f"enabled({config.reasoning_effort})"
    key = "sk-****" if config.api_key else "未配置"
    profile_label = profile.name if profile is not None else "(none)"
    print(
        f"endpoint={config.base_url} model={config.model} api={config.api} "
        f"thinking={thinking}",
        file=sys.stderr,
    )
    print(
        f"key={key}（来源: {_key_source(args, profile)}） profile={profile_label} "
        f"env_file={args.env_file}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Interactive selection menu
# ---------------------------------------------------------------------------


def _read_answer(prompt: str, default: str) -> str:
    """Write the prompt to stderr and read one reply from stdin."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if line == "":
        return default
    return line.strip() or default


def _choose(
    label: str, options: Sequence[tuple[str, str]], default_value: str
) -> str:
    """Ask the user to pick one numbered option, retrying on bad input."""
    while True:
        print(f"\n{label}", file=sys.stderr)
        for index, (_value, description) in enumerate(options, 1):
            print(f"  {index}) {description}", file=sys.stderr)
        default_index = next(
            (
                index
                for index, (value, _description) in enumerate(options, 1)
                if value == default_value
            ),
            1,
        )
        raw = _read_answer(f"请选择 [{default_index}]: ", str(default_index))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(f"输入无效，请输入 1-{len(options)}。", file=sys.stderr)


def _choose_profile(
    store: ProfileStore, active_name: str | None
) -> str | None:
    options: list[tuple[str, str]] = [
        ("", "临时配置（.env / 命令行参数，不写入档案）")
    ]
    for name in store.names():
        profile = store.get(name)
        if profile is None:  # pragma: no cover - defensive
            continue
        options.append(
            (
                name,
                f"{name}（{profile.model or '默认模型'} / {profile.api or '默认协议'}）",
            )
        )
    selected = _choose(f"{_CIRCLED[0]} 配置档案", options, active_name or "")
    return selected or None


def _choose_model(label: str, config: LLMConfig) -> str:
    options = list(_KNOWN_MODELS)
    if config.model not in {value for value, _ in _KNOWN_MODELS}:
        options.insert(0, (config.model, f"{config.model}（当前配置）"))
    options.append((_CUSTOM_MODEL, "自定义模型 id"))
    selected = _choose(label, options, config.model)
    if selected != _CUSTOM_MODEL:
        return selected
    return (
        _read_answer(f"请输入模型 id [{config.model}]: ", config.model)
        or config.model
    )


def _choose_thinking(label: str, config: LLMConfig) -> tuple[str, str | None]:
    current = "disabled"
    if config.thinking == "enabled":
        current = config.reasoning_effort or "high"
    selected = _choose(label, _THINKING_CHOICES, current)
    if selected == "disabled":
        return "disabled", None
    return "enabled", selected


def _interactive_menu(
    config: LLMConfig,
    *,
    store: ProfileStore | None = None,
    active_name: str | None = None,
    build_config=None,
) -> dict[str, str | None]:
    """Ask the user to pick a profile, protocol, model id and thinking effort."""
    print("\n=== rs-agent 交互配置（直接回车使用默认值）===", file=sys.stderr)
    profile_name = active_name
    profile: Profile | None = None
    has_profiles = store is not None and bool(store.names())
    if has_profiles:
        profile_name = _choose_profile(store, active_name)
        profile = store.get(profile_name) if profile_name else None
        # Rebuild defaults for the selected profile *and* for the temporary
        # option (profile=None -> .env/CLI defaults), otherwise choosing
        # "temporary" would keep showing the previously active profile.
        if build_config is not None:
            config = build_config(profile)

    offset = 2 if has_profiles else 1
    api = _choose(f"{_CIRCLED[offset - 1]} 接口协议", _PROTOCOLS, config.api)
    model = _choose_model(f"{_CIRCLED[offset]} 模型", config)
    thinking, effort = _choose_thinking(
        f"{_CIRCLED[offset + 1]} 思考强度", config
    )
    print(file=sys.stderr)
    return {
        "profile": profile_name,
        "api": api,
        "model": model,
        "thinking": thinking,
        "reasoning_effort": effort,
    }


# ---------------------------------------------------------------------------
# Interactive REPL with slash commands
# ---------------------------------------------------------------------------


@dataclass
class ChatState:
    """Mutable state of one interactive chat session."""

    args: argparse.Namespace
    store: ProfileStore
    profile_name: str | None
    config: LLMConfig
    harness: Harness
    extra: dict = field(default_factory=dict)

    @property
    def profile(self) -> Profile | None:
        return self.store.get(self.profile_name) if self.profile_name else None


def _args_without_menu_overrides(
    args: argparse.Namespace, cli_overrides: dict[str, str | None]
) -> argparse.Namespace:
    """Copy args, restoring the true CLI values of menu-controlled fields.

    Menu answers are stored on ``args`` for the current session, but they must
    not survive as apparent CLI overrides when a later ``/use`` selects a
    different profile.  Only values the user actually passed on the command
    line stay authoritative.
    """
    clone = argparse.Namespace(**vars(args))
    clone.api = cli_overrides.get("api")
    clone.model = cli_overrides.get("model")
    clone.thinking = cli_overrides.get("thinking")
    clone.reasoning_effort = cli_overrides.get("reasoning_effort")
    return clone


def _rebuild_session(state: ChatState, profile_name: str | None) -> None:
    """Rebuild the client/harness for a newly selected profile, keeping history."""
    profile = state.store.get(profile_name) if profile_name else None
    rebuild_args = _args_without_menu_overrides(
        state.args, state.extra.get("cli_overrides", {})
    )
    config = _build_config(rebuild_args, profile)
    harness = Harness(
        LLMClient(config),
        max_steps=state.args.max_steps,
        verbose=state.args.verbose,
        stdout=state.harness.stdout,
        stderr=state.harness.stderr,
        messages=list(state.harness.messages),
    )
    state.profile_name = profile_name
    state.config = config
    state.harness = harness
    _print_banner(config, state.args, profile)


def _print_config(state: ChatState) -> None:
    config = state.config
    thinking = config.thinking or "default"
    if config.thinking == "enabled" and config.reasoning_effort:
        thinking = f"enabled({config.reasoning_effort})"
    print(
        f"profile={state.profile_name or '(none)'} "
        f"endpoint={config.base_url} model={config.model} api={config.api} "
        f"thinking={thinking}",
        file=sys.stderr,
    )
    key = "sk-****" if config.api_key else "未配置"
    print(
        f"key={key}（来源: {_key_source(state.args, state.profile)}） "
        f"profiles_file={state.store.path}",
        file=sys.stderr,
    )


def _handle_slash(line: str, state: ChatState) -> bool:
    """Handle one slash command. Returns False when the REPL should exit."""
    parts = line.split()
    command = parts[0].lower()
    argument = parts[1] if len(parts) > 1 else None

    if command in _QUIT_WORDS:
        return False
    if command == "/help":
        print(_HELP_TEXT, file=sys.stderr)
    elif command == "/profiles":
        if not state.store.names():
            print(
                "还没有保存的配置档案；用 /save <name> 保存当前配置。",
                file=sys.stderr,
            )
        else:
            print("已保存的配置档案：", file=sys.stderr)
            for name in state.store.names():
                profile = state.store.get(name)
                marker = "*" if name == state.profile_name else " "
                details = (
                    f"{profile.model or '默认模型'} / "
                    f"{profile.api or '默认协议'} / "
                    f"{profile.base_url or '默认端点'}"
                    if profile is not None
                    else ""
                )
                print(f" {marker} {name}  ({details})", file=sys.stderr)
    elif command == "/config":
        _print_config(state)
    elif command == "/use":
        if not argument:
            print("用法: /use <name>", file=sys.stderr)
            return True
        if state.store.get(argument) is None:
            print(f"档案不存在: {argument}（可用 /profiles 查看）", file=sys.stderr)
            return True
        _rebuild_session(state, argument)
        state.store.set_last(argument)
        print(f"已切换到档案 {argument}。", file=sys.stderr)
    elif command == "/save":
        if not argument:
            print("用法: /save <name> [ENV_VAR_NAME]", file=sys.stderr)
            return True
        env_override = parts[2] if len(parts) > 2 else None
        api_key_env = env_override or _detect_api_key_env(state.args, state.profile)
        if state.args.api_key and not env_override:
            print(
                "提示: 本次 key 来自 --api-key 命令行参数，档案不会保存明文 key；"
                "请把 key 放进 .env 并用 ENV 参数指定变量名，或直接使用环境变量名。",
                file=sys.stderr,
            )
        try:
            profile = Profile.from_config(
                argument, state.config, api_key_env=api_key_env
            )
        except ProfileError as exc:
            print(f"档案无效: {exc}", file=sys.stderr)
            return True
        state.store.upsert(profile)
        state.profile_name = argument
        state.store.set_last(argument)
        key_note = f"key 引用 {api_key_env}" if api_key_env else "未记录 key 引用"
        print(
            f"已保存档案 {argument}（{key_note}）。之后可用 /use {argument} 或 "
            f"--profile {argument} 使用。",
            file=sys.stderr,
        )
    elif command == "/delete":
        if not argument:
            print("用法: /delete <name>", file=sys.stderr)
            return True
        if not state.store.delete(argument):
            print(f"档案不存在: {argument}", file=sys.stderr)
            return True
        if state.profile_name == argument:
            state.profile_name = None
        print(f"已删除档案 {argument}。", file=sys.stderr)
    else:
        print(
            f"未知命令: {command}（输入 /help 查看可用命令）", file=sys.stderr
        )
    return True


def _chat_repl(state: ChatState) -> int:
    print(
        "输入提示词开始对话；/help 查看命令，/exit 退出。", file=sys.stderr
    )
    while True:
        sys.stderr.write(">>> ")
        sys.stderr.flush()
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            print(file=sys.stderr)
            break
        if line == "":
            print(file=sys.stderr)
            break
        line = line.strip()
        if not line:
            continue
        if line in _QUIT_WORDS:
            break
        if line.startswith("/"):
            try:
                if not _handle_slash(line, state):
                    break
            except (ValueError, ProfileError) as exc:
                print(f"命令失败: {exc}", file=sys.stderr)
            continue
        try:
            state.harness.run_turn(line)
        except KeyboardInterrupt:
            print("\n已中断本轮。", file=sys.stderr)
            continue
        except LLMError as exc:
            print(f"请求失败: {exc}", file=sys.stderr)
            continue
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_chat(args: argparse.Namespace) -> int:
    _ensure_utf8_streams()
    try:
        store = ProfileStore(args.profiles_file)
    except ProfileError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    profile_name: str | None = None
    if not args.no_profile:
        if args.profile:
            if store.get(args.profile) is None:
                print(
                    f"配置错误: 档案不存在: {args.profile}（可用 /profiles 查看，"
                    f"或先 /save 保存）",
                    file=sys.stderr,
                )
                return 2
            profile_name = args.profile
        elif store.last_profile:
            profile_name = store.last_profile

    one_shot = " ".join(args.prompt).strip()
    show_menu = not one_shot and not args.no_menu

    try:
        profile = store.get(profile_name) if profile_name else None
        # When the profile picker will run, a stale key reference must not block
        # it: menu defaults do not need the credential, so resolve tolerantly.
        config = _build_config(args, profile, require_profile_key=not show_menu)
    except (ValueError, ProfileError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    cli_overrides: dict[str, str | None] = {
        "api": args.api,
        "model": args.model,
        "thinking": args.thinking,
        "reasoning_effort": args.reasoning_effort,
    }
    if show_menu:
        try:
            selection = _interactive_menu(
                config,
                store=None if args.no_profile else store,
                active_name=None if args.no_profile else profile_name,
                build_config=lambda chosen: _build_config(
                    args, chosen, require_profile_key=False
                ),
            )
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        except (ValueError, ProfileError) as exc:
            print(f"配置错误: {exc}", file=sys.stderr)
            return 2
        profile_name = None if args.no_profile else selection["profile"]
        profile = store.get(profile_name) if profile_name else None
        args.api = selection["api"]
        args.model = selection["model"]
        args.thinking = selection["thinking"]
        args.reasoning_effort = selection["reasoning_effort"]
        try:
            config = _build_config(args, profile)
        except (ValueError, ProfileError) as exc:
            print(f"配置错误: {exc}", file=sys.stderr)
            return 2
        if not args.no_profile:
            store.set_last(profile_name)

    if args.profile and not args.no_profile:
        store.set_last(profile_name)

    _print_banner(config, args, profile)
    harness = Harness(
        LLMClient(config), max_steps=args.max_steps, verbose=args.verbose
    )

    if one_shot:
        try:
            harness.run_turn(one_shot)
        except LLMError as exc:
            print(f"请求失败: {exc}", file=sys.stderr)
            return 1
        return 0

    state = ChatState(
        args=args,
        store=store,
        profile_name=profile_name,
        config=config,
        harness=harness,
        extra={"cli_overrides": cli_overrides},
    )
    return _chat_repl(state)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested command and return a process exit code."""
    args = build_parser().parse_args(argv)

    if args.command == "inspect":
        task = f"Inspect image metadata and per-band statistics for {args.image_path}."
        result = run_image_inspection_agent(task, args.image_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1

    if args.command == "inspect-raster":
        try:
            result = inspect_raster(args.raster_path)
        except RasterInspectionError as exc:
            result = {"ok": False, "error": str(exc)}
            exit_code = 1
        else:
            exit_code = 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code

    if args.command == "chat":
        return _run_chat(args)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
