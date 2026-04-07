"""
LLM pipeline loading.
Isolates openvino_genai from the rest of the codebase — swap this file
to plug in a different inference backend (llama.cpp, ONNX Runtime, etc.).
"""

import sys

import openvino_genai as ov_genai

_NPU_MAX_PROMPT = 4096


def load_pipeline(model_path: str, device: str) -> tuple:
    """
    Load an OpenVINO LLMPipeline and return (pipe, prompt_token_limit).

    prompt_token_limit is the max number of tokens the prompt should use
    before history compression kicks in (75 % of NPU limit, 3200 for others).
    """
    try:
        if device == "NPU":
            pipe = ov_genai.LLMPipeline(model_path, device, MAX_PROMPT_LEN=_NPU_MAX_PROMPT)
            prompt_token_limit = int(_NPU_MAX_PROMPT * 0.75)
        else:
            pipe = ov_genai.LLMPipeline(model_path, device)
            prompt_token_limit = 3200
    except Exception as e:
        print(f"Failed to load model: {e}")
        sys.exit(1)

    return pipe, prompt_token_limit
