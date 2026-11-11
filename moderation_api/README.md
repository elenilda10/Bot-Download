# Detector NudeNet opcional

Hospede este serviço separadamente em Render, Cloud Run ou Docker. Configure uma variável NSFW_API_KEY longa e secreta no detector.

No Bot-Download configure NSFW_API_URL=https://seu-servico.example/classify e NSFW_API_KEY com o mesmo segredo. Não use uma API pública sem autenticação.

Após a migração Alembic e o restart do Bot-Download, promova o bot a administrador com permissões para apagar mensagens e banir membros. No grupo use /nsfw on.

Padrão: fotos ON, vídeos ON, GIFs ON, sensibilidade balanced e ban após 3 infrações de alta confiança no intervalo de 30 dias. Cada grupo fica desativado até o administrador usar /nsfw on. Use /nsfw status, /nsfw off, /nsfw fotos off, /nsfw videos off, /nsfw gifs off ou /nsfw sensibilidade relaxed|balanced|strict.

O bot envia somente os bytes das imagens ou até três quadros de vídeo ao detector, sem repassar o link do Telegram que contém o token. Mídias grandes e arquivos não suportados são ignorados; detecção de vídeo é amostragem, não inspeção integral. O detector não mantém arquivos e o bot remove temporários ao fim da inspeção. Classificações podem falhar; administradores devem revisar decisões. O serviço de downloads permanece separado.
