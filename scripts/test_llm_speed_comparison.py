import os
import time
import sys
from openai import OpenAI
from dotenv import load_dotenv

# Load .env
load_dotenv()

def test_model(client, provider_name, model_name):
    print(f"🚀 Testing {provider_name} [{model_name}]...")
    start_time = time.time()
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a helpful trading assistant."},
                {"role": "user", "content": "Explain 'Slippage' in crypto trading in one short sentence."}
            ],
            max_tokens=50
        )
        end_time = time.time()
        duration = end_time - start_time
        content = response.choices[0].message.content.strip()
        print(f"✅ Success! Time: {duration:.4f}s")
        print(f"📝 Output: {content}\n")
        return duration
    except Exception as e:
        print(f"❌ Failed: {e}\n")
        return None

def main():
    print("=== 🏎️ LLM Speed & Connectivity Test ===\n")

    # 1. Test OpenAI (GPT-4o-mini)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_time = None
    if openai_api_key:
        openai_client = OpenAI(api_key=openai_api_key)
        openai_time = test_model(openai_client, "OpenAI Cloud", "gpt-4o-mini")
    else:
        print("⚠️ OpenAI API Key not found. Skipping OpenAI test.\n")

    # 2. Test Ollama (Local)
    # User specified qwen3:32b
    ollama_model = "qwen3:32b" 
    
    # Check if user provided a different model name in args
    if len(sys.argv) > 1:
        ollama_model = sys.argv[1]

    ollama_client = OpenAI(
        base_url='http://localhost:11434/v1',
        api_key='ollama',
    )
    
    ollama_time = test_model(ollama_client, "Ollama Local", ollama_model)

    # 3. Summary
    print("=== 📊 Comparison Summary ===")
    if openai_time:
        print(f"☁️  GPT-4o-mini : {openai_time:.4f}s")
    if ollama_time:
        print(f"🏠 Local Model : {ollama_time:.4f}s")
    
    if openai_time and ollama_time:
        diff = ollama_time - openai_time
        ratio = ollama_time / openai_time
        if diff > 0:
            print(f"\n💡 Result: Cloud is {ratio:.1f}x faster (saved {diff:.2f}s per request).")
        else:
            print(f"\n💡 Result: Local is {openai_time / ollama_time:.1f}x faster.")

if __name__ == "__main__":
    main()
