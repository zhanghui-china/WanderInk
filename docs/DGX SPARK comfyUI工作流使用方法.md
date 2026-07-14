一、激活环境

1.系统刷新：source ~/.bashrc
2.激活虚拟环境：conda activate comfyui
3.进入ComfyUI根目录：cd ~/ComfyUI


二、ComfyUI 服务后台管理（已配置 Systemd 用户服务，无需手动开启）

ComfyUI 已配置为系统用户级服务后台常驻运行，支持开机自启和崩溃自动恢复，无需使用 Tmux。

1. 查看后台运行情况/日志：
   journalctl --user -u comfyui -f

2. 如果发生卡死，重启服务：
   systemctl --user restart comfyui

3. 停止服务：
   systemctl --user stop comfyui

4. 启动服务：
   systemctl --user start comfyui

5. 查看服务当前运行状态：
   systemctl --user status comfyui

查看GPU算力情况：nvidia-smi



三、 端口映射
ssh -L 8188:127.0.0.1:8188 你的用户名@服务器IP -p 服务器SSH端口

四、ComfyUI  工作流
目前有五个功能
1、AI歌曲生成（ACE-STEP XL Turbo），对应执行脚本（在ComfyUI根目录下）generate_music_api.py，工作流 MusicCreation-ACESTEP1.5XL_api.json，结果输出路径./output_music/
2、TTS语音合成（QWEN3 TTS），对应执行脚本generate_tts_api.py，工作流VoiceDesign-QwenTTS.json，结果输出路径./output_tts/
3、单图编辑（QWEN EDIT 2511），对应执行脚本generate_edit_api.py，工作流image_edit_workflow.json，默认素材输入路径ComfyUI/input/，结果输出路径./output_edit/
4、双图编辑（QWEN EDIT 2511），对应执行脚本generate_blend_api.py，工作流image_blend_workflow.json，默认素材输入路径ComfyUI/input/，结果输出路径./output_blend/
5、三图编辑（QWEN EDIT 2511），对应执行脚本generate_triple_blend_api.py，工作流image_triple_blend_workflow.json，默认素材输入路径ComfyUI/input/，结果输出路径./output_triple_blend/

使用逻辑：
1、放素材，把图片放到ComfyUI/input/ 目录下
2、改参数，打开.py脚本，滑到最底部 if __name__ == "__main__": 区域，修改您的提示词、歌词或文件名，保存文件（Ctrl + S）
3、运行，在终端输入python 脚本名.py回车


模块底部参数修改规范

1、AI歌曲生成
my_lyrics: 填入您的歌词，可用 [Intro], [Verse], [Chorus], [Outro] 划分结构
my_style: 填入曲风控制词（如 "Epic J-Rock, Samurai Rock, Cinematic"）
my_duration: 歌曲长度，必须带小数点（如 60.0 代表一分钟）
my_bpm: 歌曲速度，整数（如 110）
my_timesignature: 节拍，可选 "2", "3", "4", "6"
my_language: 语言，可选 "zh" (中), "ja" (日), "en" (英)
my_keyscale: 调式和弦（如 "E minor", "C major", "A minor"）

2、TTS语音合成
my_text: 填入需要配音的中文或英文文本。
my_voice_description: 声音特征描述，必须使用英文（如 "A young female voice, energetic and bright." 或 "A deep male voice, calm and professional."）

3、图像编辑
单图编辑：修改 input_image 为 input 目录下的原图文件名；修改 my_prompt 告诉 AI 如何修改这张图。
双图/三图融合：修改 image_one / image_two / image_three 为对应的多张素材文件名；修改 my_prompt 告诉 AI 如何将这几张图融合在一起。
尺寸控制（均包含 my_ratio 变量）：可选宽高比字符串必须严格对应：
横屏："16:9 (Widescreen)" / "3:2 (Photo)" / "4:3 (Standard)" / "21:9 (Ultrawide)"
竖屏："9:16 (Portrait Widescreen)" / "2:3 (Portrait Photo)" / "3:4 (Portrait Standard)"
方形："1:1 (Square)"





