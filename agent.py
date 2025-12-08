import os
import dotenv
import asyncio
from openai import AsyncOpenAI
from agents import Agent, ModelSettings, function_tool, Runner, set_default_openai_client

dotenv.load_dotenv(".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

print(f"Using OpenAI API Key: {OPENAI_API_KEY}, Base URL: {OPENAI_BASE_URL}")
custom_client = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
set_default_openai_client(custom_client)

from agents import set_tracing_disabled

set_tracing_disabled(True)
from agents import set_default_openai_api
set_default_openai_api("chat_completions")

@function_tool
def get_weather(city: str) -> str:
    """returns weather info for the specified city.""" # This docstring will be used as the tool description for agent to decide when to use it.
    return f"The weather in {city} is sunny"

agent = Agent(
    name="Haiku agent",
    instructions="Always respond in haiku form",
    model="gpt-5-nano",
    tools=[get_weather],
    model_settings=ModelSettings(tool_choice="auto") # Allow agent to choose tools automatically
)

async def main():
    result = await Runner.run(agent, "What's the weather like in Tokyo?")
    print(result)
    print("="*40)
    for r in result.new_items:
        print(r)
    print("="*40)
    for r in result.raw_responses:
        print(r)
    print("="*40)
    
if __name__ == "__main__":
    asyncio.run(main())