"""The Demo page uses the shared ISTAR kit look (pi-profiles/ui/UI-SPEC.md) and serves exact kit copies."""

import hashlib

from bridge import demo as demo_mod

KIT_VERSION = 4
KIT_SHA256 = {  # ui-kit v4 (isr-ui.css/js line 1 says v4)
    "isr-ui.css": "59982e401316f8ef3032df561974c542119bbbf606998af97f92fff656ddab1e",
    "isr-ui.js": "109cef714b1f4dfba0159c0f5cab04e4cd2338eea61e41705063e43f4e9ea296",
    "fonts/OFL-BarlowCondensed.txt": "186d750eb496a4c17a76385f82be6aea2ac1cf2de074a811d63786cf374ea73f",
    "fonts/OFL-IBMPlexMono.txt": "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da",
    "fonts/barlow-condensed-500.woff2": "31ff5e6e96a096fde4d78bc838719e69b093db2d0b062b96e423c065a6d1b2b1",
    "fonts/barlow-condensed-600.woff2": "5bc49d41f32810a114765314392332ce2acae2276635eee265a50319a12c1b49",
    "fonts/ibm-plex-mono-400.woff2": "52de70091213c9eab340e2a7ac59e7b6b2b94e089bf69ef38479b22cfde4274c",
    "fonts/ibm-plex-mono-500.woff2": "f17f51948d10b67d907393ccbac551912f2c2bd2c5af19be3aabe2f456f8fe54",
}


def test_page_links_the_kit():
    html = (demo_mod.STATIC_DIR / "index.html").read_text()
    assert f'<link rel="stylesheet" href="/static/isr-ui.css?v={KIT_VERSION}">' in html
    assert f'<script src="/static/isr-ui.js?v={KIT_VERSION}"></script>' in html
    assert html.count('id="isr-status"') == 1 and 'id="isr-kit-link"' in html


def test_kit_copies_are_byte_identical():
    for name, sha in KIT_SHA256.items():
        assert hashlib.sha256((demo_mod.STATIC_DIR / name).read_bytes()).hexdigest() == sha, name
    assert f"v{KIT_VERSION}" in (demo_mod.STATIC_DIR / "isr-ui.css").read_text().splitlines()[0]
