"""Check that your Gemini key and model work, before running real extraction.

    python smoke_llm.py

It sends one tiny request (skipping the cache), asking for JSON with a schema, which is the
exact feature extraction depends on. If this prints a greeting, you're set.
"""
from ai import llm

SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"}, "number": {"type": "integer"}},
    "required": ["greeting", "number"],
}

if __name__ == "__main__":
    print(f"Model: {llm.default_model()}")
    try:
        reply = llm.generate_json(
            "You reply with JSON only.",
            "Give a one-sentence greeting for a payroll implementation team, and the number 7.",
            SCHEMA,
            use_cache=False,
        )
        print("OK:", reply)
    except llm.LLMUnavailable as e:
        print("Can't reach the AI:", e)
    except llm.LLMError as e:
        print("The AI answered, but something is wrong:", e)
