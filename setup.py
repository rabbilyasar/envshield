# setup.py
from setuptools import setup, find_packages

# Read the contents of your README file
from pathlib import Path

exec(open(Path(__file__).parent / 'envguard' / '__init__.py').read())
VERSION = __version__ # type: ignore # Mypy ignore: __version__ is defined via exec


this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding='utf-8')


setup(
    name='envguard',
    version=VERSION,
    packages=find_packages(), # Automatically finds all Python packages (directories with __init__.py)
    include_package_data=True, # Include non-Python files (e.g., templates, JSON patterns) from MANIFEST.in or package_data

    # --- Project Dependencies ---
    # List all external libraries your project relies on.
    # Pinning exact versions (e.g., Click==8.2.1) is good practice for reproducibility,
    # but for active development, often a range (e.g., Click>=8.0,<9.0) is used.
    install_requires=[
        'PyYAML==6.0.2',      # For reading/writing YAML configuration files
        'rich==14.0.0',       # For beautiful terminal output (tables, colors)
        'questionary==2.1.0', # For interactive prompts (e.g., for user input)
        'typer==0.16.0',  # For building the CLI
        'setuptools==80.9.0', # For packaging and distribution
        #'Click==8.2.1',       # For building the command-line interface
        #'cryptography==45.0.4', # For secure encryption (used in snippet sharing)
        #'python-dotenv==1.1.0', # For robust .env file parsing and manipulation
    ],
    # --- Console Scripts Entry Point ---
    # This creates an executable script (e.g., `envguard`) in your PATH
    # that points to the `cli` function within `envguard.cli` module.
    entry_points={
        'console_scripts': [
            'envguard=envguard.cli:app',
        ],
    },
    # --- Package Metadata ---
    author='Rabbil Yasar Sajal',
    author_email='rabbilyasar@gmail.com',
    description='EnvGuard: Secure Environment Variable Management and Secret Prevention CLI.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/rabbilyasar/envguard',

    # --- Classifiers for PyPI ---
    # Provide metadata to help users find the package on PyPI.
    classifiers=[
        'Development Status :: 3 - Alpha', # Or 4 - Beta, 5 - Production/Stable
        'Environment :: Console',         # Indicates it's a command-line tool
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License', # Choose your license
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Topic :: Security',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Utilities',
    ],
    python_requires='>=3.8',
)