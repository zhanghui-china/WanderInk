# -*- coding: utf-8 -*-
import json
import uuid
import websocket
import requests
import os

# Server Configuration
SERVER_ADDRESS = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())

def queue_prompt(prompt):
    p = {"prompt": prompt, "client_id": CLIENT_ID}
    data = json.dumps(p).encode('utf-8')
    req = requests.post(f"http://{SERVER_ADDRESS}/prompt", data=data)
    return req.json()

def get_history(prompt_id):
    with requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as response:
        return response.json()

def track_and_download_audio(ws, prompt, output_dir="./output_tts"):
    prompt_id = queue_prompt(prompt)['prompt_id']
    print(f"Task submitted successfully. Prompt ID: {prompt_id}")
    print("Waiting for DGX Spark to generate audio...")
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data['node'] is None and data['prompt_id'] == prompt_id:
                    break
        else:
            continue
            
    print("Audio generation complete! Downloading files...")
    history = get_history(prompt_id)[prompt_id]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    saved_files = []
    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'audio' in node_output:  
            for audio_item in node_output['audio']:
                filename = audio_item['filename']
                subfolder = audio_item['subfolder']
                audio_type = audio_item['type']
                
                view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={audio_type}"
                audio_data = requests.get(view_url).content
                
                target_path = os.path.join(output_dir, filename)
                with open(target_path, "wb") as f:
                    f.write(audio_data)
                saved_files.append(target_path)
                
    return saved_files

def make_voice(text_to_speak, voice_description, json_template_path="VoiceDesign-QwenTTS.json"):
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

    if not os.path.exists(json_template_path):
        raise FileNotFoundError(f"Template not found: {json_template_path}")
        
    with open(json_template_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Map parameters to workflow nodes
    workflow["75"]["inputs"]["text"] = text_to_speak        
    workflow["76"]["inputs"]["text"] = voice_description    

    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
    
    try:
        results = track_and_download_audio(ws, workflow)
        for path in results:
            print(f"Success! Audio saved to: {path}")
    finally:
        ws.close()

# ========================================================
# Run Section
# ========================================================
if __name__ == "__main__":
    
    # 1. Type what you want to say here
    my_text = "大家好，这是一个全自动语音合成测试。欢迎来到黑客松创新大赛！"

    # 2. Type voice description here (Must be in English)
    my_voice_description = "A young female voice, energetic and bright."

    make_voice(
        text_to_speak=my_text,
        voice_description=my_voice_description,
        json_template_path="VoiceDesign-QwenTTS.json"
    )
