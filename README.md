# toolmind - 科研集成工具库

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

一个用于科研工作的集成工具库，专注于数据集管理和处理。

## 功能特性

- 🔥 **数据集管理**: 支持多种主流数据集的下载和管理
- 📊 **格式支持**: 支持 parquet, json, csv, jsonl 等多种数据格式
- 🌐 **镜像支持**: 支持使用 HuggingFace 镜像加速下载
- 🛠️ **数据处理**: 提供数据集处理和转换工具


## 安装

```bash
git clone https://github.com/Auraithm/toolmind.git
cd toolmind
pip install .
```

### 开发模式安装
如果您想要参与开发或修改代码：
```bash
git clone https://github.com/Auraithm/toolmind.git
cd toolmind
pip install -e .
```

## 快速开始

### 下载数据集
```python
from toolmind import download_dataset

# 下载 AIME-2024 数据集
download_dataset("AIME-2024", download_dir="./data")

# 使用镜像加速
download_dataset("AIME-2024", download_dir="./data", use_hf_mirror=True)
```

### 添加数据集
修改 toolmind/datasets/dataset_name.yaml进行手动添加。

### 加载数据集
```python
from toolmind import load_dataset

# 加载本地数据集
dataset = load_dataset("./data/GSM8K")
```

## 支持的数据集

当前支持以下数据集：
- **GSM8K**: 数学问题数据集
- **MATH**: 数学竞赛问题数据集
- **AIME-2024**: AIME 2024 数学竞赛数据集
- **AIME-2025**: AIME 2025 数学竞赛数据集
- **AMC**: AMC 数学竞赛数据集
- 更多数据集持续添加中...

## 配置说明

数据集配置文件位于 `toolmind/datasets/dataset_name.yaml`，您可以根据需要添加自定义数据集。

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！


1. 解决数据不足，质量低的问题
2. 利用数据，构建高质量的数据增强，构建评估集和指标，瓶颈