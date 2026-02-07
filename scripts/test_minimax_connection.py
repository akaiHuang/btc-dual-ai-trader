import json
import os
from openai import OpenAI
import sys

def test_connection():
    # 1. Load Config
    config_path = "config/ai_dragon_config.json"
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ Config file not found: {config_path}")
        return

    model_name = config.get("model_config", {}).get("model_name")
    provider = config.get("model_config", {}).get("provider")

    print(f"🔍 Configuration Loaded:")
    print(f"   Provider: {provider}")
    print(f"   Model: {model_name}")

    if not model_name:
        print("❌ Model name missing in config")
        return

    # 2. Initialize Client
    print(f"🔌 Connecting to Ollama...")
    try:
        client = OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama',
        )
    except Exception as e:
        print(f"❌ Failed to initialize client: {e}")
        return

    # 3. Send Test Request
    print(f"📨 Sending test prompt to {model_name}...")
    start_time = time.time()
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": "Hello! Are you ready to trade? Please reply with a short 'Yes, I am Kimi'."}
            ],
            temperature=0.7,
            max_tokens=500
        )
        duration = time.time() - start_time
        content = response.choices[0].message.content
        
        print(f"✅ Connection Successful! ({duration:.2f}s)")
        print(f"📝 Response Content: '{content}'")
        print(f"🔍 Full Response Object: {response}")
        
    except Exception as e:
        print(f"❌ Request failed: {e}")
        print("💡 Tip: Make sure 'ollama serve' is running and the model is pulled.")

import time
if __name__ == "__main__":
    test_connection()
