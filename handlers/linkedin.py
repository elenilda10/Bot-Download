"""Telegram entry point for public LinkedIn posts and lnkd.in links."""

from services.links.linkedin import LINKEDIN_URL

DIRECT_LINK_HINT = {
    "pt": "Não consegui abrir esse link do LinkedIn. Abra a publicação no navegador e envie o endereço completo da barra de endereços (linkedin.com/posts/… ou linkedin.com/feed/update/…).",
    "en": "I couldn't open this LinkedIn link. Open the post in your browser and send the full address from the address bar (linkedin.com/posts/… or linkedin.com/feed/update/…).",
    "es": "No pude abrir este enlace de LinkedIn. Abre la publicación en el navegador y envía la dirección completa de la barra de direcciones (linkedin.com/posts/… o linkedin.com/feed/update/…).",
    "de": "Ich konnte diesen LinkedIn-Link nicht öffnen. Öffne den Beitrag im Browser und sende die vollständige Adresse aus der Adressleiste (linkedin.com/posts/… oder linkedin.com/feed/update/…).",
    "ar": "تعذر فتح رابط LinkedIn. افتح المنشور في المتصفح وأرسل الرابط الكامل من شريط العنوان (linkedin.com/posts/… أو linkedin.com/feed/update/…).",
    "it": "Non riesco ad aprire questo link di LinkedIn. Apri il post nel browser e invia l’indirizzo completo dalla barra degli indirizzi (linkedin.com/posts/… o linkedin.com/feed/update/…).",
}


async def process_linkedin(message, direct_url=None):
    from handlers.universal import process_universal_link
    from handlers.utils import get_message_text

    match = LINKEDIN_URL.search(direct_url or get_message_text(message) or "")
    if match:
        await process_universal_link(message, direct_url=match[0].rstrip(".,;:!?)]}>"))
