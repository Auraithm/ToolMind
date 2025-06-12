"""
ToolMind - 科研集成工具库

一个用于科研工作的集成工具库，专注于数据集管理和处理。
"""

__version__ = "0.1.0"
__author__ = "PJLAB-AI4S"

from .datasets.utils import download_dataset, load_dataset, apply_function

__all__ = [
    "download_dataset",
    "load_dataset", 
    "apply_function"
] 