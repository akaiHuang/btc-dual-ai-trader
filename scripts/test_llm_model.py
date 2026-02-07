import os
import time
import json
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

def test_model_capability(api_key=None):
    """
    Tests the OpenAI model's ability to analyze market data and its speed.
    """
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found. Please set it in your .env file or environment.")
        return

    client = OpenAI(api_key=api_key)
    
    # Model to test: gpt-4o-mini (Recommended: Fast, Smart, Cheap)
    model_name = "gpt-4o-mini"
    
    print(f"🤖 Testing Model: {model_name}...")
    
    # Test Scenario: The 'Trap' setup we discussed
    # High OI, Stagnant Price, High LS Ratio -> Retail is longing the top, Smart Money is absorbing.
    market_context = {
        "price": 98500,
        "price_change_1h": "+0.1%",
        "open_interest": "15B (High)",
        "oi_change_1h": "+5.2%",
        "long_short_ratio": 2.8,
        "funding_rate": 0.04,
        "recent_action": "Price rejected at 98800 twice, now ranging."
    }
    
    system_prompt = """
    You are an expert crypto market analyst specializing in Market Microstructure and Liquidity Traps.
    Analyze the provided market snapshot.
    
    Key Indicators to watch:
    1. OI Divergence (Price flat + OI up = Trap).
    2. LS Ratio (High LS > 2.0 = Crowded Longs).
    
    Output format: JSON with keys: 'sentiment' (bullish/bearish/neutral), 'confidence' (0-100), 'analysis' (brief), 'action' (long/short/wait).
    """
    
    user_prompt = f"Current Market State: {json.dumps(market_context)}"
    
    print("\n⏱️ Sending request...")
    start_time = time.time()
    
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            # temperature=0.3 # gpt-5-mini might not support temp adjustment yet
        )
        end_time = time.time()
        duration = end_time - start_time
        
        content = response.choices[0].message.content
        result = json.loads(content)
        
        print(f"✅ Response received in {duration:.2f} seconds.")
        print("-" * 50)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print("-" * 50)
        
        # Evaluation
        score = 0
        reasons = []
        
        # Check Speed
        if duration < 2.0:
            score += 20
            reasons.append("Speed: Excellent (<2s)")
        elif duration < 5.0:
            score += 10
            reasons.append("Speed: Good (<5s)")
            
        # Check Logic (Expect Bearish due to Trap setup)
        if result['sentiment'] == 'bearish':
            score += 40
            reasons.append("Logic: Correctly identified Bearish setup (Trap).")
        elif result['sentiment'] == 'neutral':
            score += 10
            reasons.append("Logic: Neutral is acceptable but missed the trap.")
        else:
            score -= 20
            reasons.append("Logic: Failed (Bullish on a Trap setup).")
            
        # Check Action
        if result['action'] in ['short', 'wait']:
            score += 20
            reasons.append("Action: Prudent choice.")
            
        # Check JSON format
        if isinstance(result, dict):
            score += 20
            reasons.append("Format: Valid JSON.")
            
        print(f"\n🏆 Final Score: {score}/100")
        print("📝 Evaluation Details:")
        for r in reasons:
            print(f" - {r}")
            
        if score >= 80:
            print(f"\n✅ Conclusion: {model_name} is highly suitable for this task.")
        else:
            print(f"\n⚠️ Conclusion: {model_name} might need prompt tuning.")

    except Exception as e:
        print(f"❌ Error during API call: {e}")

if __name__ == "__main__":
    test_model_capability()
