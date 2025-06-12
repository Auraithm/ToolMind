"""
ToolMind - 科研集成工具库

一个用于科研工作的集成工具库，专注于数据集管理、模型下载和高性能异步任务处理。
"""

__version__ = "0.1.0"
__author__ = "PJLAB-AI4S"

# 导入子模块
from . import datasets
from . import models
from . import processes

__all__ = [
    "datasets",
    "models", 
    "processes",
] 