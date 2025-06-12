import os
import yaml
from tqdm import tqdm
from datasets import Dataset
from typing import Callable

def download_model(model_name: str = None, download_dir: str = "./models/", source: str = "huggingface", use_hf_mirror: bool = False) -> None:
    """
    Download model from ModelScope or HuggingFace and save to specified directory
    Args:
        model_name: Model name (e.g., "Qwen/Qwen2.5-7B-Instruct")
        download_dir: Download directory
        source: Source to download from, either "huggingface" or "modelscope"
        use_hf_mirror: Whether to use hf-mirror mirror (only applicable for huggingface source)
    Returns:
        None
    """
    source_list = ["huggingface", "modelscope"]
    assert source in source_list, f"Parameter 'source' must be one of {source_list}, but got {source}."
    if source != "huggingface" and use_hf_mirror:
        print("use_hf_mirror is only applicable for huggingface source.")
        use_hf_mirror = False

    if use_hf_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    
    if source == "huggingface":
        from huggingface_hub import snapshot_download
        snapshot_download(model_name, local_dir=os.path.join(download_dir, model_name))
    elif source == "modelscope":
        from modelscope import snapshot_download
        snapshot_download(model_name, local_dir=os.path.join(download_dir, model_name))
    

if __name__ == "__main__":
    download_model(model_name="Qwen/Qwen2.5-1.5B-Instruct", download_dir="models/")
    # 示例使用：
    # download_model(model_name="Qwen/Qwen2.5-7B-Instruct", download_dir="models/", use_hf_mirror=True)