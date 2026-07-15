# -*- coding: utf-8 -*-
"""
ComfyUI Multi-Modal API Service
==============================
统一的 ComfyUI HTTP 服务，支持5种接口：
    1. 单图编辑 (/api/edit)
    2. 双图融合 (/api/blend)
    3. 三图融合 (/api/triple_blend)
    4. 语音合成 (/api/tts)
    5. 音乐生成 (/api/music)

启动方式:
    python comfyui_api_service.py

环境变量配置:
    COMFYUI_SERVER: ComfyUI 服务器地址 (默认: 192.168.199.239:8188)
    COMFYUI_INPUT_DIR: ComfyUI input 文件夹路径 (默认: ./input)
    SERVICE_HOST: 服务监听地址 (默认: 0.0.0.0)
    SERVICE_PORT: 服务监听端口 (默认: 5000)
    GENERATION_TIMEOUT: 生成超时时间(秒) (默认: 300)
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

SERVER_ADDRESS = os.environ.get("COMFYUI_SERVER", "192.168.199.239:8188")
COMFYUI_INPUT_DIR = os.environ.get("COMFYUI_INPUT_DIR", "./input")
GENERATION_TIMEOUT = int(os.environ.get("GENERATION_TIMEOUT", "300"))
HOST = os.environ.get("SERVICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVICE_PORT", "5000"))

_BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKFLOWS_DIR = os.path.join(_BRIDGE_DIR, "workflows")


def _workflow_path(name: str) -> str:
    """Resolve a workflow JSON under workflows/ (cwd fallback for overrides)."""
    candidates = [
        os.path.join(_WORKFLOWS_DIR, name),
        os.path.join(_BRIDGE_DIR, name),
        name,
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


WORKFLOW_CONFIG = {
    "edit": {
        "template": _workflow_path("image_edit_workflow.json"),
        "output_dir": "./output_edit"
    },
    "blend": {
        "template": _workflow_path("image_blend_workflow.json"),
        "output_dir": "./output_blend"
    },
    "triple_blend": {
        "template": _workflow_path("image_triple_blend_workflow.json"),
        "output_dir": "./output_triple_blend"
    },
    "tts": {
        "template": _workflow_path("VoiceDesign-QwenTTS.json"),
        "output_dir": "./output_tts"
    },
    "music": {
        "template": _workflow_path("MusicCreation-ACESTEP1.5XL_api.json"),
        "output_dir": "./output_music"
    }
}

# =================================================

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def clean_workflow(workflow):
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
    p = {"prompt": prompt, "client_id": client_id}
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
    resp = requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_file(filename, subfolder, file_type):
    view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={file_type}"
    resp = requests.get(view_url, timeout=60)
    resp.raise_for_status()
    return resp.content


def upload_to_comfyui(image_file):
    upload_url = f"http://{SERVER_ADDRESS}/upload/image"
    files = {"image": (image_file.filename, image_file.stream, image_file.content_type)}
    resp = requests.post(upload_url, files=files, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result["name"]


def wait_for_completion(ws, prompt_id, timeout=GENERATION_TIMEOUT):
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise TimeoutError(f"Generation timed out after {timeout} seconds")
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


def run_image_workflow(workflow_type, image_filenames, prompt_text, aspect_ratio):
    config = WORKFLOW_CONFIG[workflow_type]
    client_id = str(uuid.uuid4())

    if not os.path.exists(config["template"]):
        raise FileNotFoundError(f"Workflow template not found: {config['template']}")

    with open(config["template"], "r", encoding="utf-8") as f:
        workflow = json.load(f)

    workflow = clean_workflow(workflow)

    if workflow_type == "edit":
        workflow["41"]["inputs"]["image"] = image_filenames[0]
        workflow["68"]["inputs"]["prompt"] = prompt_text
        workflow["126"]["inputs"]["aspect_ratio"] = aspect_ratio
    elif workflow_type == "blend":
        workflow["41"]["inputs"]["image"] = image_filenames[0]
        workflow["79"]["inputs"]["image"] = image_filenames[1]
        workflow["68"]["inputs"]["prompt"] = prompt_text
        workflow["126"]["inputs"]["aspect_ratio"] = aspect_ratio
    elif workflow_type == "triple_blend":
        workflow["41"]["inputs"]["image"] = image_filenames[0]
        workflow["79"]["inputs"]["image"] = image_filenames[1]
        workflow["133"]["inputs"]["image"] = image_filenames[2]
        workflow["68"]["inputs"]["prompt"] = prompt_text
        workflow["126"]["inputs"]["aspect_ratio"] = aspect_ratio

    ws = websocket.WebSocket()
    try:
        ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}", timeout=10)
        result = queue_prompt(workflow, client_id)
        prompt_id = result.get('prompt_id')
        if not prompt_id:
            raise RuntimeError(f"Failed to queue prompt: {result}")

        print(f"[{prompt_id}] Task queued for {workflow_type}")
        wait_for_completion(ws, prompt_id)
        print(f"[{prompt_id}] Generation complete")

        history = get_history(prompt_id)
        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get('outputs', {})

        if not outputs:
            raise RuntimeError("No outputs found in history")

        for node_id in outputs:
            node_output = outputs[node_id]
            if 'images' in node_output and node_output['images']:
                img_item = node_output['images'][0]
                filename = img_item['filename']
                subfolder = img_item.get('subfolder', '')
                img_type = img_item.get('type', 'output')
                img_data = download_file(filename, subfolder, img_type)
                return img_data, filename

        raise RuntimeError("No images found in workflow outputs")
    finally:
        ws.close()


def run_audio_workflow(workflow_type, params):
    config = WORKFLOW_CONFIG[workflow_type]
    client_id = str(uuid.uuid4())

    if not os.path.exists(config["template"]):
        raise FileNotFoundError(f"Workflow template not found: {config['template']}")

    with open(config["template"], "r", encoding="utf-8") as f:
        workflow = json.load(f)

    workflow = clean_workflow(workflow)

    if workflow_type == "tts":
        workflow["75"]["inputs"]["text"] = params["text"]
        workflow["76"]["inputs"]["text"] = params["voice_description"]
    elif workflow_type == "music":
        workflow["40"]["inputs"]["text"] = params["lyrics"]
        workflow["41"]["inputs"]["text"] = params["style"]
        workflow["43"]["inputs"]["value"] = float(params["duration"])
        workflow["45"]["inputs"]["value"] = int(params["bpm"])
        workflow["36"]["inputs"]["timesignature"] = str(params["timesignature"])
        workflow["36"]["inputs"]["language"] = str(params["language"])
        workflow["36"]["inputs"]["keyscale"] = str(params["keyscale"])

    ws = websocket.WebSocket()
    try:
        ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}", timeout=10)
        result = queue_prompt(workflow, client_id)
        prompt_id = result.get('prompt_id')
        if not prompt_id:
            raise RuntimeError(f"Failed to queue prompt: {result}")

        print(f"[{prompt_id}] Task queued for {workflow_type}")
        wait_for_completion(ws, prompt_id)
        print(f"[{prompt_id}] Generation complete")

        history = get_history(prompt_id)
        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get('outputs', {})

        if not outputs:
            raise RuntimeError("No outputs found in history")

        for node_id in outputs:
            node_output = outputs[node_id]
            if 'audio' in node_output and node_output['audio']:
                audio_item = node_output['audio'][0]
                filename = audio_item['filename']
                subfolder = audio_item.get('subfolder', '')
                audio_type = audio_item.get('type', 'output')
                audio_data = download_file(filename, subfolder, audio_type)
                return audio_data, filename

        raise RuntimeError("No audio found in workflow outputs")
    finally:
        ws.close()


@app.route('/api/edit', methods=['POST'])
def api_edit():
    try:
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

        filename = upload_to_comfyui(image_file)
        print(f"Uploaded image to ComfyUI: {filename}")

        img_data, output_filename = run_image_workflow("edit", [filename], prompt_text, aspect_ratio)

        buffer = BytesIO(img_data)
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png', as_attachment=False, download_name=output_filename)

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_detail = e.response.json()
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/api/blend', methods=['POST'])
def api_blend():
    try:
        if 'image1' not in request.files or 'image2' not in request.files:
            return jsonify({"success": False, "error": "Missing image files (image1, image2 required)"}), 400
        if 'prompt' not in request.form:
            return jsonify({"success": False, "error": "Missing 'prompt' text"}), 400

        image1_file = request.files['image1']
        image2_file = request.files['image2']
        prompt_text = request.form.get('prompt', '').strip()
        aspect_ratio = request.form.get('aspect_ratio', '16:9 (Widescreen)').strip()

        if not prompt_text:
            return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400
        if image1_file.filename == '' or image2_file.filename == '':
            return jsonify({"success": False, "error": "No image selected"}), 400

        filename1 = upload_to_comfyui(image1_file)
        filename2 = upload_to_comfyui(image2_file)
        print(f"Uploaded images to ComfyUI: {filename1}, {filename2}")

        img_data, output_filename = run_image_workflow("blend", [filename1, filename2], prompt_text, aspect_ratio)

        buffer = BytesIO(img_data)
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png', as_attachment=False, download_name=output_filename)

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_detail = e.response.json()
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/api/triple_blend', methods=['POST'])
def api_triple_blend():
    try:
        if 'image1' not in request.files or 'image2' not in request.files or 'image3' not in request.files:
            return jsonify({"success": False, "error": "Missing image files (image1, image2, image3 required)"}), 400
        if 'prompt' not in request.form:
            return jsonify({"success": False, "error": "Missing 'prompt' text"}), 400

        image1_file = request.files['image1']
        image2_file = request.files['image2']
        image3_file = request.files['image3']
        prompt_text = request.form.get('prompt', '').strip()
        aspect_ratio = request.form.get('aspect_ratio', '16:9 (Widescreen)').strip()

        if not prompt_text:
            return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400
        if image1_file.filename == '' or image2_file.filename == '' or image3_file.filename == '':
            return jsonify({"success": False, "error": "No image selected"}), 400

        filename1 = upload_to_comfyui(image1_file)
        filename2 = upload_to_comfyui(image2_file)
        filename3 = upload_to_comfyui(image3_file)
        print(f"Uploaded images to ComfyUI: {filename1}, {filename2}, {filename3}")

        img_data, output_filename = run_image_workflow("triple_blend", [filename1, filename2, filename3], prompt_text, aspect_ratio)

        buffer = BytesIO(img_data)
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png', as_attachment=False, download_name=output_filename)

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_detail = e.response.json()
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/api/tts', methods=['POST'])
def api_tts():
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Content-Type must be application/json"}), 400

        data = request.get_json()
        text = data.get('text', '').strip()
        voice_description = data.get('voice_description', '').strip()

        if not text:
            return jsonify({"success": False, "error": "Text cannot be empty"}), 400
        if not voice_description:
            return jsonify({"success": False, "error": "Voice description cannot be empty"}), 400

        params = {
            "text": text,
            "voice_description": voice_description
        }

        audio_data, output_filename = run_audio_workflow("tts", params)

        buffer = BytesIO(audio_data)
        buffer.seek(0)
        return send_file(buffer, mimetype='audio/mp3', as_attachment=False, download_name=output_filename)

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_detail = e.response.json()
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/api/music', methods=['POST'])
def api_music():
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Content-Type must be application/json"}), 400

        data = request.get_json()
        lyrics = data.get('lyrics', '').strip()
        style = data.get('style', '').strip()
        duration = data.get('duration', 60.0)
        bpm = data.get('bpm', 110)
        timesignature = data.get('timesignature', "4")
        language = data.get('language', "zh")
        keyscale = data.get('keyscale', "C major")

        if not lyrics:
            return jsonify({"success": False, "error": "Lyrics cannot be empty"}), 400
        if not style:
            return jsonify({"success": False, "error": "Style cannot be empty"}), 400

        params = {
            "lyrics": lyrics,
            "style": style,
            "duration": duration,
            "bpm": bpm,
            "timesignature": timesignature,
            "language": language,
            "keyscale": keyscale
        }

        audio_data, output_filename = run_audio_workflow("music", params)

        buffer = BytesIO(audio_data)
        buffer.seek(0)
        return send_file(buffer, mimetype='audio/mp3', as_attachment=False, download_name=output_filename)

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_detail = e.response.json()
                return jsonify({"success": False, "error": f"ComfyUI error: {err_detail}"}), 502
            except Exception:
                return jsonify({"success": False, "error": f"ComfyUI error: {e.response.text}"}), 502
        return jsonify({"success": False, "error": f"ComfyUI HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500


@app.route('/health', methods=['GET'])
def health_check():
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
    return jsonify({
        "service": "ComfyUI Multi-Modal API Service",
        "version": "1.0",
        "endpoints": {
            "/api/edit": {
                "method": "POST",
                "description": "单图编辑",
                "content_type": "multipart/form-data",
                "params": {
                    "image": "图片文件",
                    "prompt": "提示词",
                    "aspect_ratio": "可选，宽高比"
                }
            },
            "/api/blend": {
                "method": "POST",
                "description": "双图融合",
                "content_type": "multipart/form-data",
                "params": {
                    "image1": "第一张图片",
                    "image2": "第二张图片",
                    "prompt": "融合提示词",
                    "aspect_ratio": "可选，宽高比"
                }
            },
            "/api/triple_blend": {
                "method": "POST",
                "description": "三图融合",
                "content_type": "multipart/form-data",
                "params": {
                    "image1": "第一张图片",
                    "image2": "第二张图片",
                    "image3": "第三张图片",
                    "prompt": "融合提示词",
                    "aspect_ratio": "可选，宽高比"
                }
            },
            "/api/tts": {
                "method": "POST",
                "description": "语音合成",
                "content_type": "application/json",
                "params": {
                    "text": "待合成文本",
                    "voice_description": "声音描述(英文)"
                }
            },
            "/api/music": {
                "method": "POST",
                "description": "音乐生成",
                "content_type": "application/json",
                "params": {
                    "lyrics": "歌词",
                    "style": "风格描述",
                    "duration": "时长(秒)",
                    "bpm": "BPM",
                    "timesignature": "拍号",
                    "language": "语言",
                    "keyscale": "调式"
                }
            },
            "/health": {
                "method": "GET",
                "description": "健康检查"
            }
        }
    })


if __name__ == "__main__":
    print("=" * 60)
    print("ComfyUI Multi-Modal API Service")
    print("=" * 60)
    print(f"ComfyUI Server:  {SERVER_ADDRESS}")
    print(f"Input Directory: {COMFYUI_INPUT_DIR}")
    print(f"Service:         http://{HOST}:{PORT}")
    print("=" * 60)
    print("Endpoints:")
    print("  POST  /api/edit         - 单图编辑")
    print("  POST  /api/blend        - 双图融合")
    print("  POST  /api/triple_blend - 三图融合")
    print("  POST  /api/tts          - 语音合成")
    print("  POST  /api/music        - 音乐生成")
    print("  GET   /health           - 健康检查")
    print("=" * 60)

    ensure_dir(COMFYUI_INPUT_DIR)
    app.run(host=HOST, port=PORT, threaded=True, debug=False)