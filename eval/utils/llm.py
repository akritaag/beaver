import re
from abc import ABC, abstractmethod
from openai import OpenAI
import os
import json
import time

def extract_json_block(text):
    """
    Extracts a JSON block from the text. 
    Tries to find ```json ... ``` or just {...} 
    """
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match: return match.group(1)
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match: return match.group(1)
    return text

class BaseChat(ABC):
    def __init__(self, model: str, temperature: float = 1.0):
        self.model = model
        self.temperature = float(temperature)
        self.messages = []

    @abstractmethod
    def get_response(self, prompt) -> str:
        pass

class GPTChat(BaseChat):
    def __init__(self, model="gpt-5-mini"):
        super().__init__(model)
        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    def get_response(self, prompt, json_mode=False) -> str:
        self.messages = [{"role": "user", "content": prompt}]
        try:
            kwargs = {
                "model": self.model,
                "messages": self.messages,
            }
            if json_mode:
                # "json_object" requires the model to support it (e.g. gpt-4-1106-preview or later)
                # and usually requires the word "JSON" in the prompt (which we have).
                kwargs["response_format"] = {"type": "json_object"}
                
            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error calling LLM: {e}")
            raise e

    def get_json_response(self, prompt, retries=3):
        for attempt in range(retries):
            try:
                response_text = self.get_response(prompt, json_mode=True)
                json_str = extract_json_block(response_text)
                return json.loads(json_str)
            except json.JSONDecodeError:
                print(f"JSON decode error on attempt {attempt+1}. Retrying...")
                continue
            except Exception as e:
                print(f"Error on attempt {attempt+1}: {e}")
                time.sleep(2)
                continue
        return None