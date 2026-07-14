# 0005 · 接本地 TTS(Qwen3-TTS / mlx-audio)

- **日期**:2026-07-09
- **结论**:成功。本地 Qwen3-TTS 经 OpenAI 兼容接口接入,产出有声成片(`projects/0d0d494c/output/final.mp4`,真实中文女声,max_volume −1.4 dB vs 静音版 −91 dB)。**未改任何业务代码**——TTSClient 本就是 OpenAI 兼容,只配 `.env`。

## 本地 TTS 是什么

- 仓库:`~/Work/qwen3-tts-apple-silicon`(Qwen3-TTS,Apple Silicon MLX 版)。
- 模型(已下载):`models/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`(预设音色)、`.../Base-8bit`(克隆)。1.7B 未下载。
- 中文音色:Vivian(女)/ Serena(女)/ Uncle_Fu(男·说书感)/ Dylan / Eric。
- 服务:依赖 `mlx-audio` 自带的 `mlx_audio.server`,暴露 OpenAI 兼容 `POST /v1/audio/speech`,默认 `localhost:8000`,本地免 key。

## 修复与启动(venv 曾因 Homebrew Python 3.13→3.14 升级而全部悬空)

```bash
R=~/Work/qwen3-tts-apple-silicon
# 1) 重建 venv(原环境是 3.13)
uv python install 3.13
uv venv "$R/.venv_shanhai" --python 3.13
uv pip install --python "$R/.venv_shanhai/bin/python" -r "$R/requirements.txt"
# 2) 补 server 依赖(requirements.txt 只含推理依赖,不含 server)
uv pip install --python "$R/.venv_shanhai/bin/python" uvicorn fastapi python-multipart websockets webrtcvad soundfile "setuptools<80"
#    注:setuptools<80 —— 新版移除了 webrtcvad 需要的 pkg_resources
# 3) 启动服务器(cwd 需在仓库根,models/ 在其下)
cd "$R" && "$R/.venv_shanhai/bin/python" -m mlx_audio.server   # localhost:8000
```

## 接入配置(shanhai/.env,不动图像/LLM 的 tu-zi 配置)

```
SHANHAI_TTS_BASE_URL=http://localhost:8000/v1
SHANHAI_TTS_MODEL=/Users/nativeas/Work/qwen3-tts-apple-silicon/models/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit
SHANHAI_TTS_VOICE=Vivian
```

`response_format=mp3` 本地 server 支持,直接返回 `audio/mp3`。首次请求会加载模型(~30-60s),之后每句几秒。

## 用法

- 服务器要先起来。**没起也不会崩**:S5 的静音兜底会接管(成片完整但无解说)。
- 复用已生成画面出有声版(零生图成本):
  ```bash
  rm -f projects/<id>/audio/*.mp3     # 清掉旧音轨(静音兜底 or 换音色)
  uv run shanhai step <id> s5         # 本地 TTS 重配音
  uv run shanhai step <id> s6         # 重新合成
  ```
- 换音色:改 `SHANHAI_TTS_VOICE`(如 Uncle_Fu 说书感),重跑 s5+s6。

## 备注

- F5-TTS(`~/opt/f5-tts`)也在本机,但走 Gradio API(非 OpenAI 格式),接它需给 providers/tts.py 写适配器;声音克隆场景再考虑。
- BGM:`assets/bgm/manifest.json` 为空,当前无背景音乐;配乐需往清单加授权曲目。
