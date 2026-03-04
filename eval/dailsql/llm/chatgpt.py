import json.decoder
from openai import OpenAI
from utils.enums import LLM
import time
import os

CLIENT = None

def init_chatgpt(OPENAI_API_KEY, OPENAI_GROUP_ID, model):
    global CLIENT
    if "gemini" in model:
        CLIENT = OpenAI(
            api_key=os.environ.get("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
    elif "/" in model:
        CLIENT = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
        )
    else:
        CLIENT = OpenAI(api_key=OPENAI_API_KEY)
        # openai.organization = OPENAI_GROUP_ID # v1.x handles this differently if needed, usually just api_key is enough for standard usage

def ask_chat(model, messages: list, temperature, n, max_tokens):
    global CLIENT
    # print('>>>model:', model)
    # print('>>>max_tokens:', max_tokens)
    
    # Ensure client is initialized
    if CLIENT is None:
        raise RuntimeError("OpenAI Client not initialized. Call init_chatgpt first.")

    if "gemini" in model:
         # Gemini logic
        try:
            if "2" in model:
                response = CLIENT.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    n=n,
                    # reasoning_effort="none"
                )
            else:
             response = CLIENT.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                n=n,
                reasoning_effort="none"
            )
        except Exception as e:
            # Fallback if reasoning_effort is not supported or other error, retry without it
            print(f"Gemini call failed with reasoning_effort='none', retrying without it. Error: {e}")
            response = CLIENT.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                n=n
            )
    elif "/" in model:
        if "qwen" in model or "claude" in model:
            response = CLIENT.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                n=n,
                extra_body={
                    "reasoning": {
                        "effort": "none",
                    }
                },
        )
        elif "minimax" in model:
            response = CLIENT.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                # max_tokens=max_tokens,
                n=n
        )

    elif "gpt-5" in model or "o4-mini" in model:
        response = CLIENT.chat.completions.create(
            model=model,
            messages=messages,
            # temperature=temperature,
            # max_completion_tokens=max_tokens,
            n=n
        )
    elif model == LLM.GPT_o1:
        response = CLIENT.chat.completions.create(
            model=model,
            messages=messages,
            # temperature=temperature, # o1 doesn't support temp
            # max_completion_tokens=max_tokens,
            n=n
        )
    else:
        response = CLIENT.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            n=n
        )
    
    response_clean = [choice.message.content for choice in response.choices]
    return dict(
        response=response_clean,
        # usage might be different object in v1, access safely
        total_tokens=response.usage.total_tokens,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens
    )


def ask_llm(model: str, batch: list, temperature: float, n:int, max_tokens):
    try:
        # assert model in LLM.TASK_CHAT, "LLM.TASK_COMPLETIONS is too old. not supported."
        # batch size must be 1
        assert len(batch) == 1, "batch must be 1 in this mode"
        messages = [{"role": "user", "content": batch[0]}]
        response = ask_chat(model, messages, temperature, n, max_tokens)
        response['response'] = [response['response']]  # hard-code for batch_size=1
    except Exception as e:
        print(f"Error occurred in ask_llm: {e}")
        # import traceback
        # traceback.print_exc()
        # Return hard-coded response
        print("Return hard-coded response")
        response = {"total_tokens": 0, "response": [["SELECT" for _ in range(n)]]}
    
    return response

