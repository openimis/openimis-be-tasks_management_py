import glob
import os
import shutil
import subprocess
import warnings

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py

with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))


class BuildPyWithTranslations(build_py):
    """Compile the gettext catalogues into the build, so an installed module ships its translations."""

    def run(self):
        super().run()
        catalogues = glob.glob(os.path.join(self.build_lib, "tasks_management", "locale", "*", "LC_MESSAGES", "*.po"))
        msgfmt = shutil.which("msgfmt")
        if catalogues and not msgfmt:
            warnings.warn("msgfmt (GNU gettext) not found: the module is built without compiled translations")
            return
        for catalogue in catalogues:
            subprocess.run([msgfmt, "--check-format", "-o", catalogue[:-3] + ".mo", catalogue], check=True)


setup(
    name='openimis-be-tasks_management',
    version='1.3.0',
    packages=find_packages(),
    include_package_data=True,
    package_data={"tasks_management": ["locale/*/LC_MESSAGES/*.po", "locale/*/LC_MESSAGES/*.mo"]},
    cmdclass={"build_py": BuildPyWithTranslations},
    license='GNU AGPL v3',
    description='The openIMIS Backend tasks_management reference module.',
    long_description=README,
    long_description_content_type='text/markdown',
    url='https://openimis.org/',
    author='jdolkowski',
    author_email='jdolkowski@gmail.com',
    install_requires=[
        'django',
        'django-db-signals',
        'djangorestframework',
        'openimis-be-core'
    ],
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 3.0',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.8',
    ],
)
