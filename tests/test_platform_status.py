"""Exercise transfer message compatibility and selective VPS updates without bot secrets."""
import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('platform_installer', ROOT / 'scripts/install_platform_status_vps.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def messages(source):
    namespace = {}
    exec(compile(source, 'messages', 'exec'), namespace)
    return namespace


class PlatformStatusTests(unittest.TestCase):
    def test_old_signatures_and_local_customizations_are_preserved(self):
        source = '''import html
DEFAULT_LANG = "pt"
def _normalize_lang(lang):
    return "pt" if lang.startswith("pt") else "en"
def downloading_video_status(lang="pt"):
    return "LOCAL VIDEO " + lang
'''
        result = installer.patch_messages(source)
        namespace = messages(result)
        self.assertEqual(namespace['downloading_video_status']('en'), 'LOCAL VIDEO en')
        self.assertIn('X/Twitter: Baixando…', namespace['downloading_video_status'](platform='Twitter'))
        self.assertEqual(installer.patch_messages(result), result)

    def test_all_platform_calls_are_escaped_and_localized(self):
        namespace = messages((ROOT / 'messages/user_messages.py').read_text())
        for platform in installer.PLATFORMS.values():
            for name in installer.STATUS_NAMES:
                text = namespace[name](lang='pt', platform=platform)
                self.assertIn('5435977112538324435', text)
                self.assertIn('Enviando…' if name.startswith('uploading') else 'Baixando…', text)
        self.assertIn('Uploading…', namespace['uploading_status']('en', platform='Twitter'))
        self.assertIn('&lt;name&gt;', namespace['media_transfer_status']('<name>'))

    def test_selective_call_patch_handles_multiline_unicode_and_trailing_comma(self):
        source = '''a = "ç"; x = bm.downloading_video_status(lang="pt")
y = bm.uploading_status(
    lang="pt",
)
z = other.uploading_status("pt")
'''
        result = installer.patch_calls(source, repr('Instagram'))
        compile(result, 'handler', 'exec')
        self.assertEqual(result.count("platform='Instagram'"), 2)
        self.assertIn('other.uploading_status("pt")', result)
        self.assertEqual(installer.patch_calls(result, repr('Instagram')), result)

    def test_installer_on_deployed_baseline_preserves_unrelated_code(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            fixtures = {
                'messages/user_messages.py': 'import html\nDEFAULT_LANG = "pt"\ndef _normalize_lang(lang):\n    return "pt"\ndef downloading_video_status(lang="pt"):\n    return "LOCAL VIDEO"\n',
                'handlers/twitter.py': 'async def send():\n    status_message = await message.answer(bm.downloading_video_status(lang=user_lang))\n    if media_result:\n        caption = bm.captions(\n            "on", "text", "bot"\n        )\n',
                'handlers/instagram.py': 'status = bm.downloading_video_status(lang="pt")\n',
            }
            for name, source in fixtures.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source + '\n# VPS customization\n')
            changes = installer.prepare(target)
            self.assertIn('handlers/twitter.py', changes)
            for name, source in changes.items():
                self.assertIn('# VPS customization', source)
                (target / name).write_text(source)
            self.assertEqual(installer.prepare(target), {})
            twitter = ast.parse((target / 'handlers/twitter.py').read_text())
            calls = [n for n in ast.walk(twitter) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == 'uploading_status']
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
