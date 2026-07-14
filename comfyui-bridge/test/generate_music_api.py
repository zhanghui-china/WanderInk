import json
import uuid
import websocket
import requests
import os
import sys
import argparse

# 配置 ComfyUI 服务器地址（由于在 DGX 本地运行，保持 127.0.0.1 即可）
SERVER_ADDRESS = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())

def queue_prompt(prompt):
    """向 ComfyUI 提交任务队列"""
    p = {"prompt": prompt, "client_id": CLIENT_ID}
    data = json.dumps(p).encode('utf-8')
    req = requests.post(f"http://{SERVER_ADDRESS}/prompt", data=data)
    return req.json()

def get_history(prompt_id):
    """获取历史执行结果"""
    with requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as response:
        return response.json()

def track_and_download_audio(ws, prompt, output_dir="./output_music", output_filename=None):
    """通过 WebSocket 追踪进度，并在完成后自动下载音频文件"""
    prompt_id = queue_prompt(prompt)['prompt_id']
    print(f"🚀 成功提交音乐生成任务，任务 ID: {prompt_id}")
    print("⏳ DGX Spark 正在全力渲染中，请稍候...", flush=True)
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            # 监听执行状态
            if message['type'] == 'executing':
                data = message['data']
                # 当 node 为 None 时，代表整个工作流全部执行完毕
                if data['node'] is None and data['prompt_id'] == prompt_id:
                    break
        else:
            continue
            
    print("✨ 渲染完成！正在提取音频文件...", flush=True)
    history = get_history(prompt_id)[prompt_id]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    saved_files = []
    # 遍历所有节点的输出，寻找音频类型
    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'audio' in node_output:  # 注意：音乐生成工作流此处为 audio
            for audio_item in node_output['audio']:
                filename = audio_item['filename']
                subfolder = audio_item['subfolder']
                audio_type = audio_item['type']
                
                # 请求 ComfyUI 的 /view 接口下载二进制音频流
                view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={audio_type}"
                audio_data = requests.get(view_url).content
                
                actual_filename = output_filename if output_filename else filename
                target_path = os.path.join(output_dir, actual_filename)
                with open(target_path, "wb") as f:
                    f.write(audio_data)
                saved_files.append(target_path)
                
    return saved_files

def make_music(lyrics, style, duration, bpm, timesignature, language, keyscale, output_dir="./output_music", output_filename=None, json_template_path="MusicCreation-ACESTEP1.5XL_api.json"):
    """
    核心业务函数：读取 JSON 模板，动态修改参数，并触发生成
    """
    # 动态支持绝对路径转换
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(json_template_path):
        resolved_path = os.path.join(script_dir, json_template_path)
        if not os.path.exists(resolved_path):
            parent_dir = os.path.join(os.path.dirname(script_dir), json_template_path)
            if os.path.exists(parent_dir):
                resolved_path = parent_dir
            else:
                cwd_dir = os.path.abspath(json_template_path)
                if os.path.exists(cwd_dir):
                    resolved_path = cwd_dir
        json_template_path = resolved_path

    # 1. 检查并读取本地的工作流 API JSON 文件
    if not os.path.exists(json_template_path):
        raise FileNotFoundError(f"未找到工作流文件：{json_template_path}，请确保路径正确！")
        
    with open(json_template_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 2. 动态改写用户指定的参数
    # Unescape literal newlines and carriage returns passed from CLI/JS stringify
    if isinstance(lyrics, str):
        lyrics = lyrics.replace('\\n', '\n').replace('\\r', '\r')
    if isinstance(style, str):
        style = style.replace('\\n', '\n').replace('\\r', '\r')

    workflow["40"]["inputs"]["text"] = lyrics                       # 歌词
    workflow["41"]["inputs"]["text"] = style                        # 歌曲风格描述
    workflow["43"]["inputs"]["value"] = float(duration)             # 歌曲长度 (Float)
    workflow["45"]["inputs"]["value"] = int(bpm)                    # BPM (Int)
    
    # 36 号节点的三个特定参数
    workflow["36"]["inputs"]["timesignature"] = str(timesignature)  # 拍子 (String: "2", "3", "4", "6")
    workflow["36"]["inputs"]["language"] = str(language)            # 语言 ("en", "ja", "zh" 等)
    workflow["36"]["inputs"]["keyscale"] = str(keyscale)            # 和弦/调式 (如 "E minor", "C major")

    # 3. 建立 WebSocket 连接并执行任务
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
    
    try:
        results = track_and_download_audio(ws, workflow, output_dir=output_dir, output_filename=output_filename)
        for path in results:
            print(f"🎉 音乐已成功保存至: {path}", flush=True)
    finally:
        ws.close()

# ==========================================
# 命令行参数解析 / 默认参数运行支持
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="ComfyUI Music Generator CLI")
        parser.add_argument("--lyrics", type=str, required=True)
        parser.add_argument("--style", type=str, required=True)
        parser.add_argument("--duration", type=float, required=True)
        parser.add_argument("--bpm", type=int, required=True)
        parser.add_argument("--timesignature", type=str, required=True)
        parser.add_argument("--language", type=str, required=True)
        parser.add_argument("--keyscale", type=str, required=True)
        parser.add_argument("--output_dir", type=str, default="./output_music")
        parser.add_argument("--output_filename", type=str, default=None)
        parser.add_argument("--template", type=str, default="MusicCreation-ACESTEP1.5XL_api.json")
        
        args = parser.parse_args()
        
        make_music(
            lyrics=args.lyrics,
            style=args.style,
            duration=args.duration,
            bpm=args.bpm,
            timesignature=args.timesignature,
            language=args.language,
            keyscale=args.keyscale,
            output_dir=args.output_dir,
            output_filename=args.output_filename,
            json_template_path=args.template
        )
    else:
        # 1. 填写歌词
        my_lyrics = """[Intro]  
風が哭く 夜が嗤う  
誰が正義か 誰が鬼か

[Verse 1]  
名を捨てた 影の剑  
刃一閃 语らぬまま  
朱に染まる この両手  
罪も义も 同じ血で洗う

[Outro]"""

        # 2. 填写风格描述
        my_style = "Style: \n- Japanese Traditional Rock Fusion\n- Samurai Rock\n- Cinematic Rock\n- Epic J-Rock"

        # 3. 设定长度与节拍
        my_duration = 60.0    # 歌曲长度（秒），必须是浮点数
        my_bpm = 110          # 歌曲 BPM，必须是整数
        
        # 4. 设定高级音乐参数
        my_timesignature = "4"  # 拍子：可选 "2", "3", "4", "6"
        my_language = "ja"      # 语言：可选 "zh" (中文), "ja" (日文), "en" (英文)
        my_keyscale = "E minor" # 和弦：例如 "C major", "A minor", "E minor" 等

        # 执行生成
        make_music(
            lyrics=my_lyrics,
            style=my_style,
            duration=my_duration,
            bpm=my_bpm,
            timesignature=my_timesignature,
            language=my_language,
            keyscale=my_keyscale,
            json_template_path="MusicCreation-ACESTEP1.5XL_api.json"
        )
