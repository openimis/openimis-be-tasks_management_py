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
CATALOGUE = "locale/en/LC_MESSAGES/django.po"
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAS_SETUP = os.path.isfile(os.path.join(SOURCE_ROOT, "setup.py"))


class LocaleShippingTest(SimpleTestCase):
    """The translations must be part of the installed package, not only of the source tree.

    The module ships the .po files only; the assembly compiles them
    (manage.py compilemessages) once the modules are installed.
    """

    def test_catalogue_lives_inside_the_package(self):
        # Only the package directory is installed by pip: a locale/ folder next
        # to setup.py stays behind and the module answers with raw msgids.
        catalogue = importlib.resources.files(PACKAGE).joinpath(CATALOGUE)
        self.assertTrue(catalogue.is_file(), f"{PACKAGE}/{CATALOGUE} is missing")

    @skipUnless(HAS_SETUP, "needs the module source tree")
    def test_built_package_ships_the_catalogue(self):
        with tempfile.TemporaryDirectory() as build_dir:
            subprocess.run(
                [sys.executable, "setup.py", "-q", "build_py", "--build-lib", build_dir],
                cwd=SOURCE_ROOT, check=True, capture_output=True,
            )
            self.assertTrue(
                os.path.isfile(os.path.join(build_dir, PACKAGE, CATALOGUE)),
                f"the built package has no {PACKAGE}/{CATALOGUE}: check package_data in setup.py",
            )

    @skipUnless(shutil.which("msgfmt"), "needs GNU gettext (msgfmt)")
    def test_catalogue_translates_messages_once_compiled(self):
        catalogue = importlib.resources.files(PACKAGE).joinpath(CATALOGUE)
        with tempfile.TemporaryDirectory() as locale_dir, importlib.resources.as_file(catalogue) as po_file:
            mo_dir = os.path.join(locale_dir, "en", "LC_MESSAGES")
            os.makedirs(mo_dir)
            subprocess.run(["msgfmt", "-o", os.path.join(mo_dir, "django.mo"), str(po_file)], check=True)
            translation = gettext.translation("django", localedir=locale_dir, languages=["en"])
            self.assertNotEqual(translation.gettext(MESSAGE_ID), MESSAGE_ID)
