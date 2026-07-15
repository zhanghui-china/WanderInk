#!/usr/bin/env python3
"""把"编剧大师"(hermes-agent)接入指定环节的 LLM 配置(默认 S0/S1),写进
~/shanhai/config.json 的按环节覆盖——和 Web 配置面板点保存效果完全一样,只是
可重复执行、能进 git(脚本本身不含真实密钥)。

背景:hermes-agent 对结构化请求(JSON Schema + "只输出 JSON"指令)会直接执行,
不会触发它自己的"编剧大师"反问式对话;S0/S1 现有的 system prompt 已经足够
构成这种强约束,LLMClient 不需要任何改动即可直接对接(纯 OpenAI 兼容协议)。
详见 docs/deploy-dgx.md "S0/S1 接入编剧大师(hermes-agent)"一节。

用法(须在仓库根目录执行,让 config.json 落在正确位置):
    HERMES_AGENT_API_KEY=xxx uv run python scripts/setup-hermes-agent.py
    uv run python scripts/setup-hermes-agent.py --api-key xxx --stages s0,s1
    uv run python scripts/setup-hermes-agent.py --remove          # 切回继承全局默认
"""
import argparse
import os
import sys

from shanhai.runtime_config import AppConfig, ConfigOverride, apply_put, update_overrides

DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
DEFAULT_MODEL = "hermes-agent"
DEFAULT_TIMEOUT = 600.0
DEFAULT_STAGES = ("s0", "s1")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--stages", default=",".join(DEFAULT_STAGES),
                    help="逗号分隔的环节列表,默认 s0,s1")
    p.add_argument("--api-key", default=os.getenv("HERMES_AGENT_API_KEY"),
                    help="不传则读环境变量 HERMES_AGENT_API_KEY;--remove 模式下不需要")
    p.add_argument("--remove", action="store_true",
                    help="清空这些环节的 LLM 覆盖,恢复继承全局默认,而不是设置")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    if not stages:
        print("错误: --stages 不能为空", file=sys.stderr)
        return 1

    if args.remove:
        # 必须显式传 None(而非空的 ConfigOverride()),merge_override 只认
        # "显式传入构造函数"的字段(model_fields_set);裸的 ConfigOverride()
        # 等于什么都没传,会被当成"不改动、保留旧值",而不是"清除"。
        override = ConfigOverride(llm_base_url=None, llm_api_key=None,
                                   llm_model=None, llm_timeout=None)
    else:
        if not args.api_key:
            print("错误: 缺少 API Key(--api-key 或环境变量 HERMES_AGENT_API_KEY)",
                  file=sys.stderr)
            return 1
        override = ConfigOverride(llm_base_url=args.base_url, llm_api_key=args.api_key,
                                   llm_model=args.model, llm_timeout=args.timeout)

    incoming = AppConfig(stages={st: override for st in stages})
    new = update_overrides(lambda existing: apply_put(existing, incoming))

    print(f"{'已清除' if args.remove else '已设置'}以下环节的 LLM 覆盖:")
    for st in stages:
        ov = new.stages.get(st)
        if ov is None:
            print(f"  {st}: 无覆盖(继承全局默认)")
        else:
            print(f"  {st}: base_url={ov.llm_base_url} model={ov.llm_model} "
                  f"timeout={ov.llm_timeout} key_set={ov.llm_api_key is not None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
