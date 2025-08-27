# setup.py
from pathlib import Path

from setuptools import find_packages, setup

# Load the version from the package without importing it
# (to avoid dependency issues during setup).
try:
    with open(Path(__file__).parent / "envshield" / "__init__.py") as f:
        exec(f.read())
    VERSION = __version__
except (NameError, FileNotFoundError):
    VERSION = "1.2.0"  # Fallback version

# Read the contents of your README file for the long description
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding="utf-8")

setup(
    # This is the name of your package on PyPI
    name="envshield",
    version=VERSION,
    packages=find_packages(),
    include_package_data=True,
    # List all external libraries your project relies on.
    install_requires=[
        "PyYAML>=6.0,<7.0",
        "rich>=13.0,<15.0",
        "questionary>=2.0,<3.0",
        "typer[all]>=0.9,<1.0",
    ],
    # This creates the `envshield` command and points it to your CLI application.
    entry_points={
        "console_scripts": [
            "envshield=envshield.cli:app",
        ],
    },
    # --- Package Metadata ---
    author="Rabbil Yasar Sajal",
    author_email="rabbilyasar@gmail.com",
    description="EnvShield: A CLI for secure local environment management and secret prevention.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/rabbilyasar/envshield",  # Update with your new repository URL
    # --- Classifiers for PyPI ---
    # Provide metadata to help users find the package on PyPI.
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Security",
        "Topic :: Software Development :: Build Tools",
        "Topic :: Utilities",
    ],
    python_requires=">=3.8",
)
