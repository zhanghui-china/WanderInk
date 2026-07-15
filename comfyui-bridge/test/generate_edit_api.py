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
    """Submits the modified JSON workflow graph to ComfyUI"""
    p = {"prompt": prompt, "client_id": CLIENT_ID}
    data = json.dumps(p).encode('utf-8')
    req = requests.post(f"http://{SERVER_ADDRESS}/prompt", data=data)
    return req.json()

def get_history(prompt_id):
    """Fetches metadata of the completed task from history"""
    with requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as response:
        return response.json()

def track_and_download_images(ws, prompt, output_dir="./output_edit"):
    """Tracks progress via WebSocket and downloads output images upon completion"""
    prompt_id = queue_prompt(prompt)['prompt_id']
    print(f"Task submitted successfully. Prompt ID: {prompt_id}")
    print("Waiting for DGX Spark to process and edit the image...")
    
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
            
    print("Image processing complete! Downloading output file...")
    history = get_history(prompt_id)[prompt_id]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    saved_files = []
    for node_id in history['outputs']:
        node_output = history['outputs'][node_id]
        if 'images' in node_output:  
            for img_item in node_output['images']:
                filename = img_item['filename']
                subfolder = img_item['subfolder']
                img_type = img_item['type']
                
                view_url = f"http://{SERVER_ADDRESS}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
                img_data = requests.get(view_url).content
                
                target_path = os.path.join(output_dir, filename)
                with open(target_path, "wb") as f:
                    f.write(img_data)
                saved_files.append(target_path)
                
    return saved_files

def edit_image(image_filename, prompt_text, aspect_ratio, json_template_path="image_edit_workflow.json"):
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
        raise FileNotFoundError(f"Template JSON file not found: {json_template_path}")
        
    with open(json_template_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Directly set the filename since the image is already in the 'input' folder
    workflow["41"]["inputs"]["image"] = image_filename        
    workflow["68"]["inputs"]["prompt"] = prompt_text    
    workflow["126"]["inputs"]["aspect_ratio"] = aspect_ratio

    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
    
    try:
        results = track_and_download_images(ws, workflow)
        for path in results:
            print(f"Success! Edited image saved to: {path}")
    finally:
        ws.close()

# ========================================================
# Run Section (Modify your parameters here)
# ========================================================
if __name__ == "__main__":
    
    # 1. Image filename ALREADY placed inside ComfyUI/input/ folder
    input_image = "raiden_shogun_coser.png"

    # 2. Text prompt describing how to modify the image
    my_prompt = "A beautiful woman in a traditional Japanese temple garden with blooming cherry blossoms under a gentle spring breeze, high quality, cinematic lighting"

    # 3. Choose Aspect Ratio
    my_ratio = "16:9 (Widescreen)"

    # Execute the workflow
    edit_image(
        image_filename=input_image,
        prompt_text=my_prompt,
        aspect_ratio=my_ratio,
        json_template_path="image_edit_workflow.json" 
    )
