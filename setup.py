#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

setup(
    name="toolmind",
    version="0.1.0",
    author="PJLAB-AI4S",
    description="A scientific integration toolkit focused on dataset management, model downloading, and high-performance asynchronous task processing",
    url="https://github.com/Auraithm/toolmind",
    packages=find_packages(),
    install_requires=[
        "datasets>=3.6.0",
        "pyyaml>=5.1",
        "tqdm>=4.66.3",
        "transformers",
        "modelscope",
        "huggingface_hub>=0.24.0,<0.34.0",  # Stable version range
        "fsspec>=2023.5.0,<2025.0.0",       # Avoid test versions
        "nest_asyncio",
        "numpy>=1.17",
        "pandas",
        "requests>=2.32.2",
        "filelock",
        "packaging>=20.9",
        "typing-extensions>=3.7.4.3",
    ],
    include_package_data=True,
    package_data={
        "toolmind": ["datasets/dataset_name.yaml"],
    },
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
) 