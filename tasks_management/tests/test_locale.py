import gettext
import importlib.resources
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import skipUnless

from django.test import SimpleTestCase

PACKAGE = "tasks_management"
# A message the UI shows as is: untranslated, the user reads the msgid.
MESSAGE_ID = "tasks_management.validation.another_task_pending"
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAS_SETUP = os.path.isfile(os.path.join(SOURCE_ROOT, "setup.py"))


class LocaleShippingTest(SimpleTestCase):
    """The translations must be part of the installed package, not only of the source tree."""

    def test_catalogue_lives_inside_the_package(self):
        # Only the package directory is installed by pip: a locale/ folder next
        # to setup.py stays behind and the module answers with raw msgids.
        catalogue = importlib.resources.files(PACKAGE).joinpath("locale/en/LC_MESSAGES/django.po")
        self.assertTrue(catalogue.is_file(), f"{PACKAGE}/locale/en/LC_MESSAGES/django.po is missing")

    @skipUnless(HAS_SETUP, "needs the module source tree")
    @skipUnless(shutil.which("msgfmt"), "needs GNU gettext (msgfmt)")
    def test_built_package_translates_messages(self):
        with tempfile.TemporaryDirectory() as build_dir:
            subprocess.run(
                [sys.executable, "setup.py", "-q", "build_py", "--build-lib", build_dir],
                cwd=SOURCE_ROOT, check=True, capture_output=True,
            )
            translation = gettext.translation(
                "django", localedir=os.path.join(build_dir, PACKAGE, "locale"), languages=["en"], fallback=True
            )
            self.assertNotEqual(translation.gettext(MESSAGE_ID), MESSAGE_ID)
