# spike/consistency_test.py
"""M0 角色一致性验证:三视图 -> 以三视图为参考逐页生成,人工评分。
用法: uv run python spike/consistency_test.py [style ...](默认跑全部 3 种画风)
"""
import sys
from pathlib import Path

from shanhai.config import Settings
from shanhai.providers.image import ImageClient
from shanhai.steps.s3_characters import TURNAROUND_TMPL
from shanhai.styles import STYLE_PRESETS

CHARACTERS = {
    "白素贞": "年轻女性,白色古装长裙,黑色长发挽髻插一支银簪,眉目温婉,腰间系淡青色丝带",
    "许仙": "年轻男性,青色书生长衫,黑发束冠,面容清秀,手持一把折叠纸伞",
}

SCENES = [
    "西湖断桥上,两人初遇,烟雨朦胧,远景",
    "药铺内,许仙在柜台后抓药,白素贞立于门口,中景",
    "端午节庭院,白素贞面色苍白倚在桌边,近景",
    "金山寺前,白素贞立于波涛之上,神情坚定,全景",
    "雷峰塔下,许仙仰望高塔,黄昏逆光,中景",
    "多年后塔前重逢,两人对望,晨光温暖,中景",
]

PAGE_TMPL = (
    "{style}。连环画单页画面:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(发型、服饰、面部特征)。画面中不要出现任何文字。"
)


def main() -> None:
    s = Settings()
    base, key = s.image_endpoint
    client = ImageClient(base, key, s.image_model, s.image_api_mode)
    for style_key in sys.argv[1:] or list(STYLE_PRESETS):
        style = STYLE_PRESETS[style_key]
        out = Path("spike/out") / style_key
        out.mkdir(parents=True, exist_ok=True)
        refs: list[Path] = []
        for name, feature in CHARACTERS.items():
            p = out / f"turnaround_{name}.png"
            if not p.exists():
                p.write_bytes(client.generate(
                    TURNAROUND_TMPL.format(style=style, feature=feature), size=s.image_size))
            refs.append(p)
            print(f"[{style_key}] 三视图 {name} 完成")
        features = ";".join(f"{n}({f})" for n, f in CHARACTERS.items())
        for i, scene in enumerate(SCENES, 1):
            p = out / f"page_{i:02d}.png"
            if not p.exists():
                p.write_bytes(client.generate(
                    PAGE_TMPL.format(style=style, scene=scene, features=features),
                    size=s.image_size, references=refs))
            print(f"[{style_key}] 页面 {i}/{len(SCENES)} 完成")


if __name__ == "__main__":
    main()
