from setuptools import find_packages, setup

setup(
    name="bot",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "solana",
        "solders",
        "websockets",
        "pytest",
        "black",
        "flake8",
        "isort",
    ],
)
