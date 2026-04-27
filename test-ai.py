from openai import OpenAI
from config.settings import OPENAI_API_KEY, OPENAI_MODEL, BASE_URL_AI


client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=BASE_URL_AI
)

try:
    response = client.responses.create(
        model="openai/gpt-4o-mini",  # model via OpenRouter
        input="Halo, ini test koneksi dari OpenRouter. Balas singkat."
    )

    print("✅ Koneksi OpenRouter berhasil!")
    print(response.output[0].content[0].text)

except Exception as e:
    print("❌ Koneksi gagal!")
    print("Error:", str(e))