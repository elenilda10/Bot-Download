import pytest

from services.links.universal_guard import (
    is_group_download_url,
    select_group_download_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://s.shopee.com.br/5fqDlaRjou",
        "https://www.amazon.com.br/dp/123",
        "https://t.me/NewsChannel/123",
        "https://t.me/s/channel/video.mp4",
        "https://telegram.me/channel",
        "https://youtube.com.evil.example/watch?v=123",
        "https://example.com/video",
        "https://short.example/promo",
    ],
)
def test_group_auto_download_rejects_unrelated_urls(url):
    assert not is_group_download_url(url)
    assert select_group_download_url("Confira " + url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://youtu.be/demo123",
        "https://www.facebook.com/watch?v=123",
        "https://www.threads.net/@user/post/abcd",
        "https://www.reddit.com/r/demo/comments/123/example/",
        "https://pin.it/abc",
        "https://br.pinterest.com/pin/123",
        "https://www.tiktok.com/@user/video/123",
        "https://www.linkedin.com/posts/demo_123",
        "https://www.mediafire.com/file/example/file",
        "https://mega.nz/file/example",
        "https://drive.google.com/file/d/example/view",
        "https://deezer.page.link/abc123",
        "https://cdn.example.org/signed/media.mp4?token=xyz",
        "https://files.example.org/image.jpg",
    ],
)
def test_group_auto_download_keeps_platforms_and_direct_media(url):
    assert is_group_download_url(url)
    assert select_group_download_url("Baixe: " + url) == url


def test_group_auto_download_selects_supported_url_after_shopping_link():
    assert select_group_download_url(
        "Cupom https://s.shopee.com.br/5fqDlaRjou e clipe https://youtu.be/demo123!"
    ) == "https://youtu.be/demo123"


def test_group_auto_download_handles_captions_punctuation_and_no_link():
    assert select_group_download_url(None) is None
    assert select_group_download_url("Texto comum sem links") is None
    assert select_group_download_url(
        "Veja (https://www.instagram.com/p/abc/)."
    ) == "https://www.instagram.com/p/abc/"
