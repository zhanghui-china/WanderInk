# -*- coding: utf-8 -*-
import json
import uuid
import websocket
import requests
import os
import sys
import random
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

def track_and_download_images(ws, prompt, output_dir="./output_text2img", output_filename=None):
    """通过 WebSocket 追踪进度，并在完成后自动下载生成的图像"""
    prompt_id = queue_prompt(prompt)['prompt_id']
    print(f"🚀 成功提交文生图任务，任务 ID: {prompt_id}")
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
            
    print("✨ 渲染完成！正在下载生成的图像...", flush=True)
    history = get_history(prompt_id)[prompt_id]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    saved_files = []
    # 遍历所有节点的输出，寻找图像类型
    img_idx = 0
    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'images' in node_output:  
            for img_item in node_output['images']:
                filename = img_item['filename']
                subfolder = img_item['subfolder']
                img_type = img_item['type']
                
                # 请求 ComfyUI 的 /view 接口下载二进制图像流
                view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
                img_data = requests.get(view_url).content
                
                if output_filename:
                    if img_idx == 0:
                        actual_filename = output_filename
                    else:
                        base, ext = os.path.splitext(output_filename)
                        actual_filename = f"{base}_{img_idx}{ext}"
                else:
                    actual_filename = filename
                
                target_path = os.path.join(output_dir, actual_filename)
                with open(target_path, "wb") as f:
                    f.write(img_data)
                saved_files.append(target_path)
                img_idx += 1
                
    return saved_files

def generate_image(prompt_text, aspect_ratio="1:1 (Square)", seed=None, output_dir="./output_text2img", output_filename=None, json_template_path="Text2IMGKrea2_api.json"):
    """
    核心业务函数：读取 JSON 模板，动态修改参数，并触发生成
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(json_template_path):
        bridge_dir = os.path.dirname(script_dir)
        candidates = [
            os.path.join(script_dir, json_template_path),
            os.path.join(bridge_dir, "workflows", os.path.basename(json_template_path)),
            os.path.join(bridge_dir, json_template_path),
            os.path.abspath(json_template_path),
        ]
        for path in candidates:
            if os.path.exists(path):
                json_template_path = path
                break
        else:
            json_template_path = candidates[1]

    # 1. 检查并读取本地的工作流 API JSON 文件
    if not os.path.exists(json_template_path):
        raise FileNotFoundError(f"未找到工作流文件：{json_template_path}，请确保路径正确！")
        
    with open(json_template_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 2. 动态改写用户指定的参数
    # 提示词修改在 51 号节点的 inputs.text
    if "51" in workflow and "inputs" in workflow["51"] and "text" in workflow["51"]["inputs"]:
        workflow["51"]["inputs"]["text"] = prompt_text
    else:
        raise KeyError("在工作流 JSON 中未找到节点 51 的 text 输入项")
        
    # 修改分辨率 (49 号节点)
    if "49" in workflow and "inputs" in workflow["49"] and "aspect_ratio" in workflow["49"]["inputs"]:
        workflow["49"]["inputs"]["aspect_ratio"] = aspect_ratio

    # 修改随机种子 (53 号节点 KSampler)
    if seed is None:
        seed = random.randint(1, 1125899906842624)
    if "53" in workflow and "inputs" in workflow["53"] and "seed" in workflow["53"]["inputs"]:
        workflow["53"]["inputs"]["seed"] = seed

    # 3. 建立 WebSocket 连接并执行任务
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
    
    try:
        results = track_and_download_images(ws, workflow, output_dir=output_dir, output_filename=output_filename)
        for path in results:
            print(f"🎉 图像已成功保存至: {path}", flush=True)
    finally:
        ws.close()

# ==========================================
# 命令行参数解析 / 默认参数运行支持
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="ComfyUI Text-to-Image Krea2 Generator CLI")
        parser.add_argument("--prompt", type=str, required=True, help="提示词 (写入到51号节点的text中)")
        parser.add_argument("--aspect_ratio", type=str, default="1:1 (Square)", 
                            choices=["1:1 (Square)", "16:9 (Widescreen)", "9:16 (Vertical)", "4:3 (Landscape)", "3:4 (Portrait)"],
                            help="生成分辨率比例")
        parser.add_argument("--seed", type=int, default=None, help="随机种子")
        parser.add_argument("--output_dir", type=str, default="./output_text2img", help="保存图片的目录")
        parser.add_argument("--output_filename", type=str, default=None, help="保存图片的名称")
        parser.add_argument("--template", type=str, default="Text2IMGKrea2_api.json", help="工作流 JSON 模板路径")
        
        args = parser.parse_args()
        
        generate_image(
            prompt_text=args.prompt,
            aspect_ratio=args.aspect_ratio,
            seed=args.seed,
            output_dir=args.output_dir,
            output_filename=args.output_filename,
            json_template_path=args.template
        )
    else:
        # 默认参数运行
        my_prompt = "雷电将军战斗，华丽的闪电特效，科幻感，高清，杰作"
        my_ratio = "1:1 (Square)"
        
        # 执行生成
        generate_image(
            prompt_text=my_prompt,
            aspect_ratio=my_ratio,
            json_template_path="Text2IMGKrea2_api.json"
        )
