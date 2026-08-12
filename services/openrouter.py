import os
import json
import requests

API_URL = "https://openrouter.ai/api/v1/chat/completions"
PREFERRED_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "openrouter/free",
]

GAME_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {"type": "string"},
        "emoji": {"type": "string"},
        "correctAnswer": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
        "highlightedWords": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
    },
    "required": ["story", "emoji", "correctAnswer", "options", "highlightedWords"],
    "additionalProperties": False,
}

WORD_SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "counterExamples": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
    },
    "required": ["explanations", "counterExamples"],
    "additionalProperties": False,
}


def _headers():
    key = os.environ["OPENROUTER_API_KEY"]
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("SITE_URL", "https://localhost"),
        "X-Title": os.environ.get("SITE_NAME", "cinetot"),
    }


def _call(prompt, schema, schema_name):
    last_error = None
    for model in PREFERRED_MODELS:
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            }
            r = requests.post(API_URL, headers=_headers(), json=payload, timeout=30)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"all candidate models failed, last error: {last_error}")


def create_game(language, known_words):
    if known_words and known_words.strip():
        constraint = f'You must write this story using mainly words from this list: "{known_words}". Use basic grammar particles as needed, but stick to the list where possible.'
    else:
        constraint = "Use simple A1/A2 vocabulary."

    prompt = f"""You are a language tutor for a student learning {language}.

TASK 1: Pick a popular, globally known movie (Disney, Pixar, Star Wars, Harry Potter, superhero, etc).
TASK 2: Write a synopsis of this movie in {language}. Under 100 words. Child-like simplicity. Use emojis occasionally. {constraint}
TASK 3: Identify 2-3 words from the story that are NOT in the known words list and are one level above them (i+1 learning), useful for general communication.
TASK 4: Create 4 multiple choice movie title options: one correct, three plausible distractors that share a genre, plot element, or character archetype.

Respond with JSON matching the schema."""
    return _call(prompt, GAME_SCHEMA, "game_round")


def analyze_word(word, context, language):
    prompt = f"""The user is learning {language}. They clicked the word "{word}" in this context: "{context}".

Give 3 simple explanatory definitions of "{word}" in {language}.
Give 2 short descriptions of what "{word}" is NOT (counter-examples or wrong usage) in {language}.
Do not use the word "{word}" or its root variations inside any definition. Use synonyms or descriptive language instead.

Respond with JSON matching the schema."""
    return _call(prompt, WORD_SCHEMA, "word_analysis")
