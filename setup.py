# setup.py
from setuptools import setup, find_packages

# Read the contents of your README file
from pathlib import Path
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name='envguard',
    version='0.1.0',
    packages=find_packages(),
    include_package_data=True, # Include non-code files specified in MANIFEST.in
    install_requires=[
        'Click==8.2.1',       # For building the command-line interface
        'PyYAML==6.0.2',      # For reading/writing YAML configuration files
        'python-dotenv==1.1.0', # For robust .env file parsing and manipulation
        'rich==14.0.0',       # For beautiful terminal output (tables, colors)
        'cryptography==45.0.4', # For secure encryption (used in snippet sharing)
    ],
    entry_points={
        'console_scripts': [
            'envguard=envguard.cli:cli',
        ],
    },
    author='Rabbil Yasar Sajal',
    author_email='rabbilyasar@gmail.com',
    description='A CLI tool for secure .env management and secret prevention.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/rabbilyasar/envguard',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Security',
        'Topic :: Software Development :: Build Tools',
    ],
    python_requires='>=3.8',
)