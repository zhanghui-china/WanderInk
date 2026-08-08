#!/usr/bin/env python3
"""核对我们发出去的斜杠命令,与 hermes 上**当前已装**的 skill 名是否还对得上。

为什么需要这个脚本:斜杠命令写错**不会报错**。hermes 把不认识的 `/xxx` 当普通文本吞掉,
照常用一个普通 LLM 回答——产出仍然合法、状态仍是 done、日志一个字都没有。2026-08-08 实测
发现 S1 的 `/screenwriter-master` 早已不匹配任何已装 skill(对方把它改名成
`screenwriting-master`),编剧大师因此长期空转:用户勾了开关、付了 hermes 的时间成本,
拿到的却是普通生成。单测测不出这件事(它只能验证前缀被拼上去了),只有对着线上比才知道。

用法(在 DGX 上跑,或本机能连到 hermes 时):
    uv run python scripts/check-hermes-skills.py
    uv run python scripts/check-hermes-skills.py --base-url http://127.0.0.1:8642/v1 --api-key XXX

不传参时从 config.json 的 stages 覆盖里自动找 hermes 的地址与密钥。
退出码:0 = 全部对得上;1 = 有对不上的(可挂进定期巡检)。
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

# 我们代码里真正会发出去的斜杠命令。改了 s1/s2 的常量,这里也要跟着改——
# 两处不同步的话这个脚本就成了摆设,那正是它要防的事。
SLASH_COMMANDS = {
    "S1 编剧大师": "screenwriting-master",
    "S2 导演大师": "director-master",
}


def _from_config() -> tuple[str, str]:
    """从 config.json 的 stages 层找 hermes 的 base_url / api_key(哪个环节配了就用哪个)。"""
    path = Path(__file__).resolve().parents[1] / "config.json"
    if not path.is_file():
        return "", ""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for ov in (cfg.get("stages") or {}).values():
        url = (ov or {}).get("llm_base_url") or ""
        if (ov or {}).get("llm_model") == "hermes-agent" and url:
            return url, (ov or {}).get("llm_api_key") or ""
    return "", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key", default="")
    a = ap.parse_args()

    base, key = a.base_url, a.api_key
    if not base:
        base, key = _from_config()
    if not base:
        print("✗ 没找到 hermes 地址:config.json 的 stages 里没有 llm_model=hermes-agent 的条目,"
              "也没传 --base-url")
        return 1

    url = base.rstrip("/") + "/skills"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as f:
            installed = {s.get("name") for s in (json.load(f).get("data") or [])}
    except Exception as e:  # noqa: BLE001 —— 巡检脚本,任何失败都要说清楚而不是抛栈
        print(f"✗ 取 {url} 失败:{type(e).__name__}: {e}")
        return 1

    print(f"hermes {base} 上已装 {len(installed)} 个 skill\n")
    bad = []
    for label, name in SLASH_COMMANDS.items():
        ok = name in installed
        print(f"  {'✓' if ok else '✗'} {label:14s} /{name}")
        if not ok:
            bad.append((label, name))

    if bad:
        print("\n⚠️ 下列斜杠命令在 hermes 上**已不存在**,对应 skill 正在静默空转:")
        for label, name in bad:
            near = sorted(n for n in installed if n and (n[:6] in name or name[:6] in n))
            print(f"  {label}: /{name}" + (f"   （名字相近的:{', '.join(near)}）" if near else ""))
        print("\n改法:src/shanhai/steps/s1_script.py 的 SKILL_PREFIX / "
              "s2_storyboard.py 的 DIRECTOR_PREFIX,以及本脚本的 SLASH_COMMANDS。")
        return 1
    print("\n全部对得上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
