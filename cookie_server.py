from aiohttp import web
from pathlib import Path
import logging

SECRET_TOKEN = "meu_token_secreto_bot_123"
COOKIES_FILE = Path("/root/Bot-Download/cookies.txt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type"
}

async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)

async def handle_sync(request):
    try:
        data = await request.json()
        token = data.get("token")
        cookies_content = data.get("cookies", "")

        if token != SECRET_TOKEN:
            return web.Response(status=403, text="Acesso negado: Token inválido", headers=CORS_HEADERS)

        if cookies_content:
            COOKIES_FILE.write_text(cookies_content, encoding="utf-8")
            logging.info(f"cookies.txt atualizado com sucesso ({len(cookies_content)} bytes)")
            return web.Response(text="OK", headers=CORS_HEADERS)
        
        return web.Response(status=400, text="Nenhum dado enviado", headers=CORS_HEADERS)
    except Exception as e:
        logging.error(f"Erro: {e}")
        return web.Response(status=500, text=str(e), headers=CORS_HEADERS)

app = web.Application()
app.router.add_route("OPTIONS", "/sync-cookies", handle_options)
app.router.add_post("/sync-cookies", handle_sync)

if __name__ == "__main__":
    logging.info("Receptor ouvindo na porta 8899...")
    web.run_app(app, host="0.0.0.0", port=8899)
