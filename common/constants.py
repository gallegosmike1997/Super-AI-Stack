import os


SERVICE_URLS = {
	"router": os.getenv("ROUTER_URL", "http://localhost:8001"),
	"general": os.getenv("GENERAL_URL", "http://localhost:8002"),
	"coding": os.getenv("CODING_URL", "http://localhost:8003"),
	"memory": os.getenv("MEMORY_URL", "http://localhost:8004"),
	"reasoning": os.getenv("REASONING_URL", "http://localhost:8005"),
	"vision": os.getenv("VISION_URL", "http://localhost:8006"),
	"speech": os.getenv("SPEECH_URL", "http://localhost:8007"),
	"image_gen": os.getenv("IMAGE_URL", "http://localhost:8008"),
	"agent": os.getenv("AGENT_URL", "http://localhost:8009"),
}

SUPPORTED_TASKS = {"chat", "code", "reasoning", "vision", "speech", "image_gen", "agent"}

# Local MoE catalog: layer -> (expert service, default local model, purpose).
# Override any MODEL_NAME via environment to match your hardware
# (e.g. Mistral Large for coding, DeepSeek R1 70B for reasoning).
EXPERT_CATALOG = [
    {"layer": "Router", "expert": "router", "model": "phi3:mini", "purpose": "Task routing"},
    {"layer": "Reasoning", "expert": "reasoning", "model": "deepseek-r1:8b", "purpose": "Logic + math"},
    {"layer": "General LLM", "expert": "general", "model": "llama3.1", "purpose": "Language + knowledge"},
    {"layer": "Coding", "expert": "coding", "model": "qwen2.5-coder:7b", "purpose": "Code + tools"},
    {"layer": "Vision", "expert": "vision", "model": "qwen2.5vl:3b", "purpose": "OCR + diagrams"},
    {"layer": "Speech", "expert": "speech", "model": "whisper-small", "purpose": "Audio"},
    {"layer": "Image Gen", "expert": "image_gen", "model": "stable-diffusion-3", "purpose": "Creation"},
    {"layer": "Memory", "expert": "memory", "model": "faiss", "purpose": "Long-term storage"},
]
