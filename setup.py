from setuptools import setup, find_packages

setup(
    name="envguard",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.2",
        "PyYAML>=6.0",
        "pycryptodome>=3.23",
    ],
    entry_points={
        "console_scripts": ["envguard=envguard.cli:cli"],
    },
    python_requires=">=3.7",
)