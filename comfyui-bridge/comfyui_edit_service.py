# -*- coding: utf-8 -*-
"""
ComfyUI Image Edit HTTP Service
==============================
基于 generate_edit_api.py 封装的 HTTP 服务，提供图片编辑 API。

启动方式:
    python comfyui_edit_service.py

接口:
    POST /api/edit
    参数:
        - image: 上传的图片文件 (multipart/form-data)
        - prompt: 提示词文本 (form-data)
        - aspect_ratio: 宽高比，可选，默认 "16:9 (Widescreen)" (form-data)
    返回:
        - 成功: 直接返回生成的图片 (image/png)
        - 失败: JSON 错误信息
"""

import json
import uuid
import websocket
import requests
import os
import time
from io import BytesIO
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename

# ==================== 配置区域 ====================

# ComfyUI 服务器地址
SERVER_ADDRESS = os.environ.get("COMFYUI_SERVER", "127.0.0.1:8188")

# ComfyUI 的 input 文件夹路径
COMFYUI_INPUT_DIR = os.environ.get("COMFYUI_INPUT_DIR", "./ComfyUI/input")

# workflow JSON 模板路径
JSON_TEMPLATE_PATH = os.environ.get("WORKFLOW_PATH", "image_edit_workflow.json")

# 生成超时时间（秒）
GENERATION_TIMEOUT = int(os.environ.get("GENERATION_TIMEOUT", "300"))

# 服务监听配置
HOST = os.environ.get("SERVICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVICE_PORT", "5000"))

# =================================================

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 限制上传 16MB


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def clean_workflow(workflow):
    """
    清理 workflow 中仅前端使用的字段（如 _meta），避免 ComfyUI 后端解析报错。
    同时确保所有节点 ID 为字符串（与 JSON 文件保持一致）。
    """
    cleaned = {}
    for node_id, node_data in workflow.items():
        clean_node = {}
        for k, v in node_data.items():
            if k == "_meta":
                continue
            clean_node[k] = v
        cleaned[str(node_id)] = clean_node
    return cleaned


def queue_prompt(prompt, client_id):
    """提交 workflow 到 ComfyUI"""
    p = {"prompt": prompt, "client_id": client_id}
    # 关键修复：显式设置 Content-Type 为 application/json，并确保中文正确编码
    data = json.dumps(p, ensure_ascii=False).encode('utf-8')
    headers = {"Content-Type": "application/json"}
    
    resp = requests.post(
        f"http://{SERVER_ADDRESS}/prompt",
        data=data,
        headers=headers,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def get_history(prompt_id):
    """获取任务历史"""
    resp = requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_image(filename, subfolder, img_type):
    """从 ComfyUI 下载生成的图片"""
    view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
    resp = requests.get(view_url, timeout=60)
    resp.raise_for_status()
    return resp.content


def wait_for_completion(ws, prompt_id, timeout=GENERATION_TIMEOUT):
    """通过 WebSocket 等待任务完成"""
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise TimeoutError(f"Image generation timed out after {timeout} seconds")
        
        try:
            out = ws.recv()
        except Exception as e:
            raise ConnectionError(f"WebSocket error: {e}")
            
        if isinstance(out, str):
            message = json.loads(out)
            if message.get('type') == 'executing':
                data = message.get('data', {})
                if data.get('node') is None and data.get('prompt_id') == prompt_id:
                    return True


def run_comfyui_workflow(image_filename, prompt_text, aspect_ratio):
    """
    执行 ComfyUI 工作流
    返回: (图片二进制数据, 文件名)
    """
    client_id = str(uuid.uuid4())
    
    # 1. 加载 workflow 模板
    if not os.path.exists(JSON_TEMPLATE_PATH):
        raise FileNotFoundError(f"Workflow template not found: {JSON_TEMPLATE_PATH}")
    
    with open(JSON_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    
    # 2. 清理 workflow（移除 _meta 等前端字段）
    workflow = clean_workflow(workflow)
    
    # 3. 修改 workflow 参数
    workflow["41"]["inputs"]["image"] = image_filename
    workflow["68"]["inputs"]["prompt"] = prompt_text
    workflow["126"]["inputs"]["aspect_ratio"] = aspect_ratio
    
    # 4. 建立 WebSocket 连接并提交任务
    ws = websocket.WebSocket()
    try:
        ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}", timeout=10)
        
        result = queue_prompt(workflow, client_id)
        prompt_id = result.get('prompt_id')
        if not prompt_id:
            raise RuntimeError(f"Failed to queue prompt: {result}")
        
        print(f"[{prompt_id}] Task queued, waiting for ComfyUI...")
        
        # 5. 等待完成
        wait_for_completion(ws, prompt_id)
        
        print(f"[{prompt_id}] Generation complete, fetching results...")
        
        # 6. 获取历史记录并下载图片
        history = get_history(prompt_id)
        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get('outputs', {})
        
        if not outputs:
            raise RuntimeError("No outputs found in history")
        
        # 查找包含图片的输出节点
        for node_id in outputs:
            node_output = outputs[node_id]
            if 'images' in node_output and node_output['images']:
                img_item = node_output['images'][0]
                filename = img_item['filename']
                subfolder = img_item.get('subfolder', '')
                img_type = img_item.get('type', 'output')
                
                img_data = download_image(filename, subfolder, img_type)
                print(f"[{prompt_id}] Downloaded: {filename}")
                return img_data, filename
        
        raise RuntimeError("No images found in workflow outputs")
        
    finally:
        ws.close()


@app.route('/api/edit', methods=['POST'])
def api_edit():
    """
    图片编辑接口
    Form-Data 参数:
        image: 图片文件
        prompt: 提示词
        aspect_ratio: 宽高比 (可选, 默认 "16:9 (Widescreen)")
    """
    try:
        # 1. 检查参数
        if 'image' not in request.files:
            return jsonify({"success": False, "error": "Missing 'image' file"}), 400
        
        if 'prompt' not in request.form:
            return jsonify({"success": False, "error": "Missing 'prompt' text"}), 400
        
        image_file = request.files['image']
        prompt_text = request.form.get('prompt', '').strip()
        aspect_ratio = request.form.get('aspect_ratio', '16:9 (Widescreen)').strip()
        
        if not prompt_text:
            return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400
        
        if image_file.filename == '':
            return jsonify({"success": False, "error": "No image selected"}), 400
        
        # 2. 保存上传的图片到 ComfyUI input 目录
        ensure_dir(COMFYUI_INPUT_DIR)
        
        ext = os.path.splitext(secure_filename(image_file.filename))[1] or '.png'
        unique_name = f"edit_{uuid.uuid4().hex[:12]}{ext}"
        input_path = os.path.join(COMFYUI_INPUT_DIR, unique_name)
        
        image_file.save(input_path)
        print(f"Saved input image to: {input_path}")
        
        # 3. 调用 ComfyUI 生成
        img_data, output_filename = run_comfyui_workflow(unique_name, prompt_text, aspect_ratio)
        
        # 4. 返回图片
        buffer = BytesIO(img_data)
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='image/png',
            as_attachment=False,
            download_name=output_filename
        )
        
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        print(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        # 关键：打印 ComfyUI 返回的详细错误信息
        print(f"ComfyUI HTTP Error: {e}")
        if e.response is not None:
            try:
                err_detail = e.response.json()
                print(f"ComfyUI response: {err_detail}")
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                print(f"ComfyUI raw response: {e.response.text}")
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    try:
        resp = requests.get(f"http://{SERVER_ADDRESS}/system_stats", timeout=5)
        comfy_status = "connected" if resp.status_code == 200 else "unreachable"
    except Exception:
        comfy_status = "unreachable"
    
    return jsonify({
        "status": "ok",
        "comfyui": comfy_status,
        "server_address": SERVER_ADDRESS
    })


@app.route('/', methods=['GET'])
def index():
    """API 说明页"""
    return jsonify({
        "service": "ComfyUI Image Edit API",
        "version": "1.0",
        "endpoints": {
            "/api/edit": {
                "method": "POST",
                "description": "Upload image and prompt, return edited image",
                "params": {
                    "image": "Image file (multipart)",
                    "prompt": "Text prompt (form)",
                    "aspect_ratio": "Optional, e.g. '16:9 (Widescreen)' (form)"
                }
            },
            "/health": {
                "method": "GET",
                "description": "Health check"
            }
        }
    })


if __name__ == "__main__":
    print("=" * 50)
    print("ComfyUI Image Edit HTTP Service")
    print("=" * 50)
    print(f"ComfyUI Server:  {SERVER_ADDRESS}")
    print(f"Input Directory: {COMFYUI_INPUT_DIR}")
    print(f"Workflow File:   {JSON_TEMPLATE_PATH}")
    print(f"Service:         http://{HOST}:{PORT}")
    print("=" * 50)
    
    ensure_dir(COMFYUI_INPUT_DIR)
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
