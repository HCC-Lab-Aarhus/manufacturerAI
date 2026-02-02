import os
import google.generativeai as genai
from pathlib import Path

def _load_env_fallback():
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.exists() and "GEMINI_API_KEY" not in os.environ:
        try:
            content = env_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    key_val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["GEMINI_API_KEY"] = key_val
                    break
        except Exception:
            pass

_load_env_fallback()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("No API Key found")
else:
    print(f"Using API Key: {api_key[:5]}...")
    genai.configure(api_key=api_key)
    try:
        print("Available models supporting generateContent:")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
    except Exception as e:
        print(f"Error listing models: {e}")