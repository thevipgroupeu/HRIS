from pathlib import Path

from django.test import RequestFactory, SimpleTestCase, override_settings

from base.context_processors import white_labelling_company


class TheVipGroupBrandingTests(SimpleTestCase):
    @override_settings(WHITE_LABELLING=False, TVG_BRAND_NAME="THEVIPGROUP")
    def test_default_product_name_is_thevipgroup(self):
        context = white_labelling_company(RequestFactory().get("/"))

        self.assertEqual(context["white_label_company_name"], "THEVIPGROUP")
        self.assertIsNone(context["white_label_company"])

    def test_public_and_private_logo_assets_are_the_supplied_artwork(self):
        root = Path(__file__).resolve().parents[2]
        public_logo = (
            root
            / "tvg_careers"
            / "static"
            / "careerhub"
            / "branding"
            / "thevipgroup-logo.png"
        )
        private_logo = (
            root
            / "horilla_theme"
            / "static"
            / "horilla_theme"
            / "assets"
            / "img"
            / "branding"
            / "thevipgroup-logo.png"
        )

        self.assertTrue(public_logo.is_file())
        self.assertTrue(private_logo.is_file())
        self.assertEqual(public_logo.read_bytes(), private_logo.read_bytes())
        self.assertTrue(public_logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_logo_is_transparent_and_keeps_its_aspect_ratio(self):
        from PIL import Image

        root = Path(__file__).resolve().parents[2]
        logo_path = (
            (root / "horilla_theme" / "static" / "horilla_theme" / "assets" / "img")
            / "branding"
            / "thevipgroup-logo.png"
        )
        with Image.open(logo_path) as image:
            logo = image.convert("RGBA")
        width, height = logo.size
        # The mark is wider than tall; layouts size it by height with
        # object-fit: contain, so any stretched replacement would fail here.
        self.assertAlmostEqual(width / height, 1.40, delta=0.05)
        for corner in (
            (0, 0),
            (width - 1, 0),
            (0, height - 1),
            (width - 1, height - 1),
        ):
            self.assertEqual(logo.getpixel(corner)[3], 0, "logo must have no white box")

    def test_favicons_and_app_icons_are_square_at_their_declared_size(self):
        import json

        from PIL import Image

        root = Path(__file__).resolve().parents[2]
        favicons = root / "static" / "favicons"
        for name, size in (
            ("favicon-16x16.png", 16),
            ("favicon-32x32.png", 32),
            ("apple-touch-icon.png", 180),
            ("android-chrome-192x192.png", 192),
            ("android-chrome-512x512.png", 512),
        ):
            with Image.open(favicons / name) as image:
                self.assertEqual(image.size, (size, size), name)
        with Image.open(favicons / "apple-touch-icon.png") as image:
            # iOS renders transparency as black, so the touch icon is opaque.
            self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3], 255)
        manifest = json.loads((root / "static" / "build" / "manifest.json").read_text())
        for icon in manifest["icons"]:
            path = root / "static" / icon["src"].removeprefix("/static/")
            with Image.open(path) as image:
                width, height = image.size
            self.assertEqual(f"{width}x{height}", icon["sizes"], icon["src"])
