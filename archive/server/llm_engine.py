"""LLM engine — load a GGUF model and generate responses via llama-cpp-python."""

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL = "qwen3-0.6b-q5_k_m.gguf"
_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class LLMEngine:
    def __init__(self) -> None:
        self._llm = None
        self._lock = threading.Lock()
        self._loaded = False
        self._model_name: str | None = None

    def load(self, model_name: str = DEFAULT_MODEL) -> bool:
        model_path = MODELS_DIR / model_name
        if not model_path.exists():
            # Try first .gguf file in models dir
            if MODELS_DIR.exists():
                gguf_files = list(MODELS_DIR.glob("*.gguf"))
                if gguf_files:
                    model_path = gguf_files[0]
                    model_name = model_path.name
                    logger.info("Default model not found, using %s", model_name)
                else:
                    logger.warning("No GGUF model files found in %s", MODELS_DIR)
                    return False
            else:
                logger.info("Models directory does not exist: %s", MODELS_DIR)
                return False

        try:
            from llama_cpp import Llama
        except ImportError:
            logger.warning(
                "llama-cpp-python not installed. Install with: pip install llama-cpp-python"
            )
            return False

        logger.info("Loading LLM model: %s", model_path)
        try:
            self._llm = Llama(
                model_path=str(model_path),
                n_ctx=2048,
                n_threads=2,
                n_gpu_layers=0,
                verbose=False,
            )
            self._loaded = True
            self._model_name = model_name
            logger.info("LLM model loaded successfully: %s", model_name)
            return True
        except Exception:
            logger.exception("Failed to load LLM model")
            return False

    def generate(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 512
    ) -> str:
        if not self._loaded or self._llm is None:
            return "LLM not loaded. Place a GGUF model file in the /models directory."
        with self._lock:
            try:
                response = self._llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
                text = response["choices"][0]["message"]["content"]
                # Strip Qwen3 thinking tags — only return the final answer
                text = _THINK_TAG_RE.sub("", text)
                return text.strip()
            except Exception:
                logger.exception("LLM generation failed")
                return "Error: LLM generation failed."

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_name(self) -> str | None:
        return self._model_name


llm_engine = LLMEngine()
