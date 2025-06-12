#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

setup(
    name="toolmind",
    version="0.1.0",
    author="PJLAB-AI4S",
    description="科研集成工具库，专注于数据集管理和处理",
    url="https://github.com/Auraithm/toolmind",
    packages=find_packages(),
    install_requires=[
        "datasets",
        "pyyaml",
        "tqdm",
        "transformers",
        "modelscope",
        "huggingface_hub",
    ],
    include_package_data=True,
    package_data={
        "toolmind": ["datasets/dataset_name.yaml"],
    },
    python_requires=">=3.8",
) 